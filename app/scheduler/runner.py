"""Long-running UTC scheduler runner for phase-12 production operation.

Call chain:
scripts/run_scheduler.py::main
    -> app/scheduler/runner.py::run_scheduler_forever
    -> app/scheduler/runner.py::SchedulerRunner.run_once
    -> app/scheduler/jobs/kline_4h_incremental_collect.py::run_kline_4h_incremental_collect_job
    -> app/scheduler/jobs/strategy_signal_scheduler_job.py::run_strategy_signal_scheduler_after_collect_job
    -> app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job

This file belongs to `app/scheduler`. It polls UTC time, separates Redis
running locks from completed markers, and calls thin scheduler jobs for phases
09, 11, 14, and the stage-17 strategy-signal orchestration hook. It does not
call strategy scripts, request Binance directly, read/write `bitcoin_price`,
implement collector business checks, call DeepSeek, generate final advice, or
perform trading.
"""

from __future__ import annotations

import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from app.alerting.types import AlertEvent, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.core.exceptions import ConfigError, RedisError
from app.core.logger import get_logger
from app.core.time_utils import UTC, now_utc
from app.scheduler.config import SchedulerRuntimeConfig, build_scheduler_runtime_config
from app.scheduler.slot_state import (
    DAILY_KLINE_INTEGRITY_JOB_NAME,
    KLINE_1D_INCREMENTAL_JOB_NAME,
    KLINE_1D_INTEGRITY_JOB_NAME,
    KLINE_4H_INCREMENTAL_JOB_NAME,
    RedisSchedulerSlotStore,
    SchedulerSlotDecision,
    SchedulerSlotStatus,
    build_daily_kline_integrity_slot_id,
    build_kline_1d_incremental_slot_id,
    build_kline_1d_integrity_slot_id,
    build_kline_4h_incremental_slot_id,
    build_scheduler_owner,
)

LOGGER = get_logger("scheduler.runner")
FOUR_HOUR_SLOT_INTERVAL = timedelta(hours=4)
DAILY_INTEGRITY_CATCH_UP_WINDOW = timedelta(hours=2)
DAILY_1D_JOB_CATCH_UP_WINDOW = timedelta(hours=2)

JobCallable = Callable[[], Any]
StrategySignalAfterCollectCallable = Callable[..., Any]
AlertSender = Callable[..., Any]


