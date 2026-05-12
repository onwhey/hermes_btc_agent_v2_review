from __future__ import annotations

import asyncio
import inspect
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.exceptions import RedisError
from app.core.time_utils import now_utc, utc_aware_to_prc_aware
from app.exchange.binance.websocket_market_client import (
    BinanceWebSocketMarketClient,
    build_market_stream_name,
    build_market_ws_url,
)
from app.market_data.price_monitor.alert_throttle import InMemoryAlertThrottle
from app.market_data.price_monitor.exceptions import PriceEventParseError, PriceStateParseError
from app.market_data.price_monitor.price_change_detector import detect_price_change
from app.market_data.price_monitor.price_event_parser import parse_agg_trade_event
from app.market_data.price_monitor.price_monitor_service import PriceMonitorService
from app.market_data.price_monitor.redis_price_state import (
    build_price_state_from_event,
    load_previous_price_state,
    save_current_price_state,
    serialize_price_state,
)
from app.market_data.price_monitor.types import (
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
    PriceEvent,
    PriceMonitorConfig,
    PriceMonitorStatus,
    PriceState,
)
from scripts import run_price_monitor_10s


class FakeRedis:
    def __init__(self, initial: dict[str, str] | None = None, *, fail_get: bool = False, fail_set: bool = False) -> None:
        self.store = dict(initial or {})
        self.fail_get = fail_get
        self.fail_set = fail_set
        self.set_calls: list[dict[str, Any]] = []

    def get(self, key: str) -> str | None:
        if self.fail_get:
            raise RuntimeError("redis get failed")
        return self.store.get(key)

    def set(self, key: str, value: str, *, ex: int) -> None:
        if self.fail_set:
            raise RuntimeError("redis set failed")
        self.store[key] = value
        self.set_calls.append({"key": key, "value": value, "ex": ex})


class FakeAlertSender:
    def __init__(self, result: AlertSendResult | None = None) -> None:
        self.result = result or AlertSendResult(status=AlertSendStatus.SENT)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, event: Any, **kwargs: Any) -> AlertSendResult:
        self.calls.append({"event": event, "kwargs": kwargs})
        return self.result


def build_event(price: str = "100", *, received_age_seconds: int = 0) -> PriceEvent:
    received = now_utc() - timedelta(seconds=received_age_seconds)
    return PriceEvent(
        symbol="BTCUSDT",
        price=Decimal(price),
        event_time_ms=1_710_000_000_000,
        trade_time_ms=1_710_000_000_001,
        received_at_utc=received,
    )


def build_state(price: str = "100") -> PriceState:
    saved_at_utc = now_utc()
    return PriceState(
        symbol="BTCUSDT",
        price=Decimal(price),
        event_time_ms=1_710_000_000_000,
        trade_time_ms=1_710_000_000_001,
        saved_at_utc=saved_at_utc,
        saved_at_prc=utc_aware_to_prc_aware(saved_at_utc),
        source=PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
    )


def monitor_config(**overrides: Any) -> PriceMonitorConfig:
    data = {
        "symbol": "BTCUSDT",
        "trigger_source": "cli",
        "monitor_interval_seconds": 10,
        "price_change_threshold": Decimal("0.01"),
        "redis_key": "bitcoin_price",
        "redis_ttl_seconds": 120,
        "alert_cooldown_seconds": 60,
        "enable_price_alerts": True,
    }
    data.update(overrides)
    return PriceMonitorConfig(**data)


def test_cli_missing_trigger_source_rejects() -> None:
    assert run_price_monitor_10s.main([]) == EXIT_PARAMETER_ERROR


def test_cli_illegal_trigger_source_rejects() -> None:
    assert run_price_monitor_10s.main(["--trigger-source", "scheduler"]) == EXIT_PARAMETER_ERROR


def test_cli_invalid_interval_or_ttl_rejects() -> None:
    assert (
        run_price_monitor_10s.main(
            ["--trigger-source", "cli", "--monitor-interval-seconds", "0"]
        )
        == EXIT_PARAMETER_ERROR
    )
    assert (
        run_price_monitor_10s.main(
            ["--trigger-source", "cli", "--monitor-interval-seconds", "10", "--redis-ttl-seconds", "5"]
        )
        == EXIT_PARAMETER_ERROR
    )


