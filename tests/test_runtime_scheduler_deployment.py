from __future__ import annotations

import json
from datetime import datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.alerting.types import AlertType
from app.core.config import AppSettings, load_settings
from app.core.exceptions import ConfigError, RedisError
from app.market_data.collector.types import (
    EXIT_SUCCESS as COLLECT_EXIT_SUCCESS,
    IncrementalKlineCollectRequest,
    IncrementalKlineCollectResult,
    KlineCollectStatus,
)
from app.market_data.collector.kline_1d_incremental_types import (
    EXIT_SUCCESS as COLLECT_1D_EXIT_SUCCESS,
    EXIT_SKIPPED as COLLECT_1D_EXIT_SKIPPED,
    IncrementalKline1dCollectRequest,
    IncrementalKline1dCollectResult,
)
from app.market_data.kline_constants import TRIGGER_SOURCE_SCHEDULER
from app.market_data.kline_integrity.types import (
    CHECK_MODE_DAILY_INTEGRITY_CHECK,
    DailyKlineIntegrityCheckRequest,
    DailyKlineIntegrityCheckResult,
    DailyKlineIntegrityStatus,
)
from app.market_data.kline_integrity.kline_1d_integrity_types import (
    DailyKline1dIntegrityCheckRequest,
    DailyKline1dIntegrityCheckResult,
    DailyKline1dIntegrityStatus,
)
from app.market_data.kline_quality.types import CHECK_TRIGGER_SOURCE_SCHEDULER
from app.scheduler.config import SchedulerRuntimeConfig, build_scheduler_runtime_config
from app.scheduler.execution_slot import SchedulerExecutionSlotStore
from app.scheduler.slot_state import (
    DAILY_KLINE_INTEGRITY_JOB_NAME,
    KLINE_1D_INCREMENTAL_JOB_NAME,
    KLINE_1D_INTEGRITY_JOB_NAME,
    KLINE_4H_INCREMENTAL_JOB_NAME,
    RedisSchedulerSlotStore,
    SchedulerSlotAction,
    SchedulerSlotDecision,
    SchedulerSlotStatus,
    build_scheduler_completed_key,
    build_scheduler_running_key,
    build_scheduler_status_key,
)
from app.scheduler.jobs.daily_kline_integrity_check import run_daily_kline_integrity_check_job
from app.scheduler.jobs.kline_1d_incremental_collect import run_kline_1d_incremental_collect_job
from app.scheduler.jobs.kline_1d_integrity_check import run_kline_1d_integrity_check_job
from app.scheduler.jobs.kline_4h_incremental_collect import run_kline_4h_incremental_collect_job
from app.scheduler.runner import SchedulerRunner, SchedulerSlotLogThrottle
from scripts import run_scheduler as run_scheduler_script


class FakeSlotStore:
    def __init__(
        self,
        *,
        acquired: bool = True,
        fail: bool = False,
        fail_completed_marker: bool = False,
        skip_status: SchedulerSlotStatus = SchedulerSlotStatus.RUNNING,
        skip_reason: str = "running_lock_active",
    ) -> None:
        self.acquired = acquired
        self.fail = fail
        self.fail_completed_marker = fail_completed_marker
        self.skip_status = skip_status
        self.skip_reason = skip_reason
        self.calls: list[dict[str, Any]] = []
        self.completed_calls: list[dict[str, Any]] = []
        self.status_calls: list[dict[str, Any]] = []
        self.release_calls: list[dict[str, Any]] = []

    def acquire_slot_for_run(
        self,
        *,
        job: str,
        slot: str,
        owner: str,
        running_ttl_seconds: int,
        status_marker_ttl_seconds: int,
        current_time_utc: datetime,
    ) -> SchedulerSlotDecision:
        self.calls.append(
            {
                "job": job,
                "slot": slot,
                "owner": owner,
                "running_ttl_seconds": running_ttl_seconds,
                "status_marker_ttl_seconds": status_marker_ttl_seconds,
                "current_time_utc": current_time_utc,
            }
        )
        if self.fail:
            raise RedisError("execution slot unavailable")
        running_key = build_scheduler_running_key(job=job, slot=slot)
        completed_key = build_scheduler_completed_key(job=job, slot=slot)
        status_key = build_scheduler_status_key(job=job, slot=slot)
        if self.acquired:
            return SchedulerSlotDecision(
                job=job,
                slot=slot,
                action=SchedulerSlotAction.ACQUIRED,
                status=SchedulerSlotStatus.RUNNING,
                running_key=running_key,
                completed_key=completed_key,
                status_key=status_key,
                owner=owner,
                reason="running_lock_acquired",
                ttl_seconds=running_ttl_seconds,
                running_value=json.dumps({"job": job, "slot": slot, "owner": owner}),
            )
        return SchedulerSlotDecision(
            job=job,
            slot=slot,
            action=SchedulerSlotAction.SKIP,
            status=self.skip_status,
            running_key=running_key,
            completed_key=completed_key,
            status_key=status_key,
            owner=owner,
            reason=self.skip_reason,
            ttl_seconds=running_ttl_seconds,
            details={"owner": "other-owner", "created_at_utc": "2026-05-13T04:05:00Z"},
        )

    def mark_slot_completed(self, **kwargs: Any) -> None:
        if self.fail_completed_marker:
            raise RedisError("completed marker unavailable")
        self.completed_calls.append(kwargs)

    def mark_slot_status(self, **kwargs: Any) -> None:
        self.status_calls.append(kwargs)

    def release_running_lock(self, **kwargs: Any) -> bool:
        self.release_calls.append(kwargs)
        return True


class FakeAlertSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, event: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"event": event, "kwargs": kwargs})
        return SimpleNamespace(status="sent")


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = int(ex) if ex is not None else -1
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def ttl(self, key: str) -> int:
        if key not in self.values:
            return -2
        return self.ttls.get(key, -1)

    def eval(self, _: str, __: int, key: str, expected_value: str) -> int:
        if self.values.get(key) != expected_value:
            return 0
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return 1


def runtime_config(**overrides: Any) -> SchedulerRuntimeConfig:
    data = {
        "enabled": True,
        "poll_interval_seconds": 30,
        "running_lock_ttl_seconds": 1800,
        "completed_marker_ttl_seconds": 259200,
        "status_marker_ttl_seconds": 86400,
        "slot_log_cooldown_seconds": 300,
        "kline_4h_incremental_collect_enabled": True,
        "kline_4h_incremental_collect_symbol": "BTCUSDT",
        "kline_4h_incremental_collect_interval": "4h",
        "kline_4h_incremental_collect_limit": 6,
        "kline_4h_incremental_collect_utc_minutes_after_close": 5,
        "kline_1d_incremental_collect_enabled": False,
        "kline_1d_incremental_collect_symbol": "BTCUSDT",
        "kline_1d_incremental_collect_interval": "1d",
        "kline_1d_incremental_collect_max_closed_count": 30,
        "kline_1d_incremental_collect_lock_ttl_seconds": 300,
        "kline_1d_incremental_collect_utc_time": time(hour=0, minute=10),
        "daily_kline_integrity_enabled": True,
        "daily_kline_integrity_symbol": "BTCUSDT",
        "daily_kline_integrity_interval": "4h",
        "daily_kline_integrity_limit": 100,
        "daily_kline_integrity_utc_time": time(hour=0, minute=30),
        "daily_kline_1d_integrity_enabled": False,
        "daily_kline_1d_integrity_symbol": "BTCUSDT",
        "daily_kline_1d_integrity_interval": "1d",
        "daily_kline_1d_integrity_limit": 500,
        "daily_kline_1d_integrity_notify_success": True,
        "daily_kline_1d_integrity_lock_ttl_seconds": 1800,
        "daily_kline_1d_integrity_utc_time": time(hour=0, minute=20),
    }
    data.update(overrides)
    return SchedulerRuntimeConfig(**data)


def utc_at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 5, 13, hour, minute, second, tzinfo=timezone.utc)


def kline_success_job(calls: list[str]) -> Any:
    def _job() -> IncrementalKlineCollectResult:
        calls.append("09")
        return IncrementalKlineCollectResult(
            status=KlineCollectStatus.SUCCESS,
            exit_code=COLLECT_EXIT_SUCCESS,
            trace_id="collect-trace",
            message="ok",
        )

    return _job


def daily_healthy_job(calls: list[str]) -> Any:
    def _job() -> DailyKlineIntegrityCheckResult:
        calls.append("11")
        return DailyKlineIntegrityCheckResult(
            status=DailyKlineIntegrityStatus.HEALTHY,
            exit_code=0,
            trace_id="daily-trace",
            message="ok",
            details={"report_status": "healthy"},
        )

    return _job


def kline_1d_success_job(calls: list[str]) -> Any:
    def _job() -> IncrementalKline1dCollectResult:
        calls.append("14-1d-collect")
        return IncrementalKline1dCollectResult(
            status=KlineCollectStatus.SUCCESS,
            exit_code=COLLECT_1D_EXIT_SUCCESS,
            trace_id="collect-1d-trace",
            message="ok",
        )

    return _job


def kline_1d_integrity_healthy_job(calls: list[str]) -> Any:
    def _job() -> DailyKline1dIntegrityCheckResult:
        calls.append("14-1d-integrity")
        return DailyKline1dIntegrityCheckResult(
            status=DailyKline1dIntegrityStatus.HEALTHY,
            exit_code=0,
            trace_id="daily-1d-trace",
            message="ok",
        )

    return _job