@dataclass(frozen=True)
class DueSchedulerJob:
    """One scheduler job that is due in the current polling window."""

    name: str
    slot_id: str
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
    """Poll UTC time, manage slot state in Redis, and call due app jobs.

    Parameters: config controls schedules; slot_store handles Redis slot state;
    job runners, alert sender, and sleep function are injectable for tests.
    Return value: call `run_once()` for a finite pass or `run_forever()` for the
    systemd process.
    Failure scenarios: Redis state failures and job-wrapper exceptions are
    logged and reported by fixed-template scheduler system alerts.
    External effects: writes Redis scheduler state keys and delegates business
    effects to 09/11 services only after a running lock is acquired.
    """

    def __init__(
        self,
        *,
        config: SchedulerRuntimeConfig,
        slot_store: RedisSchedulerSlotStore,
        settings: AppSettings | None = None,
        kline_4h_job: JobCallable | None = None,
        kline_1d_job: JobCallable | None = None,
        kline_1d_integrity_job: JobCallable | None = None,
        daily_integrity_job: JobCallable | None = None,
        strategy_signal_after_collect_job: StrategySignalAfterCollectCallable | None = None,
        alert_sender: AlertSender | None = None,
        sleep_fn: Callable[[float], None] = time_module.sleep,
    ) -> None:
        self.config = config
        self.slot_store = slot_store
        self.settings = settings or get_settings()
        self.kline_4h_job = kline_4h_job or _default_kline_4h_job
        self.kline_1d_job = kline_1d_job or _default_kline_1d_job
        self.kline_1d_integrity_job = kline_1d_integrity_job or _default_kline_1d_integrity_job
        self.daily_integrity_job = daily_integrity_job or _default_daily_integrity_job
        self.strategy_signal_after_collect_job = strategy_signal_after_collect_job or _default_strategy_signal_after_collect_job
        self.alert_sender = alert_sender or _default_alert_sender
        self.sleep_fn = sleep_fn
        self._slot_log_throttle = SchedulerSlotLogThrottle(config.slot_log_cooldown_seconds)

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
            records.append(self._acquire_slot_and_run_job(due_job, current_time_utc=active_now))
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
                    slot_id=build_kline_4h_incremental_slot_id(slot_time),
                    slot_time_utc=slot_time,
                    job_runner=self.kline_4h_job,
                )
        if self.config.kline_1d_incremental_collect_enabled:
            slot_time = _due_daily_utc_time_slot(
                current_time_utc,
                self.config.kline_1d_incremental_collect_utc_time,
                catch_up_window=DAILY_1D_JOB_CATCH_UP_WINDOW,
            )
            if slot_time is not None:
                yield DueSchedulerJob(
                    name=KLINE_1D_INCREMENTAL_JOB_NAME,
                    slot_id=build_kline_1d_incremental_slot_id(slot_time),
                    slot_time_utc=slot_time,
                    job_runner=self.kline_1d_job,
                )
        if self.config.daily_kline_1d_integrity_enabled:
            slot_time = _due_daily_utc_time_slot(
                current_time_utc,
                self.config.daily_kline_1d_integrity_utc_time,
                catch_up_window=DAILY_1D_JOB_CATCH_UP_WINDOW,
            )
            if slot_time is not None:
                yield DueSchedulerJob(
                    name=KLINE_1D_INTEGRITY_JOB_NAME,
                    slot_id=build_kline_1d_integrity_slot_id(slot_time.date()),
                    slot_time_utc=slot_time,
                    job_runner=self.kline_1d_integrity_job,
                )
        if self.config.daily_kline_integrity_enabled:
            slot_time = _due_daily_integrity_slot_time(current_time_utc, self.config)
            if slot_time is not None:
                yield DueSchedulerJob(
                    name=DAILY_KLINE_INTEGRITY_JOB_NAME,
                    slot_id=build_daily_kline_integrity_slot_id(slot_time.date()),
                    slot_time_utc=slot_time,
                    job_runner=self.daily_integrity_job,
                )

    def _acquire_slot_and_run_job(
        self,
        due_job: DueSchedulerJob,
        *,
        current_time_utc: datetime,
    ) -> SchedulerRunRecord:
        trace_id = uuid4().hex
        owner = build_scheduler_owner(trace_id=trace_id)
        try:
            decision = self.slot_store.acquire_slot_for_run(
                job=due_job.name,
                slot=due_job.slot_id,
                owner=owner,
                running_ttl_seconds=self.config.running_lock_ttl_seconds,
                status_marker_ttl_seconds=self.config.status_marker_ttl_seconds,
                current_time_utc=current_time_utc,
            )
        except RedisError as exc:
            LOGGER.exception("scheduler slot state failed job=%s slot=%s", due_job.name, due_job.slot_id)
            self._send_scheduler_system_alert(
                trace_id=trace_id,
                job_name=due_job.name,
                summary="Scheduler cannot safely decide whether the job slot should run.",
                error=exc,
                details={"slot": due_job.slot_id, "slot_time_utc": due_job.slot_time_utc.isoformat()},
            )
            return SchedulerRunRecord(
                job_name=due_job.name,
                status=SchedulerSlotStatus.FAILED.value,
                slot_key="",
                trace_id=trace_id,
                message=str(exc),
                details={"slot_error": True},
            )
        if not decision.acquired:
            self._log_slot_skip_once(due_job, decision, current_time_utc=current_time_utc)
            return SchedulerRunRecord(
                job_name=due_job.name,
                status="skipped",
                slot_key=decision.running_key,
                trace_id=trace_id,
                message=f"scheduler slot skipped: {decision.reason}",
                details=_slot_decision_details(due_job, decision, action="skip"),
            )

        LOGGER.info(
            "scheduler slot running acquired job=%s slot=%s lock_key=%s ttl=%s owner=%s action=%s",
            due_job.name,
            due_job.slot_id,
            decision.running_key,
            decision.ttl_seconds,
            decision.owner,
            decision.action.value,
        )
        terminal_status = SchedulerSlotStatus.FAILED
        terminal_reason = "job_wrapper_error"
        result: Any | None = None
        try:
            result = due_job.job_runner()
        except Exception as exc:  # noqa: BLE001 - wrapper failures need scheduler-level alerts.
            LOGGER.exception("scheduler job wrapper failed job=%s slot=%s", due_job.name, due_job.slot_id)
            self._mark_slot_status_safely(
                due_job,
                decision,
                status=SchedulerSlotStatus.FAILED,
                reason=terminal_reason,
                trace_id=trace_id,
                details={"error_type": exc.__class__.__name__, "error_message": str(exc)},
            )
            self._send_scheduler_system_alert(
                trace_id=trace_id,
                job_name=due_job.name,
                summary="Scheduler job wrapper failed before it could return a safe result.",
                error=exc,
                details=_slot_decision_details(due_job, decision, action="failed"),
            )
            return self._release_and_return_record(
                due_job,
                decision,
                trace_id=trace_id,
                status=SchedulerSlotStatus.FAILED,
                message=str(exc),
                result=None,
                details={"job_wrapper_error": True},
            )

        terminal_status, terminal_reason = _classify_job_result(due_job.name, result)
        post_collect_details: dict[str, object] = {}
        if terminal_status == SchedulerSlotStatus.COMPLETED:
            marker_written = self._mark_slot_completed_safely(
                due_job,
                decision,
                result=result,
                trace_id=trace_id,
            )
            if not marker_written:
                terminal_status = SchedulerSlotStatus.FAILED
                terminal_reason = "completed_marker_write_failed"
                self._mark_slot_status_safely(
                    due_job,
                    decision,
                    status=terminal_status,
                    reason=terminal_reason,
                    trace_id=trace_id,
                    details=_result_details(result),
                )
            else:
                post_collect_details = self._run_strategy_signal_post_collect_if_needed(
                    due_job,
                    result=result,
                    trace_id=trace_id,
                    current_time_utc=current_time_utc,
                )
        else:
            self._mark_slot_status_safely(
                due_job,
                decision,
                status=terminal_status,
                reason=terminal_reason,
                trace_id=trace_id,
                details=_result_details(result),
            )

        LOGGER.info(
            "scheduler slot terminal job=%s slot=%s status=%s running_key=%s completed_key=%s status_key=%s reason=%s",
            due_job.name,
            due_job.slot_id,
            terminal_status.value,
            decision.running_key,
            decision.completed_key,
            decision.status_key,
            terminal_reason,
        )
        return self._release_and_return_record(
            due_job,
            decision,
            trace_id=trace_id,
            status=terminal_status,
            message=f"scheduler slot {terminal_status.value}",
            result=result,
            details={
                "terminal_reason": terminal_reason,
                **_result_details(result),
                **post_collect_details,
            },
        )

    def _run_strategy_signal_post_collect_if_needed(
        self,
        due_job: DueSchedulerJob,
        *,
        result: Any,
        trace_id: str,
        current_time_utc: datetime,
    ) -> dict[str, object]:
        """Run the stage-17 post-collector hook only after collector success."""

        if due_job.name not in {KLINE_4H_INCREMENTAL_JOB_NAME, KLINE_1D_INCREMENTAL_JOB_NAME}:
            return {}
        if _result_status_text(result) != "success":
            return {}
        if not self.config.strategy_signal_scheduler_enabled:
            return {"strategy_signal_scheduler": {"status": "disabled"}}
        try:
            scheduler_result = self.strategy_signal_after_collect_job(
                upstream_job_name=due_job.name,
                upstream_result=result,
                current_time_utc=current_time_utc,
                settings=self.settings,
                config=self.config,
            )
            return {"strategy_signal_scheduler": _strategy_scheduler_result_details(scheduler_result)}
        except Exception as exc:  # noqa: BLE001 - post-hook failures must not rewrite collector result.
            LOGGER.exception("strategy signal scheduler post-collect hook failed job=%s", due_job.name)
            self._send_scheduler_system_alert(
                trace_id=trace_id,
                job_name=due_job.name,
                summary="Strategy signal scheduler post-collector hook failed.",
                error=exc,
                details={"slot": due_job.slot_id, "slot_time_utc": due_job.slot_time_utc.isoformat()},
            )
            return {
                "strategy_signal_scheduler": {
                    "status": "failed",
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                }
            }

    def _release_and_return_record(
        self,
        due_job: DueSchedulerJob,
        decision: SchedulerSlotDecision,
        *,
        trace_id: str,
        status: SchedulerSlotStatus,
        message: str,
        result: Any | None,
        details: dict[str, object],
    ) -> SchedulerRunRecord:
        release_ok = self._release_running_lock_safely(due_job, decision, trace_id=trace_id)
        return SchedulerRunRecord(
            job_name=due_job.name,
            status=status.value,
            slot_key=decision.running_key,
            trace_id=trace_id,
            message=message,
            result=result,
            details={
                **_slot_decision_details(due_job, decision, action=status.value),
                **details,
                "running_lock_released": release_ok,
            },
        )

    def _mark_slot_completed_safely(
        self,
        due_job: DueSchedulerJob,
        decision: SchedulerSlotDecision,
        *,
        result: Any,
        trace_id: str,
    ) -> bool:
        try:
            self.slot_store.mark_slot_completed(
                job=due_job.name,
                slot=due_job.slot_id,
                owner=decision.owner,
                completed_ttl_seconds=self.config.completed_marker_ttl_seconds,
                result_status=_result_status_text(result),
                details=_result_details(result),
            )
            return True
        except RedisError as exc:
            LOGGER.exception("scheduler completed marker write failed job=%s slot=%s", due_job.name, due_job.slot_id)
            self._send_scheduler_system_alert(
                trace_id=trace_id,
                job_name=due_job.name,
                summary="Scheduler job finished but completed marker could not be written.",
                error=exc,
                details=_slot_decision_details(due_job, decision, action="completed_marker_failed"),
            )
            return False

    def _mark_slot_status_safely(
        self,
        due_job: DueSchedulerJob,
        decision: SchedulerSlotDecision,
        *,
        status: SchedulerSlotStatus,
        reason: str,
        trace_id: str,
        details: Mapping[str, object],
    ) -> bool:
        try:
            self.slot_store.mark_slot_status(
                job=due_job.name,
                slot=due_job.slot_id,
                status=status,
                owner=decision.owner,
                reason=reason,
                ttl_seconds=self.config.status_marker_ttl_seconds,
                details=details,
            )
            return True
        except RedisError as exc:
            LOGGER.exception("scheduler status marker write failed job=%s slot=%s", due_job.name, due_job.slot_id)
            self._send_scheduler_system_alert(
                trace_id=trace_id,
                job_name=due_job.name,
                summary="Scheduler terminal status marker could not be written.",
                error=exc,
                details=_slot_decision_details(due_job, decision, action=f"{status.value}_marker_failed"),
            )
            return False

    def _release_running_lock_safely(
        self,
        due_job: DueSchedulerJob,
        decision: SchedulerSlotDecision,
        *,
        trace_id: str,
    ) -> bool:
        if not decision.running_value:
            return False
        try:
            return self.slot_store.release_running_lock(
                running_key=decision.running_key,
                running_value=decision.running_value,
            )
        except RedisError as exc:
            LOGGER.exception("scheduler running lock release failed job=%s slot=%s", due_job.name, due_job.slot_id)
            self._send_scheduler_system_alert(
                trace_id=trace_id,
                job_name=due_job.name,
                summary="Scheduler running lock could not be released; TTL remains the safety net.",
                error=exc,
                details=_slot_decision_details(due_job, decision, action="running_lock_release_failed"),
            )
            return False

    def _log_slot_skip_once(
        self,
        due_job: DueSchedulerJob,
        decision: SchedulerSlotDecision,
        *,
        current_time_utc: datetime,
    ) -> None:
        if not self._slot_log_throttle.should_emit(
            job=due_job.name,
            slot=due_job.slot_id,
            reason=decision.reason,
            current_time_utc=current_time_utc,
        ):
            return
        lock = decision.existing_lock
        LOGGER.info(
            (
                "scheduler slot state job=%s slot=%s lock_key=%s completed_key=%s "
                "status_key=%s lock_status=%s ttl=%s owner=%s created_at_utc=%s action=skip reason=%s"
            ),
            due_job.name,
            due_job.slot_id,
            decision.running_key,
            decision.completed_key,
            decision.status_key,
            decision.status.value,
            decision.ttl_seconds,
            lock.owner if lock else decision.details.get("owner", ""),
            lock.created_at_utc if lock else decision.details.get("created_at_utc", ""),
            decision.reason,
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
    slot_store: RedisSchedulerSlotStore | None = None,
) -> None:
    """Build dependencies and run the long-lived scheduler loop."""

    active_settings = settings or get_settings()
    active_config = config or build_scheduler_runtime_config(active_settings)
    active_slot_store = slot_store or RedisSchedulerSlotStore()
    runner = SchedulerRunner(
        config=active_config,
        slot_store=active_slot_store,
        settings=active_settings,
    )
    runner.run_forever()


