"""Long-running UTC scheduler runner for phase-12 production operation.

Call chain:
scripts/run_scheduler.py::main
    -> app/scheduler/runner.py::run_scheduler_forever
    -> app/scheduler/runner.py::SchedulerRunner.run_once
    -> app/scheduler/jobs/kline_4h_incremental_collect.py::run_kline_4h_incremental_collect_job
    -> app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job

This file belongs to `app/scheduler`. It polls UTC time, reserves Redis
execution slots, and calls thin scheduler jobs for phases 09 and 11. It does
not call scripts, request Binance directly, read/write business MySQL tables,
read/write `bitcoin_price`, implement 09/11 business checks, call DeepSeek,
generate advice, or perform trading.
"""

from __future__ import annotations

import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable
from uuid import uuid4

from app.alerting.types import AlertEvent, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.core.exceptions import ConfigError, RedisError
from app.core.logger import get_logger
from app.core.time_utils import UTC, now_utc
from app.scheduler.config import SchedulerRuntimeConfig, build_scheduler_runtime_config
from app.scheduler.execution_slot import (
    DAILY_KLINE_INTEGRITY_JOB_NAME,
    KLINE_4H_INCREMENTAL_JOB_NAME,
    SchedulerExecutionSlotStore,
    build_daily_kline_integrity_slot_key,
    build_kline_4h_incremental_slot_key,
)

LOGGER = get_logger("scheduler.runner")
FOUR_HOUR_SLOT_INTERVAL = timedelta(hours=4)
DAILY_INTEGRITY_CATCH_UP_WINDOW = timedelta(hours=2)

JobCallable = Callable[[], Any]
AlertSender = Callable[..., Any]


@dataclass(frozen=True)
class DueSchedulerJob:
    """One scheduler job that is due in the current polling window."""

    name: str
    slot_key: str
    slot_time_utc: datetime
    job_runner: JobCallable


@dataclass(frozen=True)
class SchedulerRunRecord:
    """Result record for one scheduler wrapper action."""

    job_name: str
    status: str
    slot_key: str
    trace_id: str
    message: str
    result: Any | None = None
    details: dict[str, object] = field(default_factory=dict)