def read_files(paths: list[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_scheduler_runtime_config_loads_from_settings() -> None:
    settings = AppSettings(
        scheduler_enabled=False,
        scheduler_poll_interval_seconds=45,
        scheduler_running_lock_ttl_seconds=1200,
        scheduler_completed_marker_ttl_seconds=172800,
        scheduler_status_marker_ttl_seconds=43200,
        scheduler_slot_log_cooldown_seconds=600,
        kline_4h_incremental_collect_enabled=False,
        kline_4h_incremental_collect_symbol="btcusdt",
        kline_4h_incremental_collect_interval="4h",
        kline_4h_incremental_collect_limit=7,
        kline_4h_incremental_collect_utc_minutes_after_close=6,
        kline_1d_incremental_collect_enabled=True,
        kline_1d_incremental_collect_symbol="btcusdt",
        kline_1d_incremental_collect_interval="1d",
        kline_1d_incremental_collect_max_closed_count=12,
        kline_1d_incremental_collect_lock_ttl_seconds=301,
        kline_1d_incremental_collect_utc_time="00:10",
        daily_kline_integrity_enabled=False,
        daily_kline_integrity_symbol="btcusdt",
        daily_kline_integrity_interval="4h",
        daily_kline_integrity_limit=101,
        daily_kline_integrity_utc_time="01:15",
        daily_kline_1d_integrity_enabled=True,
        daily_kline_1d_integrity_symbol="btcusdt",
        daily_kline_1d_integrity_interval="1d",
        daily_kline_1d_integrity_limit=500,
        daily_kline_1d_integrity_notify_success=False,
        daily_kline_1d_integrity_lock_ttl_seconds=1201,
        daily_kline_1d_integrity_utc_time="00:20",
    )

    config = build_scheduler_runtime_config(settings)

    assert config.enabled is False
    assert config.poll_interval_seconds == 45
    assert config.running_lock_ttl_seconds == 1200
    assert config.completed_marker_ttl_seconds == 172800
    assert config.status_marker_ttl_seconds == 43200
    assert config.slot_log_cooldown_seconds == 600
    assert config.kline_4h_incremental_collect_enabled is False
    assert config.kline_4h_incremental_collect_symbol == "BTCUSDT"
    assert config.kline_4h_incremental_collect_limit == 7
    assert config.kline_1d_incremental_collect_enabled is True
    assert config.kline_1d_incremental_collect_symbol == "BTCUSDT"
    assert config.kline_1d_incremental_collect_utc_time == time(hour=0, minute=10)
    assert config.daily_kline_integrity_enabled is False
    assert config.daily_kline_integrity_utc_time == time(hour=1, minute=15)
    assert config.daily_kline_1d_integrity_enabled is True
    assert config.daily_kline_1d_integrity_utc_time == time(hour=0, minute=20)


def test_price_monitor_enabled_is_not_a_phase_12_required_config() -> None:
    sources = read_files(
        [
            Path(".env.example"),
            Path("docs/plans/12_runtime_scheduler_deployment.md"),
            Path("docs/implementation/12_runtime_scheduler_deployment.md"),
        ]
    )
    settings = load_settings(env_file=None, environ={"PRICE_MONITOR_ENABLED": "false"})

    assert "PRICE_MONITOR_ENABLED=true" not in sources
    assert "PRICE_MONITOR_ENABLED=" not in Path(".env.example").read_text(encoding="utf-8")
    assert not hasattr(settings, "price_monitor_enabled")


def test_daily_scheduler_time_uses_only_daily_kline_integrity_utc_time() -> None:
    calls: list[str] = []
    settings = load_settings(
        env_file=None,
        environ={
            "DAILY_KLINE_INTEGRITY_SCHEDULE_HOUR_UTC": "5",
            "DAILY_KLINE_INTEGRITY_SCHEDULE_MINUTE_UTC": "45",
            "DAILY_KLINE_INTEGRITY_UTC_TIME": "01:15",
        },
    )
    config = build_scheduler_runtime_config(settings)
    runner = SchedulerRunner(
        config=runtime_config(
            kline_4h_incremental_collect_enabled=False,
            daily_kline_integrity_utc_time=config.daily_kline_integrity_utc_time,
        ),
        slot_store=FakeSlotStore(),
        settings=AppSettings(),
        daily_integrity_job=daily_healthy_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(1, 15))

    assert config.daily_kline_integrity_utc_time == time(hour=1, minute=15)
    assert not hasattr(settings, "daily_kline_integrity_schedule_hour_utc")
    assert not hasattr(settings, "daily_kline_integrity_schedule_minute_utc")
    assert calls == ["11"]
    assert records[0].slot_key == "scheduler:running:daily_kline_integrity:2026-05-13"


def test_legacy_daily_hour_minute_config_does_not_trigger_scheduler() -> None:
    calls: list[str] = []
    settings = load_settings(
        env_file=None,
        environ={
            "DAILY_KLINE_INTEGRITY_SCHEDULE_HOUR_UTC": "5",
            "DAILY_KLINE_INTEGRITY_SCHEDULE_MINUTE_UTC": "45",
            "DAILY_KLINE_INTEGRITY_UTC_TIME": "01:15",
        },
    )
    config = build_scheduler_runtime_config(settings)
    runner = SchedulerRunner(
        config=runtime_config(
            kline_4h_incremental_collect_enabled=False,
            daily_kline_integrity_utc_time=config.daily_kline_integrity_utc_time,
        ),
        slot_store=FakeSlotStore(),
        settings=AppSettings(),
        daily_integrity_job=daily_healthy_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(5, 45))

    assert records == []
    assert calls == []


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
        kline_4h_job=kline_success_job(calls),
        daily_integrity_job=daily_healthy_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5, 10))

    assert calls == ["09"]
    assert records[0].job_name == KLINE_4H_INCREMENTAL_JOB_NAME
    assert records[0].status == "completed"
    assert slot_store.calls[0]["slot"] == "2026-05-13T04:05Z"
    assert slot_store.completed_calls[0]["completed_ttl_seconds"] == 259200
    assert slot_store.release_calls