def _default_kline_4h_job() -> Any:
    from app.scheduler.jobs.kline_4h_incremental_collect import run_kline_4h_incremental_collect_job

    return run_kline_4h_incremental_collect_job()


def _default_kline_1d_job() -> Any:
    from app.scheduler.jobs.kline_1d_incremental_collect import run_kline_1d_incremental_collect_job

    return run_kline_1d_incremental_collect_job()


def _default_kline_1d_integrity_job() -> Any:
    from app.scheduler.jobs.kline_1d_integrity_check import run_kline_1d_integrity_check_job

    return run_kline_1d_integrity_check_job()


def _default_daily_integrity_job() -> Any:
    from app.scheduler.jobs.daily_kline_integrity_check import run_daily_kline_integrity_check_job

    return run_daily_kline_integrity_check_job()


def _default_strategy_signal_after_collect_job(*args: Any, **kwargs: Any) -> Any:
    from app.scheduler.jobs.strategy_signal_scheduler_job import run_strategy_signal_scheduler_after_collect_job

    return run_strategy_signal_scheduler_after_collect_job(*args, **kwargs)


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
    return _due_daily_utc_time_slot(
        current_time_utc,
        config.daily_kline_integrity_utc_time,
        catch_up_window=DAILY_INTEGRITY_CATCH_UP_WINDOW,
    )