def test_websocket_url_builds_btcusdt_agg_trade_market_stream() -> None:
    assert build_market_stream_name("BTCUSDT") == "btcusdt@aggTrade"
    assert (
        build_market_ws_url(symbol="BTCUSDT", base_url="wss://fstream.binance.com/market/ws")
        == "wss://fstream.binance.com/market/ws/btcusdt@aggTrade"
    )


def test_parser_parses_valid_agg_trade() -> None:
    event = parse_agg_trade_event(
        '{"e":"aggTrade","E":1710000000000,"s":"BTCUSDT","p":"65000.12","T":1710000000001}',
        expected_symbol="BTCUSDT",
    )

    assert event.symbol == "BTCUSDT"
    assert event.price == Decimal("65000.12")
    assert event.event_time_ms == 1_710_000_000_000
    assert event.trade_time_ms == 1_710_000_000_001
    assert event.source == PRICE_SOURCE_BINANCE_WS_AGG_TRADE


def test_parser_rejects_missing_price_invalid_price_and_symbol_mismatch() -> None:
    with pytest.raises(PriceEventParseError):
        parse_agg_trade_event('{"e":"aggTrade","E":1,"s":"BTCUSDT","T":2}', expected_symbol="BTCUSDT")
    with pytest.raises(PriceEventParseError):
        parse_agg_trade_event('{"e":"aggTrade","E":1,"s":"BTCUSDT","p":"bad","T":2}', expected_symbol="BTCUSDT")
    with pytest.raises(PriceEventParseError):
        parse_agg_trade_event('{"e":"aggTrade","E":1,"s":"ETHUSDT","p":"1","T":2}', expected_symbol="BTCUSDT")


def test_price_detector_identifies_up_and_down_moves_over_one_percent() -> None:
    up = detect_price_change(build_event("102"), build_state("100"), threshold=Decimal("0.01"))
    down = detect_price_change(build_event("98"), build_state("100"), threshold=Decimal("0.01"))

    assert up.exceeded is True
    assert up.direction == "up"
    assert up.change_percent == Decimal("2.00")
    assert down.exceeded is True
    assert down.direction == "down"
    assert down.change_percent == Decimal("2.00")


def test_price_detector_previous_missing_does_not_alert() -> None:
    result = detect_price_change(build_event("102"), None, threshold=Decimal("0.01"))

    assert result.has_previous is False
    assert result.exceeded is False
    assert result.reason == "previous_price_missing"


def test_redis_state_write_sets_ttl_and_read_errors_are_explicit() -> None:
    redis = FakeRedis()
    state = build_state("100")

    save_current_price_state(redis, key="bitcoin_price", price_state=state, ttl_seconds=120)

    assert redis.set_calls[0]["key"] == "bitcoin_price"
    assert redis.set_calls[0]["ex"] == 120
    loaded = load_previous_price_state(redis, key="bitcoin_price")
    assert loaded is not None
    assert loaded.price == Decimal("100")

    broken = FakeRedis({"bitcoin_price": "{bad json"})
    with pytest.raises(PriceStateParseError):
        load_previous_price_state(broken, key="bitcoin_price")

    fail_get = FakeRedis(fail_get=True)
    with pytest.raises(RedisError):
        load_previous_price_state(fail_get, key="bitcoin_price")


def test_alert_throttle_blocks_repeated_alerts_inside_cooldown() -> None:
    throttle = InMemoryAlertThrottle(cooldown_seconds=60)
    now = now_utc()

    assert throttle.should_send_alert(symbol="BTCUSDT", alert_type="price", now=now) is True
    assert throttle.should_send_alert(symbol="BTCUSDT", alert_type="price", now=now + timedelta(seconds=30)) is False
    assert throttle.should_send_alert(symbol="BTCUSDT", alert_type="price", now=now + timedelta(seconds=61)) is True


def test_service_writes_redis_and_alerts_when_threshold_exceeded() -> None:
    async def scenario() -> None:
        redis = FakeRedis({"bitcoin_price": serialize_price_state(build_state("100"))})
        alert_sender = FakeAlertSender()
        service = PriceMonitorService(redis_client=redis, alert_sender=alert_sender)
        await service.update_latest_price_event(build_event("102"))

        result = await service.check_latest_price_every_interval(monitor_config())

        assert result.status == PriceMonitorStatus.ALERTED
        assert result.exit_code == EXIT_SUCCESS
        assert redis.set_calls[0]["ex"] == 120
        assert len(alert_sender.calls) == 1
        assert alert_sender.calls[0]["event"].alert_type.value == "price_monitor_error"

    asyncio.run(scenario())