def test_09_late_scheduler_start_catches_up_before_next_4h_slot() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore()
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        daily_integrity_job=daily_healthy_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(0, 6))

    assert calls == ["09"]
    assert records[0].status == "completed"
    assert slot_store.calls[0]["slot"] == "2026-05-13T00:05Z"


def test_09_late_slot_existing_still_skips_without_duplicate_execution() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore(acquired=False)
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(0, 6))

    assert calls == []
    assert records[0].status == "skipped"
    assert records[0].details["lock_status"] == "running"
    assert slot_store.calls[0]["slot"] == "2026-05-13T00:05Z"


def test_09_scheduler_uses_next_4h_slot_after_next_slot_arrives() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore()
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5))

    assert calls == ["09"]
    assert records[0].status == "completed"
    assert slot_store.calls[0]["slot"] == "2026-05-13T04:05Z"


def test_09_disabled_runner_does_not_run_09_job() -> None:
    calls: list[str] = []
    runner = SchedulerRunner(
        config=runtime_config(
            kline_4h_incremental_collect_enabled=False,
            daily_kline_integrity_enabled=False,
        ),
        slot_store=FakeSlotStore(),
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        daily_integrity_job=daily_healthy_job(calls),
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
        kline_4h_job=kline_success_job(calls),
        daily_integrity_job=daily_healthy_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(0, 30, 5))

    assert calls == ["11"]
    assert records[0].job_name == DAILY_KLINE_INTEGRITY_JOB_NAME
    assert records[0].status == "completed"
    assert slot_store.calls[0]["slot"] == "2026-05-13"