def _due_daily_utc_time_slot(
    current_time_utc: datetime,
    scheduled_utc_time: Any,
    *,
    catch_up_window: timedelta,
) -> datetime | None:
    scheduled = current_time_utc.replace(
        hour=scheduled_utc_time.hour,
        minute=scheduled_utc_time.minute,
        second=0,
        microsecond=0,
    )
    if current_time_utc < scheduled:
        scheduled -= timedelta(days=1)
    if _is_inside_catch_up_window(current_time_utc, scheduled, catch_up_window=catch_up_window):
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


class SchedulerSlotLogThrottle:
    """In-memory cooldown for repeated scheduler slot skip logs.

    Parameters: cooldown seconds. Return value: `should_emit()` returns whether
    the caller should write a diagnostic log line. Failure scenarios: none.
    External effects: none. This does not suppress collector_event_log records,
    Hermes alerts, Redis markers, Kline writes, or data-quality checks.
    """

    def __init__(self, cooldown_seconds: int) -> None:
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self._last_emitted: dict[tuple[str, str, str], datetime] = {}

    def should_emit(
        self,
        *,
        job: str,
        slot: str,
        reason: str,
        current_time_utc: datetime,
    ) -> bool:
        """Return True only once per job + slot + reason inside cooldown."""

        active_now = _ensure_utc_aware(current_time_utc)
        key = (job, slot, reason)
        last_emitted_at = self._last_emitted.get(key)
        if last_emitted_at is None:
            self._last_emitted[key] = active_now
            return True
        if (active_now - last_emitted_at).total_seconds() >= self.cooldown_seconds:
            self._last_emitted[key] = active_now
            return True
        return False