class SchedulerRunner:
    """Poll UTC time, de-duplicate slots in Redis, and call due app jobs.

    Parameters: config controls schedules; slot_store handles Redis slot writes;
    job runners and alert sender are injectable for tests.
    Return value: call `run_once()` for a finite pass or `run_forever()` for the
    systemd process.
    Failure scenarios: Redis slot failures and job-wrapper exceptions are logged
    and reported by fixed-template scheduler system alerts.
    External effects: writes Redis execution-slot keys and delegates business
    effects to 09/11 services only after slot reservation succeeds.
    """

    def __init__(
        self,
        *,
        config: SchedulerRuntimeConfig,
        slot_store: SchedulerExecutionSlotStore,
        settings: AppSettings | None = None,
        kline_4h_job: JobCallable | None = None,
        daily_integrity_job: JobCallable | None = None,
        alert_sender: AlertSender | None = None,
        sleep_fn: Callable[[float], None] = time_module.sleep,
    ) -> None:
        self.config = config
        self.slot_store = slot_store
        self.settings = settings or get_settings()
        self.kline_4h_job = kline_4h_job or _default_kline_4h_job
        self.daily_integrity_job = daily_integrity_job or _default_daily_integrity_job
        self.alert_sender = alert_sender or _default_alert_sender
        self.sleep_fn = sleep_fn

    def run_once(self, *, current_time_utc: datetime | None = None) -> list[SchedulerRunRecord]:
        """Run one scheduler polling pass.

        Parameters: optional UTC time for tests.
        Return value: records for executed, skipped, disabled, or errored jobs.
        Failure scenarios: wrapper-level exceptions are converted into records
        after a fixed-template scheduler alert attempt.
        """

        active_now = _ensure_utc_aware(current_time_utc or now_utc())
        if not self.config.enabled:
            LOGGER.info("scheduler disabled at %s", active_now.isoformat())
            return [
                SchedulerRunRecord(
                    job_name="scheduler",
                    status="disabled",
                    slot_key="",
                    trace_id=uuid4().hex,
                    message="scheduler disabled",
                )
            ]

        records: list[SchedulerRunRecord] = []
        for due_job in self._iter_due_jobs(active_now):
            records.append(self._reserve_slot_and_run_job(due_job))
        return records

    def run_forever(self) -> None:
        """Run the scheduler loop until interrupted by the process manager."""

        LOGGER.info("scheduler runner started")
        while True:
            self.run_once()
            self.sleep_fn(self.config.poll_interval_seconds)

    def _iter_due_jobs(self, current_time_utc: datetime) -> Iterable[DueSchedulerJob]:
        if self.config.kline_4h_incremental_collect_enabled:
            slot_time = _due_kline_4h_slot_time(current_time_utc, self.config)
            if slot_time is not None:
                yield DueSchedulerJob(
                    name=KLINE_4H_INCREMENTAL_JOB_NAME,
                    slot_key=build_kline_4h_incremental_slot_key(slot_time),
                    slot_time_utc=slot_time,
                    job_runner=self.kline_4h_job,
                )
        if self.config.daily_kline_integrity_enabled:
            slot_time = _due_daily_integrity_slot_time(current_time_utc, self.config)
            if slot_time is not None:
                yield DueSchedulerJob(
                    name=DAILY_KLINE_INTEGRITY_JOB_NAME,
                    slot_key=build_daily_kline_integrity_slot_key(slot_time.date()),
                    slot_time_utc=slot_time,
                    job_runner=self.daily_integrity_job,
                )

    def _reserve_slot_and_run_job(self, due_job: DueSchedulerJob) -> SchedulerRunRecord:
        trace_id = uuid4().hex
        try:
            reserved = self.slot_store.reserve_execution_slot(
                key=due_job.slot_key,
                owner=trace_id,
                ttl_seconds=self.config.job_slot_ttl_seconds,
            )
        except RedisError as exc:
            LOGGER.exception("scheduler execution slot failed job=%s key=%s", due_job.name, due_job.slot_key)
            self._send_scheduler_system_alert(
                trace_id=trace_id,
                job_name=due_job.name,
                summary="Scheduler cannot safely decide whether the job already ran.",
                error=exc,
                details={"slot_key": due_job.slot_key, "slot_time_utc": due_job.slot_time_utc.isoformat()},
            )
            return SchedulerRunRecord(
                job_name=due_job.name,
                status="error",
                slot_key=due_job.slot_key,
                trace_id=trace_id,
                message=str(exc),
                details={"slot_error": True},
            )
        if not reserved:
            LOGGER.info("scheduler slot already reserved job=%s key=%s", due_job.name, due_job.slot_key)
            return SchedulerRunRecord(
                job_name=due_job.name,
                status="skipped",
                slot_key=due_job.slot_key,
                trace_id=trace_id,
                message="execution slot already exists",
                details={"slot_time_utc": due_job.slot_time_utc.isoformat()},
            )

        try:
            result = due_job.job_runner()
        except Exception as exc:  # noqa: BLE001 - wrapper failures need scheduler-level alerts.
            LOGGER.exception("scheduler job wrapper failed job=%s key=%s", due_job.name, due_job.slot_key)
            self._send_scheduler_system_alert(
                trace_id=trace_id,
                job_name=due_job.name,
                summary="Scheduler job wrapper failed before it could return a safe result.",
                error=exc,
                details={"slot_key": due_job.slot_key, "slot_time_utc": due_job.slot_time_utc.isoformat()},
            )
            return SchedulerRunRecord(
                job_name=due_job.name,
                status="error",
                slot_key=due_job.slot_key,
                trace_id=trace_id,
                message=str(exc),
                details={"job_wrapper_error": True},
            )

        LOGGER.info("scheduler job finished job=%s key=%s", due_job.name, due_job.slot_key)
        return SchedulerRunRecord(
            job_name=due_job.name,
            status="executed",
            slot_key=due_job.slot_key,
            trace_id=trace_id,
            message="job executed",
            result=result,
            details={"slot_time_utc": due_job.slot_time_utc.isoformat()},
        )

    def _send_scheduler_system_alert(
        self,
        *,
        trace_id: str,
        job_name: str,
        summary: str,
        error: BaseException,
        details: dict[str, object],
    ) -> None:
        event = AlertEvent(
            alert_type=AlertType.SYSTEM_ERROR,
            severity=AlertSeverity.CRITICAL,
            title="Scheduler runtime exception",
            summary=summary,
            details={
                "scheduler_job": job_name,
                "error_type": error.__class__.__name__,
                "error_message": str(error),
                "no_auto_repair": True,
                "no_auto_backfill": True,
                "no_trading": True,
                **details,
            },
            source="app.scheduler.runner",
            trace_id=trace_id,
        )
        try:
            self.alert_sender(event, settings=self.settings, send_real_alert=True)
        except Exception:  # noqa: BLE001 - alert failure must not crash the scheduler loop.
            LOGGER.exception("scheduler system alert failed job=%s trace_id=%s", job_name, trace_id)


