from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.alerting.types import AlertType
from app.core.config import AppSettings
from app.core.exceptions import RedisError
from app.market_data.collector.types import (
    EXIT_SUCCESS as COLLECT_EXIT_SUCCESS,
    IncrementalKlineCollectRequest,
    IncrementalKlineCollectResult,
    KlineCollectStatus,
)
from app.market_data.kline_constants import TRIGGER_SOURCE_SCHEDULER
from app.market_data.kline_integrity.types import (
    CHECK_MODE_DAILY_INTEGRITY_CHECK,
    DailyKlineIntegrityCheckRequest,
    DailyKlineIntegrityCheckResult,
    DailyKlineIntegrityStatus,
)
from app.market_data.kline_quality.types import CHECK_TRIGGER_SOURCE_SCHEDULER
from app.scheduler.config import SchedulerRuntimeConfig, build_scheduler_runtime_config
from app.scheduler.execution_slot import (
    DAILY_KLINE_INTEGRITY_JOB_NAME,
    KLINE_4H_INCREMENTAL_JOB_NAME,
)
from app.scheduler.jobs.daily_kline_integrity_check import run_daily_kline_integrity_check_job
from app.scheduler.jobs.kline_4h_incremental_collect import run_kline_4h_incremental_collect_job
from app.scheduler.runner import SchedulerRunner


class FakeSlotStore:
    def __init__(self, *, reserved: bool = True, fail: bool = False) -> None:
        self.reserved = reserved
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def reserve_execution_slot(self, *, key: str, owner: str, ttl_seconds: int) -> bool:
        self.calls.append({"key": key, "owner": owner, "ttl_seconds": ttl_seconds})
        if self.fail:
            raise RedisError("execution slot unavailable")
        return self.reserved


class FakeAlertSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, event: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"event": event, "kwargs": kwargs})
        return SimpleNamespace(status="sent")


def runtime_config(**overrides: Any) -> SchedulerRuntimeConfig:
    data = {
        "enabled": True,
        "poll_interval_seconds": 30,
        "job_slot_ttl_seconds": 90000,
        "kline_4h_incremental_collect_enabled": True,
        "kline_4h_incremental_collect_symbol": "BTCUSDT",
        "kline_4h_incremental_collect_interval": "4h",
        "kline_4h_incremental_collect_limit": 6,
        "kline_4h_incremental_collect_utc_minutes_after_close": 5,
        "daily_kline_integrity_enabled": True,
        "daily_kline_integrity_symbol": "BTCUSDT",
        "daily_kline_integrity_interval": "4h",
        "daily_kline_integrity_limit": 100,
        "daily_kline_integrity_utc_time": time(hour=0, minute=30),
    }
    data.update(overrides)
    return SchedulerRuntimeConfig(**data)


def utc_at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 5, 13, hour, minute, second, tzinfo=timezone.utc)


def read_files(paths: list[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_scheduler_runtime_config_loads_from_settings() -> None:
    settings = AppSettings(
        scheduler_enabled=False,
        scheduler_poll_interval_seconds=45,
        scheduler_job_slot_ttl_seconds=12345,
        kline_4h_incremental_collect_enabled=False,
        kline_4h_incremental_collect_symbol="btcusdt",
        kline_4h_incremental_collect_interval="4h",
        kline_4h_incremental_collect_limit=7,
        kline_4h_incremental_collect_utc_minutes_after_close=6,
        daily_kline_integrity_enabled=False,
        daily_kline_integrity_symbol="btcusdt",
        daily_kline_integrity_interval="4h",
        daily_kline_integrity_limit=101,
        daily_kline_integrity_utc_time="01:15",
    )

    config = build_scheduler_runtime_config(settings)

    assert config.enabled is False
    assert config.poll_interval_seconds == 45
    assert config.job_slot_ttl_seconds == 12345
    assert config.kline_4h_incremental_collect_enabled is False
    assert config.kline_4h_incremental_collect_symbol == "BTCUSDT"
    assert config.kline_4h_incremental_collect_limit == 7
    assert config.daily_kline_integrity_enabled is False
    assert config.daily_kline_integrity_utc_time == time(hour=1, minute=15)


def test_scheduler_disabled_does_not_run_09_or_11() -> None:
    slot_store = FakeSlotStore()
    alert_sender = FakeAlertSender()

    def fail_job() -> None:
        raise AssertionError("disabled scheduler must not run jobs")

    runner = SchedulerRunner(
        config=runtime_config(enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=fail_job,
        daily_integrity_job=fail_job,
        alert_sender=alert_sender,
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5))

    assert records[0].status == "disabled"
    assert slot_store.calls == []
    assert alert_sender.calls == []


def test_09_enabled_runner_calls_09_job_after_slot_reservation() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore()
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=lambda: calls.append("09"),
        daily_integrity_job=lambda: calls.append("11"),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5, 10))

    assert calls == ["09"]
    assert records[0].job_name == KLINE_4H_INCREMENTAL_JOB_NAME
    assert records[0].status == "executed"
    assert slot_store.calls[0]["key"] == "scheduler:job:kline_4h_incremental:2026-05-13T04:05Z"