def test_service_price_change_alert_is_cooled_down_while_redis_updates() -> None:
    async def scenario() -> None:
        redis = FakeRedis({"bitcoin_price": serialize_price_state(build_state("100"))})
        alert_sender = FakeAlertSender()
        service = PriceMonitorService(redis_client=redis, alert_sender=alert_sender)
        await service.update_latest_price_event(build_event("102"))

        first = await service.check_latest_price_every_interval(monitor_config())
        await service.update_latest_price_event(build_event("104"))
        second = await service.check_latest_price_every_interval(monitor_config())

        assert first.status == PriceMonitorStatus.ALERTED
        assert second.status == PriceMonitorStatus.SUPPRESSED
        assert second.details["alert_suppressed_by_cooldown"] is True
        assert len(alert_sender.calls) == 1
        assert len(redis.set_calls) == 2

    asyncio.run(scenario())


def test_service_does_not_alert_when_threshold_not_exceeded() -> None:
    async def scenario() -> None:
        redis = FakeRedis({"bitcoin_price": serialize_price_state(build_state("100"))})
        alert_sender = FakeAlertSender()
        service = PriceMonitorService(redis_client=redis, alert_sender=alert_sender)
        await service.update_latest_price_event(build_event("100.5"))

        result = await service.check_latest_price_every_interval(monitor_config())

        assert result.status == PriceMonitorStatus.UPDATED
        assert redis.set_calls[0]["ex"] == 120
        assert alert_sender.calls == []

    asyncio.run(scenario())


def test_service_previous_missing_initializes_without_alert() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        alert_sender = FakeAlertSender()
        service = PriceMonitorService(redis_client=redis, alert_sender=alert_sender)
        await service.update_latest_price_event(build_event("100"))

        result = await service.check_latest_price_every_interval(monitor_config())

        assert result.status == PriceMonitorStatus.INITIALIZED
        assert result.redis_written is True
        assert alert_sender.calls == []

    asyncio.run(scenario())


def test_service_redis_get_failures_are_cooled_down() -> None:
    async def scenario() -> None:
        redis = FakeRedis(fail_get=True)
        alert_sender = FakeAlertSender()
        service = PriceMonitorService(redis_client=redis, alert_sender=alert_sender)
        await service.update_latest_price_event(build_event("100"))

        first = await service.check_latest_price_every_interval(monitor_config(enable_price_alerts=False))
        second = await service.check_latest_price_every_interval(monitor_config(enable_price_alerts=False))

        assert first.status == PriceMonitorStatus.FAILED
        assert second.status == PriceMonitorStatus.FAILED
        assert second.details["alert_suppressed_by_cooldown"] is True
        assert second.alert_status == AlertSendStatus.SKIPPED.value
        assert len(alert_sender.calls) == 1

    asyncio.run(scenario())


def test_service_redis_set_failures_are_cooled_down() -> None:
    async def scenario() -> None:
        redis = FakeRedis({"bitcoin_price": serialize_price_state(build_state("100"))}, fail_set=True)
        alert_sender = FakeAlertSender()
        service = PriceMonitorService(redis_client=redis, alert_sender=alert_sender)
        await service.update_latest_price_event(build_event("100.5"))

        first = await service.check_latest_price_every_interval(monitor_config())
        second = await service.check_latest_price_every_interval(monitor_config())

        assert first.status == PriceMonitorStatus.FAILED
        assert second.status == PriceMonitorStatus.FAILED
        assert second.details["alert_suppressed_by_cooldown"] is True
        assert second.alert_status == AlertSendStatus.SKIPPED.value
        assert len(alert_sender.calls) == 1

    asyncio.run(scenario())


def test_service_parser_repeated_failures_are_cooled_down() -> None:
    async def scenario() -> None:
        alert_sender = FakeAlertSender()
        service = PriceMonitorService(redis_client=FakeRedis(), alert_sender=alert_sender)
        bad_message = '{"e":"aggTrade","E":1,"s":"BTCUSDT","T":2}'

        for _ in range(4):
            await service.handle_raw_ws_message(bad_message, config=monitor_config())

        assert len(alert_sender.calls) == 1
        assert alert_sender.calls[0]["event"].details["alert_kind"] == "price_monitor_parser_error"

    asyncio.run(scenario())