def test_11_late_scheduler_start_catches_up_within_daily_window() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore()
    runner = SchedulerRunner(
        config=runtime_config(kline_4h_incremental_collect_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        daily_integrity_job=daily_healthy_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(0, 31))

    assert calls == ["11"]
    assert records[0].status == "completed"
    assert slot_store.calls[0]["slot"] == "2026-05-13"


def test_11_daily_catch_up_window_expires_without_running_job() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore()
    runner = SchedulerRunner(
        config=runtime_config(kline_4h_incremental_collect_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        daily_integrity_job=daily_healthy_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(2, 31))

    assert records == []
    assert calls == []
    assert slot_store.calls == []


def test_11_late_daily_slot_existing_skips_without_duplicate_execution() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore(acquired=False, skip_status=SchedulerSlotStatus.COMPLETED, skip_reason="completed_marker_exists")
    runner = SchedulerRunner(
        config=runtime_config(kline_4h_incremental_collect_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        daily_integrity_job=daily_healthy_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(0, 31))

    assert calls == []
    assert records[0].status == "skipped"
    assert records[0].details["lock_status"] == "completed"
    assert slot_store.calls[0]["slot"] == "2026-05-13"


def test_11_disabled_runner_does_not_run_11_job() -> None:
    calls: list[str] = []
    runner = SchedulerRunner(
        config=runtime_config(
            kline_4h_incremental_collect_enabled=False,
            daily_kline_integrity_enabled=False,
        ),
        slot_store=FakeSlotStore(),
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        daily_integrity_job=daily_healthy_job(calls),
        alert_sender=FakeAlertSender(),
    )

    assert runner.run_once(current_time_utc=utc_at(0, 30)) == []
    assert calls == []


def test_14_4_1d_incremental_runner_calls_app_job_at_utc_0010() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore()
    runner = SchedulerRunner(
        config=runtime_config(
            kline_4h_incremental_collect_enabled=False,
            daily_kline_integrity_enabled=False,
            kline_1d_incremental_collect_enabled=True,
        ),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_1d_job=kline_1d_success_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(0, 10, 5))

    assert calls == ["14-1d-collect"]
    assert records[0].job_name == KLINE_1D_INCREMENTAL_JOB_NAME
    assert records[0].status == "completed"
    assert slot_store.calls[0]["slot"] == "2026-05-13T00:10Z"
    assert "1d" in records[0].slot_key
    assert "4h" not in records[0].slot_key


def test_14_4_1d_integrity_runner_calls_app_job_at_utc_0020() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore()
    runner = SchedulerRunner(
        config=runtime_config(
            kline_4h_incremental_collect_enabled=False,
            daily_kline_integrity_enabled=False,
            daily_kline_1d_integrity_enabled=True,
        ),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_1d_integrity_job=kline_1d_integrity_healthy_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(0, 20, 5))

    assert calls == ["14-1d-integrity"]
    assert records[0].job_name == KLINE_1D_INTEGRITY_JOB_NAME
    assert records[0].status == "completed"
    assert slot_store.calls[0]["slot"] == "2026-05-13"
    assert "kline_1d_integrity_check" in records[0].slot_key


def test_14_4_1d_incremental_late_start_catches_only_recent_slot() -> None:
    calls: list[str] = []
    runner = SchedulerRunner(
        config=runtime_config(
            kline_4h_incremental_collect_enabled=False,
            daily_kline_integrity_enabled=False,
            kline_1d_incremental_collect_enabled=True,
        ),
        slot_store=FakeSlotStore(),
        settings=AppSettings(),
        kline_1d_job=kline_1d_success_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(0, 11))
    expired_records = runner.run_once(current_time_utc=utc_at(2, 11))

    assert calls == ["14-1d-collect"]
    assert records[0].status == "completed"
    assert records[0].details["slot"] == "2026-05-13T00:10Z"
    assert expired_records == []


def test_14_4_1d_incremental_skipped_lock_result_is_scheduler_skipped_not_quality_failed() -> None:
    def skipped_job() -> IncrementalKline1dCollectResult:
        return IncrementalKline1dCollectResult(
            status=KlineCollectStatus.SKIPPED,
            exit_code=COLLECT_1D_EXIT_SKIPPED,
            trace_id="skip-trace",
            message="Skipped because task lock is already held",
        )

    slot_store = FakeSlotStore()
    runner = SchedulerRunner(
        config=runtime_config(
            kline_4h_incremental_collect_enabled=False,
            daily_kline_integrity_enabled=False,
            kline_1d_incremental_collect_enabled=True,
        ),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_1d_job=skipped_job,
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(0, 10))

    assert records[0].job_name == KLINE_1D_INCREMENTAL_JOB_NAME
    assert records[0].status == "skipped"
    assert slot_store.status_calls[0]["status"] == SchedulerSlotStatus.SKIPPED
    assert slot_store.status_calls[0]["reason"] == "kline_1d_incremental_skipped"
    assert slot_store.completed_calls == []


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


def test_14_4_1d_incremental_job_passes_scheduler_trigger_to_app_service() -> None:
    called: dict[str, Any] = {}
    expected_result = IncrementalKline1dCollectResult(
        status=KlineCollectStatus.SUCCESS,
        exit_code=COLLECT_1D_EXIT_SUCCESS,
        trace_id="trace",
        message="ok",
    )

    def fake_service(request: IncrementalKline1dCollectRequest, **kwargs: Any) -> Any:
        called["request"] = request
        called["kwargs"] = kwargs
        return expected_result

    db_session = object()
    result = run_kline_1d_incremental_collect_job(
        db_session=db_session,
        settings=AppSettings(),
        service_runner=fake_service,
    )

    assert result is expected_result
    assert called["request"].trigger_source == TRIGGER_SOURCE_SCHEDULER
    assert called["request"].data_source == "binance_rest_by_scheduler"
    assert called["request"].interval_value == "1d"
    assert called["request"].confirm_write is True
    assert called["request"].dry_run is False
    assert called["kwargs"]["db_session"] is db_session


def test_14_4_1d_integrity_job_passes_scheduler_trigger_to_app_service() -> None:
    called: dict[str, Any] = {}
    expected_result = DailyKline1dIntegrityCheckResult(
        status=DailyKline1dIntegrityStatus.HEALTHY,
        exit_code=0,
        trace_id="trace",
        message="ok",
    )

    def fake_service(request: DailyKline1dIntegrityCheckRequest, **kwargs: Any) -> Any:
        called["request"] = request
        called["kwargs"] = kwargs
        return expected_result

    db_session = object()
    result = run_kline_1d_integrity_check_job(
        db_session=db_session,
        settings=AppSettings(),
        service_runner=fake_service,
    )

    assert result is expected_result
    assert called["request"].check_trigger == CHECK_TRIGGER_SOURCE_SCHEDULER
    assert called["request"].data_source == "binance_rest_by_scheduler"
    assert called["request"].interval_value == "1d"
    assert called["request"].lookback_count == 500
    assert called["kwargs"]["db_session"] is db_session


def test_redis_execution_slot_existing_skips_without_running_job() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore(acquired=False)
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
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
        kline_4h_job=kline_success_job(calls),
        alert_sender=alert_sender,
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5))

    assert records[0].status == "failed"
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


def test_completed_marker_existing_skips_without_running_job_or_reserved_log() -> None:
    calls: list[str] = []
    fake_redis = FakeRedis()
    slot_store = RedisSchedulerSlotStore(redis_client=fake_redis)
    slot_store.mark_slot_completed(
        job=KLINE_4H_INCREMENTAL_JOB_NAME,
        slot="2026-05-13T04:05Z",
        owner="previous-owner",
        completed_ttl_seconds=259200,
        current_time_utc=utc_at(4, 6),
    )
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(4, 7))

    assert calls == []
    assert records[0].status == "skipped"
    assert records[0].message == "scheduler slot skipped: completed_marker_exists"
    assert records[0].details["lock_status"] == "completed"
    assert records[0].details["reason"] == "completed_marker_exists"
    assert build_scheduler_running_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z") not in fake_redis.values
    assert "already reserved" not in Path("app/scheduler/runner.py").read_text(encoding="utf-8")


