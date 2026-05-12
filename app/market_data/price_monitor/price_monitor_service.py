"""Service for Binance WebSocket 10s price monitoring.

Call chain:
scripts/run_price_monitor_10s.py::main
    -> app/market_data/price_monitor/price_monitor_service.py::run_price_monitor
    -> app/exchange/binance/websocket_market_client.py::BinanceWebSocketMarketClient.connect_and_listen
    -> app/market_data/price_monitor/price_event_parser.py::parse_agg_trade_event
    -> app/market_data/price_monitor/redis_price_state.py::load_previous_price_state
    -> app/market_data/price_monitor/price_change_detector.py::detect_price_change
    -> app/market_data/price_monitor/redis_price_state.py::save_current_price_state
    -> app/alerting/service.py::send_alert

This file belongs to `app/market_data/price_monitor`.
It coordinates Binance public WebSocket, aggTrade parsing, 10s monitor cadence,
Redis `bitcoin_price` state, in-memory alert cooldown, and fixed-template
Hermes alerts. It does not request REST latest prices, write formal Kline
tables, write collector/data-quality records, call DeepSeek, generate advice,
or perform trading.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from typing import Any, Awaitable, Callable

from app.alerting.types import AlertEvent, AlertSendResult, AlertSendStatus, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.core.exceptions import RedisError
from app.core.logger import get_logger
from app.exchange.binance.rest_client import normalize_binance_symbol
from app.exchange.binance.websocket_market_client import (
    BinanceWebSocketMarketClient,
    validate_market_stream_name,
)
from app.market_data.price_monitor.alert_throttle import InMemoryAlertThrottle
from app.market_data.price_monitor.exceptions import (
    PriceEventParseError,
    PriceMonitorError,
    PriceMonitorValidationError,
    PriceStateParseError,
)
from app.market_data.price_monitor.price_change_detector import detect_price_change, parse_decimal_threshold
from app.market_data.price_monitor.price_event_parser import parse_agg_trade_event
from app.market_data.price_monitor.redis_price_state import (
    build_price_state_from_event,
    load_previous_price_state,
    save_current_price_state,
)
from app.market_data.price_monitor.types import (
    ALLOWED_PRICE_MONITOR_TRIGGER_SOURCES,
    EXIT_ALERT_FAILED,
    EXIT_RUNTIME_ERROR,
    EXIT_SUCCESS,
    PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
    PriceChangeResult,
    PriceEvent,
    PriceMonitorConfig,
    PriceMonitorResult,
    PriceMonitorStatus,
)

LOGGER = get_logger("market_data.price_monitor.service")
PARSER_ERROR_ALERT_THRESHOLD = 3
PRICE_CHANGE_ALERT_KIND = "price_change_threshold_exceeded"
NO_RECENT_PRICE_ALERT_KIND = "price_monitor_no_recent_price"
REDIS_ERROR_ALERT_KIND = "price_monitor_redis_error"
PARSER_ERROR_ALERT_KIND = "price_monitor_parser_error"
RUNTIME_ERROR_ALERT_KIND = "price_monitor_runtime_error"

AsyncSleep = Callable[[float], Awaitable[None]]


class PriceMonitorService:
    """Coordinate a persistent WebSocket price monitor.

    Parameters: dependencies are injectable for tests; omitted dependencies are
    created from existing project modules.
    Return value: service instance.
    Failure scenarios: invalid config, WebSocket failures, Redis failures,
    parser failures, and alert failures are returned as explicit results/logs.
    External service access: WebSocket/Redis/Hermes are only accessed through
    injected clients or project service modules.
    Data impact: writes only Redis price state and optional alert records through
    `app/alerting`; never writes formal Kline, collector, or quality tables.
    """

    def __init__(
        self,
        *,
        websocket_client: Any | None = None,
        redis_client: Any | None = None,
        alert_sender: Any | None = None,
        alert_repository: Any | None = None,
        db_session: Any | None = None,
        alert_throttle: InMemoryAlertThrottle | None = None,
        settings: AppSettings | None = None,
        sleep: AsyncSleep | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._websocket_client = websocket_client or BinanceWebSocketMarketClient(settings=self._settings)
        self._redis_client = redis_client
        self._alert_sender = alert_sender or _default_alert_sender()
        self._alert_repository = alert_repository
        self._db_session = db_session
        self._alert_throttle = alert_throttle or InMemoryAlertThrottle(
            self._settings.price_monitor_alert_cooldown_seconds
        )
        self._sleep = sleep or asyncio.sleep
        self._latest_price_event: PriceEvent | None = None
        self._latest_lock = asyncio.Lock()
        self._parser_error_count = 0
        self._started_at_utc = self._now_utc()

    async def handle_raw_ws_message(self, raw_message: str, *, config: PriceMonitorConfig) -> None:
        """Parse a raw WebSocket message and keep only the latest valid event.

        Parameters: `raw_message` is raw Binance aggTrade JSON; `config` supplies
        the expected symbol.
        Return value: none.
        Failure scenarios: parser errors are counted and may trigger a fixed
        template alert after consecutive failures.
        External service access: none except optional alerting on repeated parse
        failures.
        Data impact: no Redis write here; Redis is written only by the 10s loop.
        """

        try:
            price_event = parse_agg_trade_event(raw_message, expected_symbol=config.symbol)
        except PriceEventParseError as exc:
            self._parser_error_count += 1
            LOGGER.warning(
                "Price monitor parser rejected message trace_id=%s count=%s error=%s",
                config.trace_id,
                self._parser_error_count,
                exc,
            )
            if self._parser_error_count >= PARSER_ERROR_ALERT_THRESHOLD:
                self._send_monitor_system_alert(
                    config,
                    alert_kind=PARSER_ERROR_ALERT_KIND,
                    summary="Binance WebSocket aggTrade parser failed repeatedly",
                    error_message=str(exc),
                    severity=AlertSeverity.ERROR,
                )
            return

        self._parser_error_count = 0
        await self.update_latest_price_event(price_event)

    async def update_latest_price_event(self, price_event: PriceEvent) -> None:
        """Store latest valid price event in memory without writing Redis."""

        async with self._latest_lock:
            self._latest_price_event = price_event

    async def get_latest_price_event(self) -> PriceEvent | None:
        """Return the latest in-memory price event."""

        async with self._latest_lock:
            return self._latest_price_event

    async def check_latest_price_every_interval(self, config: PriceMonitorConfig) -> PriceMonitorResult:
        """Run one monitor decision cycle.

        Parameters: `config` contains interval, Redis key, threshold, and alert
        settings.
        Return value: `PriceMonitorResult` for this single cycle.
        Failure scenarios: no recent event, Redis failure, invalid Redis state,
        price detector failure, or alert failure are explicit in the result.
        External service access: reads/writes Redis and may send Hermes.
        Data impact: writes Redis once for a valid latest event; no MySQL Kline,
        no collector/data-quality records, no REST latest price.
        """

        active_config = validate_price_monitor_config(config)
        latest_event = await self.get_latest_price_event()
        if latest_event is None:
            age_seconds = (self._now_utc() - self._started_at_utc).total_seconds()
            if age_seconds <= active_config.no_event_timeout_seconds:
                return PriceMonitorResult(
                    status=PriceMonitorStatus.NO_RECENT_PRICE,
                    exit_code=EXIT_SUCCESS,
                    message="waiting for first valid Binance WebSocket price event",
                    trace_id=active_config.trace_id,
                    redis_written=False,
                    details={
                        "symbol": active_config.symbol,
                        "alert_kind": NO_RECENT_PRICE_ALERT_KIND,
                        "reason": "no valid price event received yet",
                        "age_seconds": age_seconds,
                        "no_event_timeout_seconds": active_config.no_event_timeout_seconds,
                        "source": PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
                    },
                )
            return self._handle_no_recent_price(active_config, "no valid price event received yet")
        age_seconds = (self._now_utc() - latest_event.received_at_utc).total_seconds()
        if age_seconds > active_config.no_event_timeout_seconds:
            return self._handle_no_recent_price(
                active_config,
                f"latest price event is stale: age_seconds={age_seconds:.3f}",
            )

        redis_client = self._get_redis_client()
        invalid_previous_state = ""
        try:
            previous_state = load_previous_price_state(redis_client, key=active_config.redis_key)
        except PriceStateParseError as exc:
            previous_state = None
            invalid_previous_state = str(exc)
            LOGGER.warning("Redis previous price state invalid trace_id=%s error=%s", active_config.trace_id, exc)
        except RedisError as exc:
            return self._handle_monitor_failure(
                active_config,
                alert_kind=REDIS_ERROR_ALERT_KIND,
                message="Redis previous price state read failed",
                error_message=str(exc),
            )

        try:
            change_result = detect_price_change(
                latest_event,
                previous_state,
                threshold=active_config.price_change_threshold,
            )
        except PriceMonitorValidationError as exc:
            return self._handle_monitor_failure(
                active_config,
                alert_kind=RUNTIME_ERROR_ALERT_KIND,
                message="Price change detection failed",
                error_message=str(exc),
            )

        current_state = build_price_state_from_event(latest_event)
        try:
            save_current_price_state(
                redis_client,
                key=active_config.redis_key,
                price_state=current_state,
                ttl_seconds=active_config.redis_ttl_seconds,
            )
        except (RedisError, PriceMonitorValidationError) as exc:
            return self._handle_monitor_failure(
                active_config,
                alert_kind=REDIS_ERROR_ALERT_KIND,
                message="Redis current price state write failed",
                error_message=str(exc),
            )

        details = _base_result_details(active_config, latest_event, change_result)
        details["invalid_previous_state"] = invalid_previous_state
        if invalid_previous_state or not change_result.has_previous:
            return PriceMonitorResult(
                status=PriceMonitorStatus.INITIALIZED,
                exit_code=EXIT_SUCCESS,
                message="current price state saved; previous price unavailable or invalid",
                trace_id=active_config.trace_id,
                redis_written=True,
                details=details,
            )

        if change_result.exceeded and active_config.enable_price_alerts:
            return self._send_price_change_alert_if_allowed(active_config, latest_event, change_result, details)

        if change_result.exceeded and not active_config.enable_price_alerts:
            details["price_alerts_enabled"] = False

        return PriceMonitorResult(
            status=PriceMonitorStatus.UPDATED,
            exit_code=EXIT_SUCCESS,
            message="current price state saved",
            trace_id=active_config.trace_id,
            redis_written=True,
            details=details,
        )

    async def run_monitor_loop(
        self,
        config: PriceMonitorConfig,
        *,
        stop_event: asyncio.Event | None = None,
        max_checks: int | None = None,
    ) -> PriceMonitorResult:
        """Run the 10s monitor loop.

        Parameters: `stop_event` can stop the loop; `max_checks` is used by
        tests and manual smoke checks.
        Return value: last monitor result.
        Failure scenarios: unexpected exceptions are converted to a failed result
        with a fixed-template alert.
        External service access: each cycle may read/write Redis and alert.
        Data impact: no writes to formal Kline, collector, or quality tables.
        """

        active_config = validate_price_monitor_config(config)
        checks_completed = 0
        last_result = PriceMonitorResult(
            status=PriceMonitorStatus.INITIALIZED,
            exit_code=EXIT_SUCCESS,
            message="price monitor loop started",
            trace_id=active_config.trace_id,
        )
        try:
            while stop_event is None or not stop_event.is_set():
                last_result = await self.check_latest_price_every_interval(active_config)
                checks_completed += 1
                if max_checks is not None and checks_completed >= max_checks:
                    return last_result
                await self._sleep(active_config.monitor_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - top-level monitor loop must return a clear failure.
            return self._handle_monitor_failure(
                active_config,
                alert_kind=RUNTIME_ERROR_ALERT_KIND,
                message="Price monitor loop failed",
                error_message=str(exc),
            )
        return last_result

    async def run_price_monitor(
        self,
        config: PriceMonitorConfig,
        *,
        stop_event: asyncio.Event | None = None,
        max_checks: int | None = None,
    ) -> PriceMonitorResult:
        """Start WebSocket listener and 10s monitor loop.

        Parameters: `config` is validated runtime config; optional `stop_event`
        supports graceful shutdown; `max_checks` is for tests/smoke checks.
        Return value: final `PriceMonitorResult` when the process exits.
        Failure scenarios: WebSocket or monitor-loop exceptions become failed
        results and may trigger fixed-template alerts.
        External service access: opens public Binance WebSocket and uses Redis/Hermes.
        Data impact: no formal Kline, collector, or data-quality writes.
        """

        active_config = validate_price_monitor_config(config)
        active_stop_event = stop_event or asyncio.Event()
        listener_task = asyncio.create_task(
            self._websocket_client.connect_and_listen(
                lambda raw: self.handle_raw_ws_message(raw, config=active_config),
                symbol=active_config.symbol,
                stream_name=active_config.ws_stream,
                reconnect_min_seconds=active_config.reconnect_min_seconds,
                reconnect_max_seconds=active_config.reconnect_max_seconds,
                stop_event=active_stop_event,
            )
        )
        monitor_task = asyncio.create_task(
            self.run_monitor_loop(active_config, stop_event=active_stop_event, max_checks=max_checks)
        )

        try:
            if max_checks is not None:
                result = await monitor_task
                active_stop_event.set()
                listener_task.cancel()
                await _await_cancelled(listener_task)
                return result

            done, pending = await asyncio.wait(
                {listener_task, monitor_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                exception = task.exception()
                if exception is not None:
                    active_stop_event.set()
                    for pending_task in pending:
                        pending_task.cancel()
                    return self._handle_monitor_failure(
                        active_config,
                        alert_kind=RUNTIME_ERROR_ALERT_KIND,
                        message="Price monitor process failed",
                        error_message=str(exception),
                    )
            return PriceMonitorResult(
                status=PriceMonitorStatus.UPDATED,
                exit_code=EXIT_SUCCESS,
                message="price monitor stopped",
                trace_id=active_config.trace_id,
            )
        finally:
            active_stop_event.set()

    def _send_price_change_alert_if_allowed(
        self,
        config: PriceMonitorConfig,
        latest_event: PriceEvent,
        change_result: PriceChangeResult,
        details: dict[str, object],
    ) -> PriceMonitorResult:
        if not self._alert_throttle.should_send_alert(
            symbol=config.symbol,
            alert_type=PRICE_CHANGE_ALERT_KIND,
        ):
            return PriceMonitorResult(
                status=PriceMonitorStatus.SUPPRESSED,
                exit_code=EXIT_SUCCESS,
                message="price change alert suppressed by cooldown; Redis state was still updated",
                trace_id=config.trace_id,
                redis_written=True,
                details={**details, "alert_suppressed_by_cooldown": True},
            )

        alert_event = AlertEvent(
            alert_type=AlertType.PRICE_MONITOR_ERROR,
            severity=AlertSeverity.WARNING,
            title="BTCUSDT price movement threshold exceeded",
            summary=f"{config.symbol} price changed {change_result.change_percent:.4f}% over monitor interval",
            details={
                **details,
                "alert_kind": PRICE_CHANGE_ALERT_KIND,
                "event_time_ms": latest_event.event_time_ms,
                "trade_time_ms": latest_event.trade_time_ms,
                "source": PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
                "note": "This is a price event reminder only.",
            },
            source="app.market_data.price_monitor.price_monitor_service",
            trace_id=config.trace_id,
        )
        alert_result = self._send_alert(alert_event)
        result = PriceMonitorResult(
            status=PriceMonitorStatus.ALERTED,
            exit_code=EXIT_SUCCESS,
            message="price movement alert sent",
            trace_id=config.trace_id,
            redis_written=True,
            alert_status=alert_result.status.value,
            details=details,
        )
        if alert_result.status != AlertSendStatus.SENT:
            LOGGER.error(
                "Price movement alert delivery failed trace_id=%s status=%s error=%s",
                config.trace_id,
                alert_result.status.value,
                alert_result.error_message,
            )
            return replace(result, exit_code=EXIT_ALERT_FAILED, message="price movement alert delivery failed")
        return result

    def _handle_no_recent_price(self, config: PriceMonitorConfig, reason: str) -> PriceMonitorResult:
        details = {
            "symbol": config.symbol,
            "alert_kind": NO_RECENT_PRICE_ALERT_KIND,
            "reason": reason,
            "monitor_interval_seconds": config.monitor_interval_seconds,
            "no_event_timeout_seconds": config.no_event_timeout_seconds,
            "source": PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
        }
        alert_status = ""
        exit_code = EXIT_SUCCESS
        if self._alert_throttle.should_send_alert(symbol=config.symbol, alert_type=NO_RECENT_PRICE_ALERT_KIND):
            alert_result = self._send_monitor_system_alert(
                config,
                alert_kind=NO_RECENT_PRICE_ALERT_KIND,
                summary="Price monitor has no recent valid Binance WebSocket price event",
                error_message=reason,
                severity=AlertSeverity.WARNING,
            )
            alert_status = alert_result.status.value
            if alert_result.status != AlertSendStatus.SENT:
                exit_code = EXIT_ALERT_FAILED
        return PriceMonitorResult(
            status=PriceMonitorStatus.NO_RECENT_PRICE,
            exit_code=exit_code,
            message=reason,
            trace_id=config.trace_id,
            redis_written=False,
            alert_status=alert_status,
            details=details,
        )

    def _handle_monitor_failure(
        self,
        config: PriceMonitorConfig,
        *,
        alert_kind: str,
        message: str,
        error_message: str,
    ) -> PriceMonitorResult:
        alert_result = self._send_monitor_system_alert(
            config,
            alert_kind=alert_kind,
            summary=message,
            error_message=error_message,
            severity=AlertSeverity.ERROR,
        )
        exit_code = EXIT_ALERT_FAILED if alert_result.status != AlertSendStatus.SENT else EXIT_RUNTIME_ERROR
        return PriceMonitorResult(
            status=PriceMonitorStatus.FAILED,
            exit_code=exit_code,
            message=message,
            trace_id=config.trace_id,
            redis_written=False,
            alert_status=alert_result.status.value,
            details={
                "symbol": config.symbol,
                "alert_kind": alert_kind,
                "error_message": error_message,
                "source": PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
            },
        )

    def _send_monitor_system_alert(
        self,
        config: PriceMonitorConfig,
        *,
        alert_kind: str,
        summary: str,
        error_message: str,
        severity: AlertSeverity,
    ) -> AlertSendResult:
        event = AlertEvent(
            alert_type=AlertType.PRICE_MONITOR_ERROR,
            severity=severity,
            title="10s WebSocket price monitor issue",
            summary=summary,
            details={
                "alert_kind": alert_kind,
                "symbol": config.symbol,
                "stream": f"{config.symbol.lower()}@{config.ws_stream}",
                "monitor_interval_seconds": config.monitor_interval_seconds,
                "redis_key": config.redis_key,
                "error_message": error_message,
                "source": PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
                "formal_kline_write_performed": False,
            },
            source="app.market_data.price_monitor.price_monitor_service",
            trace_id=config.trace_id,
        )
        return self._send_alert(event)

    def _send_alert(self, event: AlertEvent) -> AlertSendResult:
        alert_repository = self._alert_repository
        if alert_repository is None and self._db_session is not None:
            alert_repository = _default_alert_repository()
        result = self._alert_sender(
            event,
            repository=alert_repository,
            db_session=self._db_session,
            send_real_alert=True,
        )
        if self._db_session is not None and hasattr(self._db_session, "commit"):
            self._db_session.commit()
        return result

    def _get_redis_client(self) -> Any:
        if self._redis_client is not None:
            return self._redis_client
        from app.storage.redis.client import get_redis_client

        self._redis_client = get_redis_client(settings=self._settings)
        return self._redis_client

    @staticmethod
    def _now_utc():
        from app.core.time_utils import now_utc

        return now_utc()


def validate_price_monitor_config(config: PriceMonitorConfig) -> PriceMonitorConfig:
    """Validate and normalize a price monitor config object."""

    symbol = normalize_binance_symbol(config.symbol)
    trigger_source = config.trigger_source.strip()
    if trigger_source not in ALLOWED_PRICE_MONITOR_TRIGGER_SOURCES:
        allowed = ", ".join(sorted(ALLOWED_PRICE_MONITOR_TRIGGER_SOURCES))
        raise PriceMonitorValidationError(f"trigger_source must be one of: {allowed}")
    ws_stream = validate_market_stream_name(config.ws_stream)
    if config.monitor_interval_seconds < 1:
        raise PriceMonitorValidationError("monitor_interval_seconds must be at least 1")
    if config.redis_ttl_seconds < config.monitor_interval_seconds:
        raise PriceMonitorValidationError("redis_ttl_seconds must be greater than or equal to monitor interval")
    if not config.redis_key.strip():
        raise PriceMonitorValidationError("redis_key must not be empty")
    threshold = parse_decimal_threshold(config.price_change_threshold)
    if config.reconnect_min_seconds < 0 or config.reconnect_max_seconds < config.reconnect_min_seconds:
        raise PriceMonitorValidationError("WebSocket reconnect seconds are invalid")
    if config.no_event_timeout_seconds < 1:
        raise PriceMonitorValidationError("no_event_timeout_seconds must be at least 1")
    return replace(
        config,
        symbol=symbol,
        trigger_source=trigger_source,
        ws_stream=ws_stream,
        price_change_threshold=threshold,
        redis_key=config.redis_key.strip(),
    )


def build_price_monitor_config_from_settings(
    settings: AppSettings | None = None,
    *,
    trigger_source: str,
    symbol: str | None = None,
    monitor_interval_seconds: int | None = None,
    price_change_threshold: Decimal | str | None = None,
    redis_key: str | None = None,
    redis_ttl_seconds: int | None = None,
    enable_price_alerts: bool | None = None,
) -> PriceMonitorConfig:
    """Build runtime config from unified settings and CLI overrides."""

    active_settings = settings or get_settings()
    return validate_price_monitor_config(
        PriceMonitorConfig(
            symbol=symbol or active_settings.price_monitor_symbol,
            trigger_source=trigger_source,
            ws_stream=active_settings.price_monitor_ws_stream,
            monitor_interval_seconds=(
                monitor_interval_seconds
                if monitor_interval_seconds is not None
                else active_settings.price_monitor_interval_seconds
            ),
            price_change_threshold=(
                parse_decimal_threshold(price_change_threshold)
                if price_change_threshold is not None
                else parse_decimal_threshold(active_settings.price_monitor_change_threshold)
            ),
            redis_key=redis_key or active_settings.price_monitor_redis_key,
            redis_ttl_seconds=(
                redis_ttl_seconds if redis_ttl_seconds is not None else active_settings.price_monitor_redis_ttl_seconds
            ),
            alert_cooldown_seconds=active_settings.price_monitor_alert_cooldown_seconds,
            enable_price_alerts=(
                enable_price_alerts
                if enable_price_alerts is not None
                else active_settings.price_monitor_enable_price_alerts
            ),
            reconnect_min_seconds=active_settings.price_monitor_ws_reconnect_min_seconds,
            reconnect_max_seconds=active_settings.price_monitor_ws_reconnect_max_seconds,
            no_event_timeout_seconds=active_settings.price_monitor_no_event_timeout_seconds,
        )
    )


def run_price_monitor(
    config: PriceMonitorConfig,
    *,
    websocket_client: Any | None = None,
    redis_client: Any | None = None,
    alert_sender: Any | None = None,
    alert_repository: Any | None = None,
    db_session: Any | None = None,
    settings: AppSettings | None = None,
    max_checks: int | None = None,
) -> PriceMonitorResult:
    """Synchronous wrapper used by CLI entry points."""

    service = PriceMonitorService(
        websocket_client=websocket_client,
        redis_client=redis_client,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        db_session=db_session,
        settings=settings,
    )
    try:
        return asyncio.run(service.run_price_monitor(config, max_checks=max_checks))
    except KeyboardInterrupt:
        return PriceMonitorResult(
            status=PriceMonitorStatus.UPDATED,
            exit_code=EXIT_SUCCESS,
            message="price monitor stopped by user",
            trace_id=config.trace_id,
        )
    except PriceMonitorValidationError as exc:
        return PriceMonitorResult(
            status=PriceMonitorStatus.FAILED,
            exit_code=EXIT_RUNTIME_ERROR,
            message=str(exc),
            trace_id=config.trace_id,
        )


def _base_result_details(
    config: PriceMonitorConfig,
    latest_event: PriceEvent,
    change_result: PriceChangeResult,
) -> dict[str, object]:
    return {
        "symbol": config.symbol,
        "stream": f"{config.symbol.lower()}@{config.ws_stream}",
        "redis_key": config.redis_key,
        "redis_ttl_seconds": config.redis_ttl_seconds,
        "monitor_interval_seconds": config.monitor_interval_seconds,
        "threshold": str(change_result.threshold),
        "previous_price": str(change_result.previous_price) if change_result.previous_price is not None else "",
        "current_price": str(change_result.current_price),
        "change_percent": str(change_result.change_percent),
        "direction": change_result.direction,
        "event_time_ms": latest_event.event_time_ms,
        "trade_time_ms": latest_event.trade_time_ms,
        "source": PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
    }


async def _await_cancelled(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return


def _default_alert_sender() -> Any:
    from app.alerting.service import send_alert

    return send_alert


def _default_alert_repository() -> Any:
    from app.storage.mysql.repositories.alert_message_repository import AlertMessageRepository

    return AlertMessageRepository()