def run_scheduler_forever(
    *,
    settings: AppSettings | None = None,
    config: SchedulerRuntimeConfig | None = None,
    slot_store: SchedulerExecutionSlotStore | None = None,
) -> None:
    """Build dependencies and run the long-lived scheduler loop."""

    active_settings = settings or get_settings()
    active_config = config or build_scheduler_runtime_config(active_settings)
    active_slot_store = slot_store or SchedulerExecutionSlotStore()
    runner = SchedulerRunner(
        config=active_config,
        slot_store=active_slot_store,
        settings=active_settings,
    )
    runner.run_forever()


def _default_kline_4h_job() -> Any:
    from app.scheduler.jobs.kline_4h_incremental_collect import run_kline_4h_incremental_collect_job

    return run_kline_4h_incremental_collect_job()


def _default_daily_integrity_job() -> Any:
    from app.scheduler.jobs.daily_kline_integrity_check import run_daily_kline_integrity_check_job

    return run_daily_kline_integrity_check_job()


def _default_alert_sender(*args: Any, **kwargs: Any) -> Any:
    from app.alerting.service import send_alert

    return send_alert(*args, **kwargs)


def _due_kline_4h_slot_time(
    current_time_utc: datetime,
    config: SchedulerRuntimeConfig,
) -> datetime | None:
    scheduled = _latest_kline_4h_scheduled_slot_time(
        current_time_utc,
        minutes_after_close=config.kline_4h_incremental_collect_utc_minutes_after_close,
    )
    if scheduled <= current_time_utc < scheduled + FOUR_HOUR_SLOT_INTERVAL:
        return scheduled
    return None


def _due_daily_integrity_slot_time(
    current_time_utc: datetime,
    config: SchedulerRuntimeConfig,
) -> datetime | None:
    scheduled = current_time_utc.replace(
        hour=config.daily_kline_integrity_utc_time.hour,
        minute=config.daily_kline_integrity_utc_time.minute,
        second=0,
        microsecond=0,
    )
    if current_time_utc < scheduled:
        scheduled -= timedelta(days=1)
    if _is_inside_catch_up_window(
        current_time_utc,
        scheduled,
        catch_up_window=DAILY_INTEGRITY_CATCH_UP_WINDOW,
    ):
        return scheduled
    return None


def _latest_kline_4h_scheduled_slot_time(
    current_time_utc: datetime,
    *,
    minutes_after_close: int,
) -> datetime:
    """Return the latest 4h scheduler slot whose catch-up period is active.

    The 09 business service already fetches a recent REST overlap window and
    performs all Kline quality checks. This scheduler helper only decides which
    UTC execution slot should be attempted: after the configured minute arrives,
    the same slot remains eligible until the next 4h slot arrives. Redis
    execution-slot reservation still decides whether that slot already ran.
    """

    bucket_hour = (current_time_utc.hour // 4) * 4
    scheduled = current_time_utc.replace(
        hour=bucket_hour,
        minute=minutes_after_close,
        second=0,
        microsecond=0,
    )
    if current_time_utc < scheduled:
        scheduled -= FOUR_HOUR_SLOT_INTERVAL
    return scheduled


def _is_inside_catch_up_window(
    current_time_utc: datetime,
    scheduled_time_utc: datetime,
    *,
    catch_up_window: timedelta,
) -> bool:
    delta = current_time_utc - scheduled_time_utc
    return timedelta(seconds=0) <= delta < catch_up_window


def _ensure_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ConfigError("scheduler current_time_utc must be timezone-aware UTC")
    return value.astimezone(UTC)


__all__ = [
    "SchedulerRunRecord",
    "SchedulerRunner",
    "run_scheduler_forever",
]