def test_running_lock_existing_skips_without_parallel_execution() -> None:
    fake_redis = FakeRedis()
    slot_store = RedisSchedulerSlotStore(redis_client=fake_redis)
    first = slot_store.acquire_slot_for_run(
        job=KLINE_4H_INCREMENTAL_JOB_NAME,
        slot="2026-05-13T04:05Z",
        owner="owner-a",
        running_ttl_seconds=1800,
        status_marker_ttl_seconds=86400,
        current_time_utc=utc_at(4, 5),
    )

    second = slot_store.acquire_slot_for_run(
        job=KLINE_4H_INCREMENTAL_JOB_NAME,
        slot="2026-05-13T04:05Z",
        owner="owner-b",
        running_ttl_seconds=1800,
        status_marker_ttl_seconds=86400,
        current_time_utc=utc_at(4, 5, 30),
    )

    assert first.acquired is True
    assert second.acquired is False
    assert second.status == SchedulerSlotStatus.RUNNING
    assert second.reason == "running_lock_active"
    assert second.existing_lock is not None
    assert second.existing_lock.owner == "owner-a"
    assert build_scheduler_status_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z") not in fake_redis.values


def test_stale_running_lock_is_marked_and_retried_with_json_value() -> None:
    fake_redis = FakeRedis()
    slot_store = RedisSchedulerSlotStore(redis_client=fake_redis)
    running_key = build_scheduler_running_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z")
    fake_redis.set(running_key, "68923051fe8c415ca387f5d8fa967150", ex=79220)

    decision = slot_store.acquire_slot_for_run(
        job=KLINE_4H_INCREMENTAL_JOB_NAME,
        slot="2026-05-13T04:05Z",
        owner="owner-b",
        running_ttl_seconds=1800,
        status_marker_ttl_seconds=86400,
        current_time_utc=utc_at(4, 6),
    )

    status_key = build_scheduler_status_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z")
    status_marker = json.loads(fake_redis.values[status_key])
    running_value = json.loads(fake_redis.values[running_key])
    assert decision.acquired is True
    assert decision.action == SchedulerSlotAction.ACQUIRED_AFTER_STALE
    assert decision.reason == "retry_after_stale_running_lock"
    assert status_marker["status"] == "stale"
    assert status_marker["reason"] == "running_lock_value_invalid"
    assert running_value["job"] == KLINE_4H_INCREMENTAL_JOB_NAME
    assert running_value["slot"] == "2026-05-13T04:05Z"
    assert running_value["status"] == "running"
    assert running_value["owner"] == "owner-b"
    assert running_value["ttl_seconds"] == 1800
    assert "token" in running_value
    assert fake_redis.ttls[running_key] == 1800


def test_successful_job_releases_running_lock_and_writes_completed_marker() -> None:
    calls: list[str] = []
    fake_redis = FakeRedis()
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=RedisSchedulerSlotStore(redis_client=fake_redis),
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5))

    running_key = build_scheduler_running_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z")
    completed_key = build_scheduler_completed_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z")
    completed_marker = json.loads(fake_redis.values[completed_key])
    assert calls == ["09"]
    assert records[0].status == "completed"
    assert records[0].details["running_lock_released"] is True
    assert running_key not in fake_redis.values
    assert completed_marker["status"] == "completed"
    assert completed_marker["source"] == "scheduler"
    assert fake_redis.ttls[completed_key] == 259200


def test_blocked_job_releases_running_lock_and_writes_no_completed_marker() -> None:
    def blocked_job() -> IncrementalKlineCollectResult:
        return IncrementalKlineCollectResult(
            status=KlineCollectStatus.BLOCKED,
            exit_code=2,
            trace_id="blocked-trace",
            message="quality blocked",
        )

    fake_redis = FakeRedis()
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=RedisSchedulerSlotStore(redis_client=fake_redis),
        settings=AppSettings(),
        kline_4h_job=blocked_job,
        alert_sender=FakeAlertSender(),
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5))

    running_key = build_scheduler_running_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z")
    completed_key = build_scheduler_completed_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z")
    status_key = build_scheduler_status_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z")
    status_marker = json.loads(fake_redis.values[status_key])
    assert records[0].status == "blocked"
    assert running_key not in fake_redis.values
    assert completed_key not in fake_redis.values
    assert status_marker["status"] == "blocked"
    assert status_marker["reason"] == "kline_incremental_blocked"
    assert fake_redis.ttls[status_key] == 86400