def _classify_job_result(job_name: str, result: Any) -> tuple[SchedulerSlotStatus, str]:
    result_status = _result_status_text(result)
    alert_status = str(getattr(result, "alert_status", "") or "")
    exit_code = getattr(result, "exit_code", None)

    if job_name == KLINE_4H_INCREMENTAL_JOB_NAME:
        if result_status == "success":
            return SchedulerSlotStatus.COMPLETED, "kline_incremental_success"
        if result_status == "blocked":
            return SchedulerSlotStatus.BLOCKED, "kline_incremental_blocked"
        if result_status == "skipped":
            return SchedulerSlotStatus.SKIPPED, "kline_incremental_skipped"
        return SchedulerSlotStatus.FAILED, "kline_incremental_failed"

    if job_name == KLINE_1D_INCREMENTAL_JOB_NAME:
        if result_status == "success":
            return SchedulerSlotStatus.COMPLETED, "kline_1d_incremental_success"
        if result_status == "blocked":
            return SchedulerSlotStatus.BLOCKED, "kline_1d_incremental_blocked"
        if result_status == "skipped":
            return SchedulerSlotStatus.SKIPPED, "kline_1d_incremental_skipped"
        return SchedulerSlotStatus.FAILED, "kline_1d_incremental_failed"

    if job_name == KLINE_1D_INTEGRITY_JOB_NAME:
        if alert_status in {"failed", "submit_failed", "gateway_rejected"}:
            return SchedulerSlotStatus.FAILED, "kline_1d_integrity_alert_failed"
        if result_status == "healthy":
            return SchedulerSlotStatus.COMPLETED, "kline_1d_integrity_healthy"
        if result_status in {"warning", "failed", "blocked"}:
            return SchedulerSlotStatus.COMPLETED, f"kline_1d_integrity_{result_status}_completed"
        if result_status == "skipped":
            return SchedulerSlotStatus.SKIPPED, "kline_1d_integrity_skipped"
        if exit_code not in (None, 0):
            return SchedulerSlotStatus.FAILED, "kline_1d_integrity_failed"
        return SchedulerSlotStatus.COMPLETED, "kline_1d_integrity_completed"

    if job_name == DAILY_KLINE_INTEGRITY_JOB_NAME:
        report_status = str(getattr(result, "details", {}).get("report_status", "") or "")
        if alert_status == "failed":
            return SchedulerSlotStatus.FAILED, "daily_integrity_alert_failed"
        if result_status == "healthy" or report_status == "healthy":
            return SchedulerSlotStatus.COMPLETED, "daily_integrity_healthy"
        if result_status == "failed" and report_status == "unhealthy":
            return SchedulerSlotStatus.COMPLETED, "daily_integrity_unhealthy_completed"
        if result_status == "skipped" or report_status == "skipped":
            return SchedulerSlotStatus.SKIPPED, "daily_integrity_skipped"
        if exit_code not in (None, 0):
            return SchedulerSlotStatus.FAILED, "daily_integrity_failed"
        return SchedulerSlotStatus.COMPLETED, "daily_integrity_completed"

    return SchedulerSlotStatus.FAILED, "unknown_scheduler_job_result"