def test_service_monitor_loop_uses_configured_interval() -> None:
    async def scenario() -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        redis = FakeRedis()
        service = PriceMonitorService(redis_client=redis, alert_sender=FakeAlertSender(), sleep=fake_sleep)
        await service.update_latest_price_event(build_event("100"))

        result = await service.run_monitor_loop(monitor_config(), max_checks=2)

        assert result.exit_code == EXIT_SUCCESS
        assert sleeps == [10]
        assert len(redis.set_calls) == 2

    asyncio.run(scenario())


def test_service_no_latest_price_generates_exception_status() -> None:
    async def scenario() -> None:
        alert_sender = FakeAlertSender()
        service = PriceMonitorService(redis_client=FakeRedis(), alert_sender=alert_sender)
        service._started_at_utc = now_utc() - timedelta(seconds=31)

        result = await service.check_latest_price_every_interval(monitor_config(no_event_timeout_seconds=30))

        assert result.status == PriceMonitorStatus.NO_RECENT_PRICE
        assert result.redis_written is False
        assert len(alert_sender.calls) == 1

    asyncio.run(scenario())


def test_service_no_latest_price_alert_is_cooled_down() -> None:
    async def scenario() -> None:
        alert_sender = FakeAlertSender()
        service = PriceMonitorService(redis_client=FakeRedis(), alert_sender=alert_sender)
        service._started_at_utc = now_utc() - timedelta(seconds=31)

        first = await service.check_latest_price_every_interval(monitor_config(no_event_timeout_seconds=30))
        second = await service.check_latest_price_every_interval(monitor_config(no_event_timeout_seconds=30))

        assert first.status == PriceMonitorStatus.NO_RECENT_PRICE
        assert second.status == PriceMonitorStatus.NO_RECENT_PRICE
        assert second.details["alert_suppressed_by_cooldown"] is True
        assert second.alert_status == AlertSendStatus.SKIPPED.value
        assert len(alert_sender.calls) == 1

    asyncio.run(scenario())


def test_websocket_client_reconnect_can_be_mocked() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        messages: list[str] = []

        class FakeWebSocketContext:
            def __init__(self, *, fail_enter: bool) -> None:
                self.fail_enter = fail_enter
                self.sent = False

            async def __aenter__(self) -> "FakeWebSocketContext":
                if self.fail_enter:
                    raise RuntimeError("disconnect")
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            def __aiter__(self) -> "FakeWebSocketContext":
                return self

            async def __anext__(self) -> str:
                if self.sent:
                    raise StopAsyncIteration
                self.sent = True
                return '{"e":"aggTrade","E":1,"s":"BTCUSDT","p":"1","T":2}'

        def fake_connect(url: str) -> FakeWebSocketContext:
            calls.append(url)
            return FakeWebSocketContext(fail_enter=len(calls) == 1)

        client = BinanceWebSocketMarketClient(connect_factory=fake_connect)

        await client.connect_and_listen(
            lambda raw: messages.append(raw),
            symbol="BTCUSDT",
            reconnect_min_seconds=0,
            reconnect_max_seconds=0,
            max_messages=1,
        )

        assert len(calls) == 2
        assert calls[0].endswith("/btcusdt@aggTrade")
        assert len(messages) == 1

    asyncio.run(scenario())


def test_price_monitor_sources_do_not_use_forbidden_capabilities() -> None:
    from app.exchange.binance import websocket_market_client
    from app.market_data.price_monitor import price_monitor_service

    source = (
        inspect.getsource(websocket_market_client)
        + inspect.getsource(price_monitor_service)
        + Path("scripts/run_price_monitor_10s.py").read_text(encoding="utf-8")
    )
    forbidden_terms = [
        "--send" "-alert",
        "/fapi/v1/" "ticker",
        "/fapi/v2/" "ticker",
        "get_" "account",
        "get_" "position",
        "create_" "order",
        "listen" "Key",
        "market_kline_4h",
        "collector_event_log",
        "data_quality_check",
        "deepseek_client",
    ]

    for term in forbidden_terms:
        assert term not in source