def test_failed_and_skipped_jobs_write_status_marker_and_no_completed_marker() -> None:
    cases = [
        (KlineCollectStatus.FAILED, 4, SchedulerSlotStatus.FAILED, "kline_incremental_failed"),
        (KlineCollectStatus.SKIPPED, 0, SchedulerSlotStatus.SKIPPED, "kline_incremental_skipped"),
    ]
    for result_status, exit_code, expected_status, expected_reason in cases:
        def terminal_job(
            status: KlineCollectStatus = result_status,
            code: int = exit_code,
        ) -> IncrementalKlineCollectResult:
            return IncrementalKlineCollectResult(
                status=status,
                exit_code=code,
                trace_id=f"{status.value}-trace",
                message=status.value,
            )

        fake_redis = FakeRedis()
        runner = SchedulerRunner(
            config=runtime_config(daily_kline_integrity_enabled=False),
            slot_store=RedisSchedulerSlotStore(redis_client=fake_redis),
            settings=AppSettings(),
            kline_4h_job=terminal_job,
            alert_sender=FakeAlertSender(),
        )

        records = runner.run_once(current_time_utc=utc_at(4, 5))

        completed_key = build_scheduler_completed_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z")
        status_key = build_scheduler_status_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot="2026-05-13T04:05Z")
        status_marker = json.loads(fake_redis.values[status_key])
        assert records[0].status == expected_status.value
        assert completed_key not in fake_redis.values
        assert status_marker["status"] == expected_status.value
        assert status_marker["reason"] == expected_reason
        assert fake_redis.ttls[status_key] == 86400


def test_completed_marker_write_failure_returns_failed_and_sends_scheduler_alert() -> None:
    calls: list[str] = []
    alert_sender = FakeAlertSender()
    slot_store = FakeSlotStore(fail_completed_marker=True)
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        alert_sender=alert_sender,
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5))

    assert calls == ["09"]
    assert records[0].status == "failed"
    assert records[0].details["terminal_reason"] == "completed_marker_write_failed"
    assert len(alert_sender.calls) == 1
    assert alert_sender.calls[0]["event"].alert_type == AlertType.SYSTEM_ERROR
    assert alert_sender.calls[0]["event"].details["scheduler_job"] == KLINE_4H_INCREMENTAL_JOB_NAME
    assert slot_store.status_calls[0]["status"] == SchedulerSlotStatus.FAILED
    assert slot_store.status_calls[0]["reason"] == "completed_marker_write_failed"
    assert slot_store.release_calls


def test_release_running_lock_only_deletes_matching_owned_value() -> None:
    fake_redis = FakeRedis()
    slot_store = RedisSchedulerSlotStore(redis_client=fake_redis)
    decision = slot_store.acquire_slot_for_run(
        job=KLINE_4H_INCREMENTAL_JOB_NAME,
        slot="2026-05-13T04:05Z",
        owner="owner-a",
        running_ttl_seconds=1800,
        status_marker_ttl_seconds=86400,
        current_time_utc=utc_at(4, 5),
    )
    other_value = json.dumps({"job": KLINE_4H_INCREMENTAL_JOB_NAME, "slot": "2026-05-13T04:05Z", "owner": "owner-b"})
    fake_redis.set(decision.running_key, other_value, ex=1800)

    released = slot_store.release_running_lock(
        running_key=decision.running_key,
        running_value=decision.running_value or "",
    )

    assert released is False
    assert fake_redis.values[decision.running_key] == other_value


def test_running_slot_does_not_block_next_4h_slot_when_window_advances() -> None:
    calls: list[str] = []
    slot_store = FakeSlotStore(acquired=False)
    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=kline_success_job(calls),
        alert_sender=FakeAlertSender(),
    )

    first_records = runner.run_once(current_time_utc=utc_at(4, 6))
    slot_store.acquired = True
    second_records = runner.run_once(current_time_utc=utc_at(8, 5))

    assert first_records[0].status == "skipped"
    assert second_records[0].status == "completed"
    assert calls == ["09"]
    assert [call["slot"] for call in slot_store.calls] == ["2026-05-13T04:05Z", "2026-05-13T08:05Z"]


def test_scheduler_slot_log_throttle_suppresses_repeated_reason_inside_cooldown() -> None:
    throttle = SchedulerSlotLogThrottle(cooldown_seconds=300)

    assert throttle.should_emit(
        job=KLINE_4H_INCREMENTAL_JOB_NAME,
        slot="2026-05-13T04:05Z",
        reason="running_lock_active",
        current_time_utc=utc_at(4, 5),
    )
    assert not throttle.should_emit(
        job=KLINE_4H_INCREMENTAL_JOB_NAME,
        slot="2026-05-13T04:05Z",
        reason="running_lock_active",
        current_time_utc=utc_at(4, 5, 30),
    )
    assert throttle.should_emit(
        job=KLINE_4H_INCREMENTAL_JOB_NAME,
        slot="2026-05-13T04:05Z",
        reason="running_lock_active",
        current_time_utc=utc_at(4, 10),
    )
    assert throttle.should_emit(
        job=KLINE_4H_INCREMENTAL_JOB_NAME,
        slot="2026-05-13T04:05Z",
        reason="completed_marker_exists",
        current_time_utc=utc_at(4, 10),
    )