def _result_status_text(result: Any) -> str:
    status = getattr(result, "status", "")
    return str(getattr(status, "value", status))


def _result_details(result: Any) -> dict[str, object]:
    details = getattr(result, "details", {}) or {}
    if not isinstance(details, Mapping):
        details = {"result_details": str(details)}
    return {
        "result_status": _result_status_text(result),
        "exit_code": getattr(result, "exit_code", None),
        "result_trace_id": getattr(result, "trace_id", ""),
        "alert_status": getattr(result, "alert_status", ""),
        **dict(details),
    }


def _strategy_scheduler_result_details(result: Any) -> dict[str, object]:
    status = getattr(result, "status", "")
    hermes_status = getattr(result, "hermes_status", "")
    return {
        "status": str(getattr(status, "value", status)),
        "event_id": getattr(result, "event_id", None),
        "run_id": getattr(result, "run_id", None),
        "snapshot_id": getattr(result, "snapshot_id", None),
        "target_base_open_time_ms": getattr(result, "target_base_open_time_ms", None),
        "strategy_count": getattr(result, "strategy_count", 0),
        "success_count": getattr(result, "success_count", 0),
        "failed_count": getattr(result, "failed_count", 0),
        "invalid_count": getattr(result, "invalid_count", 0),
        "not_implemented_count": getattr(result, "not_implemented_count", 0),
        "hermes_status": str(getattr(hermes_status, "value", hermes_status)),
        "message": getattr(result, "message", ""),
    }


def _slot_decision_details(
    due_job: DueSchedulerJob,
    decision: SchedulerSlotDecision,
    *,
    action: str,
) -> dict[str, object]:
    lock = decision.existing_lock
    return {
        "slot": due_job.slot_id,
        "slot_time_utc": due_job.slot_time_utc.isoformat(),
        "running_key": decision.running_key,
        "completed_key": decision.completed_key,
        "status_key": decision.status_key,
        "lock_status": decision.status.value,
        "ttl_seconds": decision.ttl_seconds,
        "owner": lock.owner if lock else decision.owner,
        "created_at_utc": lock.created_at_utc if lock else "",
        "action": action,
        "reason": decision.reason,
    }


__all__ = [
    "SchedulerRunRecord",
    "SchedulerRunner",
    "SchedulerSlotLogThrottle",
    "run_scheduler_forever",
]