def test_09_disabled_runner_does_not_run_09_job() -> None:
    calls: list[str] = []
    runner = SchedulerRunner(
        config=runtime_config(
            kline_4h_incremental_collect_enabled=False,
            daily_kline_integrity_enabled=False,
        ),
        slot_store=FakeSlotStore(),
        settings=AppSettings(),
        kline_4h_job=lambda: calls.append("09"),
        daily_integrity_job=lambda: calls.append("11"),
        alert_sender=FakeAlertSender(),
    )

    assert runner.run_once(current_time_utc=utc_at(4, 5)) == []
    assert calls == []


def test_11_enabled_runner_calls_11_job_after_slot_reservation() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore()
    runner = SchedulerRunner(
        config=runtime_config(kline_4h_incremental_collect_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=lambda: calls.append("09"),
        daily_integrity_job=lambda: calls.append("11"),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(0, 30, 5))

    assert calls == ["11"]
    assert records[0].job_name == DAILY_KLINE_INTEGRITY_JOB_NAME
    assert records[0].status == "executed"
    assert slot_store.calls[0]["key"] == "scheduler:job:daily_kline_integrity:2026-05-13"


def test_11_disabled_runner_does_not_run_11_job() -> None:
    calls: list[str] = []
    runner = SchedulerRunner(
        config=runtime_config(
            kline_4h_incremental_collect_enabled=False,
            daily_kline_integrity_enabled=False,
        ),
        slot_store=FakeSlotStore(),
        settings=AppSettings(),
        kline_4h_job=lambda: calls.append("09"),
        daily_integrity_job=lambda: calls.append("11"),
        alert_sender=FakeAlertSender(),
    )

    assert runner.run_once(current_time_utc=utc_at(0, 30)) == []
    assert calls == []


def test_09_scheduler_job_passes_scheduler_trigger_to_app_service() -> None:
    called: dict[str, Any] = {}
    expected_result = IncrementalKlineCollectResult(
        status=KlineCollectStatus.SUCCESS,
        exit_code=COLLECT_EXIT_SUCCESS,
        trace_id="trace",
        message="ok",
    )

    def fake_service(request: IncrementalKlineCollectRequest, **kwargs: Any) -> Any:
        called["request"] = request
        called["kwargs"] = kwargs
        return expected_result

    db_session = object()
    result = run_kline_4h_incremental_collect_job(
        db_session=db_session,
        settings=AppSettings(),
        service_runner=fake_service,
    )

    assert result is expected_result
    assert called["request"].trigger_source == TRIGGER_SOURCE_SCHEDULER
    assert called["request"].confirm_write is True
    assert called["request"].dry_run is False
    assert called["kwargs"]["db_session"] is db_session


def test_11_scheduler_job_passes_scheduler_trigger_and_daily_mode_to_app_service() -> None:
    called: dict[str, Any] = {}
    expected_result = DailyKlineIntegrityCheckResult(
        status=DailyKlineIntegrityStatus.HEALTHY,
        exit_code=0,
        trace_id="trace",
        message="ok",
    )

    def fake_service(request: DailyKlineIntegrityCheckRequest, **kwargs: Any) -> Any:
        called["request"] = request
        called["kwargs"] = kwargs
        return expected_result

    db_session = object()
    result = run_daily_kline_integrity_check_job(
        db_session=db_session,
        settings=AppSettings(),
        service_runner=fake_service,
    )

    assert result is expected_result
    assert called["request"].check_trigger == CHECK_TRIGGER_SOURCE_SCHEDULER
    assert called["request"].check_mode == CHECK_MODE_DAILY_INTEGRITY_CHECK
    assert called["kwargs"]["db_session"] is db_session


def test_redis_execution_slot_existing_skips_without_running_job() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore(reserved=False)
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=lambda: calls.append("09"),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5))

    assert records[0].status == "skipped"
    assert calls == []
    assert len(slot_store.calls) == 1