def test_execution_slot_compatibility_name_has_no_legacy_reserve_method() -> None:
    source = Path("app/scheduler/execution_slot.py").read_text(encoding="utf-8")

    assert not hasattr(SchedulerExecutionSlotStore(redis_client=FakeRedis()), "reserve_" "execution_slot")
    assert ".reserve_" "execution_slot(" not in source


def test_scheduler_job_wrapper_exception_sends_system_alert() -> None:
    alert_sender = FakeAlertSender()
    slot_store = FakeSlotStore()

    def failing_job() -> None:
        raise RuntimeError("wrapper failed")

    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=slot_store,
        settings=AppSettings(),
        kline_4h_job=failing_job,
        alert_sender=alert_sender,
    )

    records = runner.run_once(current_time_utc=utc_at(4, 5))

    assert records[0].status == "failed"
    assert records[0].details["job_wrapper_error"] is True
    assert len(alert_sender.calls) == 1
    assert alert_sender.calls[0]["event"].alert_type == AlertType.SYSTEM_ERROR
    assert slot_store.status_calls[0]["status"] == SchedulerSlotStatus.FAILED
    assert slot_store.completed_calls == []
    assert slot_store.release_calls


def test_run_scheduler_config_error_invokes_startup_system_alert_when_settings_loaded(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    settings = AppSettings()
    calls: list[dict[str, Any]] = []

    def fail_config(_: AppSettings) -> None:
        raise ConfigError("DAILY_KLINE_INTEGRITY_UTC_TIME 必须使用 HH:MM 格式")

    def fake_startup_alert(*, settings: AppSettings | None, error: ConfigError) -> bool:
        calls.append({"settings": settings, "error": error})
        return True

    monkeypatch.setattr(run_scheduler_script, "get_settings", lambda: settings)
    monkeypatch.setattr(run_scheduler_script, "configure_logging", lambda _: None)
    monkeypatch.setattr(run_scheduler_script, "build_scheduler_runtime_config", fail_config)
    monkeypatch.setattr(run_scheduler_script, "_send_scheduler_startup_config_error_alert", fake_startup_alert)

    exit_code = run_scheduler_script.main([])

    assert exit_code == run_scheduler_script.EXIT_PARAMETER_ERROR
    assert calls[0]["settings"] is settings
    assert "DAILY_KLINE_INTEGRITY_UTC_TIME" in str(calls[0]["error"])
    assert "scheduler config error" in capsys.readouterr().out


def test_scheduler_startup_config_error_alert_uses_fixed_template_and_local_cooldown(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    cooldown_file = tmp_path / "scheduler_startup_config_error_alert.cooldown"

    def fake_alert_sender(event: Any, **kwargs: Any) -> SimpleNamespace:
        calls.append({"event": event, "kwargs": kwargs})
        return SimpleNamespace(status="sent")

    first_attempt = run_scheduler_script._send_scheduler_startup_config_error_alert(
        settings=AppSettings(),
        error=ConfigError("invalid scheduler config"),
        alert_sender=fake_alert_sender,
        cooldown_file=cooldown_file,
    )
    second_attempt = run_scheduler_script._send_scheduler_startup_config_error_alert(
        settings=AppSettings(),
        error=ConfigError("invalid scheduler config"),
        alert_sender=fake_alert_sender,
        cooldown_file=cooldown_file,
    )

    assert first_attempt is True
    assert second_attempt is False
    assert len(calls) == 1
    event = calls[0]["event"]
    assert event.alert_type == AlertType.SYSTEM_ERROR
    assert event.details["scheduler_stage"] == "startup"
    assert event.details["scheduler_started"] is False
    assert event.details["no_auto_repair"] is True
    assert event.details["no_auto_backfill"] is True
    assert event.details["no_trading"] is True
    assert calls[0]["kwargs"]["send_real_alert"] is True


def test_run_scheduler_config_error_without_settings_does_not_force_alert(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    def fail_settings() -> None:
        raise ConfigError("APP_DEBUG 必须是布尔值")

    def fail_if_alerting_is_called(*_: Any, **__: Any) -> None:
        raise AssertionError("alerting must not initialize when settings cannot load")

    monkeypatch.setattr(run_scheduler_script, "get_settings", fail_settings)
    monkeypatch.setattr(run_scheduler_script, "_default_alert_sender", fail_if_alerting_is_called)

    exit_code = run_scheduler_script.main([])

    assert exit_code == run_scheduler_script.EXIT_PARAMETER_ERROR
    assert "APP_DEBUG 必须是布尔值" in capsys.readouterr().out


def test_scheduler_sources_do_not_call_scripts_or_start_price_monitor() -> None:
    scheduler_source = read_files(sorted(Path("app/scheduler").rglob("*.py")))
    run_scheduler_source = Path("scripts/run_scheduler.py").read_text(encoding="utf-8")

    forbidden_scheduler_terms = [
        "scripts.collect_4h_klines",
        "scripts.collect_1d_klines",
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
        "PRICE_MONITOR_ENABLED",
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