def test_redis_execution_slot_failure_blocks_job_and_sends_system_alert() -> None:
    calls: list[str] = []
    alert_sender = FakeAlertSender()
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=FakeSlotStore(fail=True),
        settings=AppSettings(),
        kline_4h_job=lambda: calls.append("09"),
        alert_sender=alert_sender,
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5))

    assert records[0].status == "error"
    assert records[0].details["slot_error"] is True
    assert calls == []
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    assert event.alert_type == AlertType.SYSTEM_ERROR
    assert event.details["scheduler_job"] == KLINE_4H_INCREMENTAL_JOB_NAME
    assert event.details["no_auto_repair"] is True
    assert event.details["no_auto_backfill"] is True
    assert event.details["no_trading"] is True
    assert alert_sender.calls[0]["kwargs"]["send_real_alert"] is True


def test_scheduler_job_wrapper_exception_sends_system_alert() -> None:
    alert_sender = FakeAlertSender()

    def failing_job() -> None:
        raise RuntimeError("wrapper failed")

    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=FakeSlotStore(),
        settings=AppSettings(),
        kline_4h_job=failing_job,
        alert_sender=alert_sender,
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5))

    assert records[0].status == "error"
    assert records[0].details["job_wrapper_error"] is True
    assert len(alert_sender.calls) == 1
    assert alert_sender.calls[0]["event"].alert_type == AlertType.SYSTEM_ERROR


def test_scheduler_sources_do_not_call_scripts_or_start_price_monitor() -> None:
    scheduler_source = read_files(sorted(Path("app/scheduler").rglob("*.py")))
    run_scheduler_source = Path("scripts/run_scheduler.py").read_text(encoding="utf-8")

    forbidden_scheduler_terms = [
        "scripts.collect_4h_klines",
        "scripts.check_kline_integrity",
        "scripts.run_price_monitor_10s",
        "python -m scripts",
        "subprocess",
        "runpy",
    ]
    for term in forbidden_scheduler_terms:
        assert term not in scheduler_source

    assert "subprocess" not in run_scheduler_source
    assert "runpy" not in run_scheduler_source
    assert "scripts.collect_4h_klines" not in run_scheduler_source
    assert "scripts.check_kline_integrity" not in run_scheduler_source
    assert "scripts.run_price_monitor_10s" not in run_scheduler_source


def test_systemd_examples_are_secret_free_and_split_scheduler_from_price_monitor() -> None:
    scheduler_unit = Path("deploy/systemd/hermes-btc-scheduler.service.example").read_text(encoding="utf-8")
    price_unit = Path("deploy/systemd/hermes-btc-price-monitor.service.example").read_text(encoding="utf-8")
    combined = scheduler_unit + "\n" + price_unit

    assert "scripts.run_scheduler" in scheduler_unit
    assert "scripts.run_price_monitor_10s" not in scheduler_unit
    assert "scripts.run_price_monitor_10s --trigger-source systemd" in price_unit
    assert "scripts.run_scheduler" not in price_unit
    for forbidden in ["HERMES_SECRET", "BINANCE_SECRET", "MYSQL_PASSWORD=", "REDIS_PASSWORD=", "token="]:
        assert forbidden not in combined


def test_phase_12_does_not_restore_forbidden_alert_switches_or_private_capabilities() -> None:
    sources = read_files(
        sorted(Path("app/scheduler").rglob("*.py"))
        + [
            Path("scripts/run_scheduler.py"),
            Path(".env.example"),
        ]
    )
    forbidden_terms = [
        "--send" "-alert",
        "PRICE_MONITOR_SEND_ALERT",
        "KLINE_4H_COLLECT_SEND_ALERT",
        "deepseek_client",
        "create_" "order",
        "get_" "account",
        "get_" "position",
        "listen" "Key",
        "/fapi/v1/" "ticker",
    ]

    for term in forbidden_terms:
        assert term not in sources
