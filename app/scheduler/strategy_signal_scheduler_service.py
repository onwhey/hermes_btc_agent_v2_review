"""Stage-17 scheduler orchestration for strategy signal runs.

Call chain:
app/scheduler/runner.py::SchedulerRunner._run_strategy_signal_post_collect_if_needed
    -> app/scheduler/jobs/strategy_signal_scheduler_job.py::run_strategy_signal_scheduler_after_collect_job
    -> app/scheduler/strategy_signal_scheduler_service.py::run_after_collector_success
    -> app/strategy/signal_service.py::StrategySignalService.run_strategy_signals
    -> app/strategy/result_repository.py::create_strategy_signal_run_with_results

This file belongs to `app/scheduler`. It records one scheduler event for the
upstream collector slot's target 4h Kline and, when allowed, calls only the stage-16
StrategySignalService. It does not call scripts, does not call the stage-15
MarketContextSnapshot service directly, does not request Binance REST or
WebSocket, does not modify formal Kline tables, does not call DeepSeek or any
large language model, does not generate final trading advice, and does not
perform trading.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSendStatus, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.core.time_utils import (
    UTC,
    format_datetime_with_timezone,
    now_utc,
    timestamp_ms_to_utc_datetime,
    utc_aware_to_prc_aware,
    utc_datetime_to_timestamp_ms,
)
from app.market_data.kline_constants import (
    KLINE_1D_INTERVAL_MS,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_MS,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_SCHEDULER,
)
from app.scheduler.config import SchedulerRuntimeConfig, build_scheduler_runtime_config
from app.scheduler.slot_state import KLINE_1D_INCREMENTAL_JOB_NAME, KLINE_4H_INCREMENTAL_JOB_NAME
from app.scheduler.strategy_signal_scheduler_types import (
    STRATEGY_SIGNAL_TRIGGER_REASON_1D_SUCCESS,
    STRATEGY_SIGNAL_TRIGGER_REASON_4H_SUCCESS,
    StrategySignalSchedulerEventPayload,
    StrategySignalSchedulerHermesStatus,
    StrategySignalSchedulerRequest,
    StrategySignalSchedulerResult,
    StrategySignalSchedulerStatus,
)
from app.strategy.scheduler_event_repository import (
    create_default_strategy_signal_scheduler_event_repository,
)
from app.strategy.signal_service import StrategySignalService, create_default_strategy_signal_service
from app.strategy.types import StrategyRunStatus, StrategySignalRunRequest, StrategySignalRunResult


class StrategySignalSchedulerService:
    """Coordinate stage-17 scheduler events around stage-16 strategy signals.

    Parameters: scheduler config, settings, repository, strategy service, and
    alert sender are injectable for tests.
    Return value: service instance.
    Failure scenarios: duplicate target events are skipped; strategy blocked
    and failed results are recorded in the event log; Hermes failures are
    recorded without changing the strategy status.
    External service access: only optional Hermes via `app/alerting`; strategy
    execution itself is delegated to stage 16.
    Data impact: writes `strategy_signal_scheduler_event_log`; stage 16 may
    write strategy signal tables and may lazily create a MarketContextSnapshot
    only because the request is non-dry-run confirm-write. Formal Kline tables
    are never modified here.
    """

    def __init__(
        self,
        *,
        config: SchedulerRuntimeConfig | None = None,
        settings: AppSettings | None = None,
        event_repository: Any | None = None,
        strategy_signal_service: StrategySignalService | Any | None = None,
        alert_sender: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._config = config or build_scheduler_runtime_config(self._settings)
        self._event_repository = event_repository or create_default_strategy_signal_scheduler_event_repository()
        self._strategy_signal_service = strategy_signal_service or create_default_strategy_signal_service()
        self._alert_sender = alert_sender or _default_alert_sender

    def run_after_collector_success(
        self,
        db_session: Any,
        *,
        request: StrategySignalSchedulerRequest,
    ) -> StrategySignalSchedulerResult:
        """Handle one successful upstream collector event.

        Parameters: caller-owned MySQL session and a post-collector request.
        Return value: compact scheduler result.
        Failure scenarios: invalid upstream job names return skipped; repository
        and strategy exceptions become failed scheduler events when an event row
        exists.
        External effects: may write scheduler event rows and may send one
        Hermes notification according to config. It never calls scripts or
        requests market data.
        """

        active_now = _ensure_utc(request.current_time_utc)
        trace_id = request.trace_id or request.upstream_trace_id or uuid.uuid4().hex
        target = _build_target_context_from_upstream(
            request=request,
            current_time_utc=active_now,
            config=self._config,
        )
        trigger_reason = _trigger_reason_for_upstream_job(request.upstream_job_name)
        if trigger_reason is None:
            return StrategySignalSchedulerResult(
                status=StrategySignalSchedulerStatus.SKIPPED,
                event_id=None,
                trace_id=trace_id,
                message=f"unsupported upstream job for strategy signal scheduler: {request.upstream_job_name}",
                target_base_open_time_ms=target["target_base_open_time_ms"],
                hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
            )

        existing_event = self._event_repository.get_event_by_target(
            db_session,
            symbol=self._config.strategy_signal_symbol,
            base_interval=self._config.strategy_signal_base_interval,
            higher_interval=self._config.strategy_signal_higher_interval,
            target_base_open_time_ms=target["target_base_open_time_ms"],
        )

        if request.upstream_job_name == KLINE_1D_INCREMENTAL_JOB_NAME:
            return self._handle_1d_success(
                db_session,
                request=request,
                existing_event=existing_event,
                trace_id=trace_id,
                target=target,
            )

        return self._handle_4h_success(
            db_session,
            request=request,
            existing_event=existing_event,
            trace_id=trace_id,
            target=target,
            trigger_reason=trigger_reason,
        )

    def _handle_4h_success(
        self,
        db_session: Any,
        *,
        request: StrategySignalSchedulerRequest,
        existing_event: Any | None,
        trace_id: str,
        target: dict[str, Any],
        trigger_reason: str,
    ) -> StrategySignalSchedulerResult:
        if existing_event is not None:
            return self._record_duplicate_skip(
                db_session,
                event=existing_event,
                trace_id=trace_id,
                target=target,
                reason=f"existing stage-17 event status={getattr(existing_event, 'status', '')}",
            )

        if not self._config.strategy_signal_scheduler_enabled:
            event = self._create_event(
                db_session,
                status=StrategySignalSchedulerStatus.SKIPPED,
                request=request,
                trace_id=trace_id,
                target=target,
                trigger_reason=trigger_reason,
                message="Strategy signal scheduler is disabled by configuration.",
                error_code="strategy_signal_scheduler_disabled",
                started_at_utc=now_utc(),
                finished_at_utc=now_utc(),
            )
            _commit_if_possible(db_session)
            return self._record_hermes_and_build_result(
                db_session,
                event=event,
                status=StrategySignalSchedulerStatus.SKIPPED,
                trace_id=trace_id,
                target=target,
                message=str(event.message or ""),
                strategy_result=None,
                force_not_required=not self._config.strategy_signal_hermes_notify_skipped,
            )

        if _is_utc_midnight_close_boundary(target["target_base_close_time_utc"]):
            event = self._create_event(
                db_session,
                status=StrategySignalSchedulerStatus.WAITING_UPSTREAM,
                request=request,
                trace_id=trace_id,
                target=target,
                trigger_reason=trigger_reason,
                message="UTC 00:00 close boundary reached; waiting for 1d incremental collector success.",
            )
            _commit_if_possible(db_session)
            self._event_repository.record_hermes_result(
                db_session,
                event,
                hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED.value,
                hermes_message=None,
                hermes_error=None,
                hermes_sent_at_utc=None,
            )
            _commit_if_possible(db_session)
            return _build_scheduler_result_from_event(
                event,
                status=StrategySignalSchedulerStatus.WAITING_UPSTREAM,
                trace_id=trace_id,
                target=target,
                message=str(event.message or ""),
                hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
            )

        event = self._create_event(
            db_session,
            status=StrategySignalSchedulerStatus.RUNNING,
            request=request,
            trace_id=trace_id,
            target=target,
            trigger_reason=trigger_reason,
            message="4h collector succeeded; scheduler is calling stage-16 StrategySignalService.",
            started_at_utc=now_utc(),
        )
        _commit_if_possible(db_session)
        return self._run_stage16_and_finalize_event(db_session, event=event, trace_id=trace_id, target=target)

    def _handle_1d_success(
        self,
        db_session: Any,
        *,
        request: StrategySignalSchedulerRequest,
        existing_event: Any | None,
        trace_id: str,
        target: dict[str, Any],
    ) -> StrategySignalSchedulerResult:
        if not _is_utc_midnight_close_boundary(target["target_base_close_time_utc"]):
            return StrategySignalSchedulerResult(
                status=StrategySignalSchedulerStatus.SKIPPED,
                event_id=None,
                trace_id=trace_id,
                message="1d collector success is only a stage-17 trigger at the UTC 00:00 close boundary.",
                target_base_open_time_ms=target["target_base_open_time_ms"],
                hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
            )
        if existing_event is None:
            return StrategySignalSchedulerResult(
                status=StrategySignalSchedulerStatus.SKIPPED,
                event_id=None,
                trace_id=trace_id,
                message="1d collector succeeded but no waiting 4h scheduler event exists for the latest target.",
                target_base_open_time_ms=target["target_base_open_time_ms"],
                hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
            )
        if getattr(existing_event, "status", "") != StrategySignalSchedulerStatus.WAITING_UPSTREAM.value:
            return self._record_duplicate_skip(
                db_session,
                event=existing_event,
                trace_id=trace_id,
                target=target,
                reason=f"1d trigger skipped because existing status={getattr(existing_event, 'status', '')}",
            )
        if not self._config.strategy_signal_scheduler_enabled:
            self._event_repository.mark_event_completed_from_strategy_result(
                db_session,
                existing_event,
                status=StrategySignalSchedulerStatus.SKIPPED.value,
                run_id=None,
                snapshot_id=None,
                strategy_count=0,
                success_count=0,
                failed_count=0,
                invalid_count=0,
                not_implemented_count=0,
                message="Strategy signal scheduler is disabled by configuration.",
                error_code="strategy_signal_scheduler_disabled",
                error_message=None,
            )
            _commit_if_possible(db_session)
            return self._record_hermes_and_build_result(
                db_session,
                event=existing_event,
                status=StrategySignalSchedulerStatus.SKIPPED,
                trace_id=trace_id,
                target=target,
                message=str(getattr(existing_event, "message", "") or ""),
                strategy_result=None,
                force_not_required=not self._config.strategy_signal_hermes_notify_skipped,
            )

        self._event_repository.mark_event_running(
            db_session,
            existing_event,
            message="1d collector succeeded; scheduler is calling stage-16 StrategySignalService.",
            upstream_1d_collector_event_id=request.upstream_collector_event_id,
        )
        _commit_if_possible(db_session)
        return self._run_stage16_and_finalize_event(
            db_session,
            event=existing_event,
            trace_id=trace_id,
            target=target,
        )

    def _create_event(
        self,
        db_session: Any,
        *,
        status: StrategySignalSchedulerStatus,
        request: StrategySignalSchedulerRequest,
        trace_id: str,
        target: dict[str, Any],
        trigger_reason: str,
        message: str,
        error_code: str | None = None,
        error_message: str | None = None,
        started_at_utc: datetime | None = None,
        finished_at_utc: datetime | None = None,
    ) -> Any:
        payload = StrategySignalSchedulerEventPayload(
            event_id=_build_event_id(
                symbol=self._config.strategy_signal_symbol,
                base_interval=self._config.strategy_signal_base_interval,
                higher_interval=self._config.strategy_signal_higher_interval,
                target_base_close_time_utc=target["target_base_close_time_utc"],
                trace_id=trace_id,
            ),
            symbol=self._config.strategy_signal_symbol,
            base_interval=self._config.strategy_signal_base_interval,
            higher_interval=self._config.strategy_signal_higher_interval,
            target_base_open_time_ms=target["target_base_open_time_ms"],
            target_base_open_time_utc=target["target_base_open_time_utc"],
            target_base_close_time_ms=target["target_base_close_time_ms"],
            target_base_close_time_utc=target["target_base_close_time_utc"],
            target_higher_open_time_ms=target["target_higher_open_time_ms"],
            target_higher_open_time_utc=target["target_higher_open_time_utc"],
            status=status.value,
            trigger_source=TRIGGER_SOURCE_SCHEDULER,
            trigger_reason=trigger_reason,
            upstream_4h_collector_event_id=(
                request.upstream_collector_event_id
                if request.upstream_job_name == KLINE_4H_INCREMENTAL_JOB_NAME
                else None
            ),
            upstream_1d_collector_event_id=(
                request.upstream_collector_event_id
                if request.upstream_job_name == KLINE_1D_INCREMENTAL_JOB_NAME
                else None
            ),
            message=message,
            error_code=error_code,
            error_message=error_message,
            trace_id=trace_id,
            hermes_enabled=self._config.strategy_signal_hermes_enabled,
            hermes_status=None,
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
        )
        return self._event_repository.create_scheduler_event(db_session, payload=payload)

    def _record_duplicate_skip(
        self,
        db_session: Any,
        *,
        event: Any,
        trace_id: str,
        target: dict[str, Any],
        reason: str,
    ) -> StrategySignalSchedulerResult:
        self._event_repository.mark_duplicate_skipped(db_session, event, reason=reason)
        _commit_if_possible(db_session)
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SKIPPED,
            event_id=getattr(event, "event_id", None),
            trace_id=trace_id,
            message="Stage-17 strategy signal scheduler skipped a duplicate target event.",
            target_base_open_time_ms=target["target_base_open_time_ms"],
            run_id=getattr(event, "run_id", None),
            snapshot_id=getattr(event, "snapshot_id", None),
            strategy_count=int(getattr(event, "strategy_count", 0) or 0),
            success_count=int(getattr(event, "success_count", 0) or 0),
            failed_count=int(getattr(event, "failed_count", 0) or 0),
            invalid_count=int(getattr(event, "invalid_count", 0) or 0),
            not_implemented_count=int(getattr(event, "not_implemented_count", 0) or 0),
            hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
            details={"duplicate_skip_reason": reason},
        )

    def _run_stage16_and_finalize_event(
        self,
        db_session: Any,
        *,
        event: Any,
        trace_id: str,
        target: dict[str, Any],
    ) -> StrategySignalSchedulerResult:
        try:
            strategy_result = self._call_strategy_signal_service(
                db_session,
                trace_id=trace_id,
                current_time_ms=target["current_time_ms"],
            )
        except Exception as exc:  # noqa: BLE001 - scheduler event must capture stage-16 boundary failures.
            _rollback_if_possible(db_session)
            self._event_repository.mark_event_completed_from_strategy_result(
                db_session,
                event,
                status=StrategySignalSchedulerStatus.FAILED.value,
                run_id=None,
                snapshot_id=None,
                strategy_count=0,
                success_count=0,
                failed_count=0,
                invalid_count=0,
                not_implemented_count=0,
                message="Stage-16 StrategySignalService raised before returning a structured result.",
                error_code="strategy_signal_service_exception",
                error_message=str(exc),
            )
            _commit_if_possible(db_session)
            return self._record_hermes_and_build_result(
                db_session,
                event=event,
                status=StrategySignalSchedulerStatus.FAILED,
                trace_id=trace_id,
                target=target,
                message=str(getattr(event, "message", "") or ""),
                strategy_result=None,
                error_message=str(exc),
            )

        event_status, error_code = _event_status_from_strategy_result(strategy_result)
        self._event_repository.mark_event_completed_from_strategy_result(
            db_session,
            event,
            status=event_status.value,
            run_id=strategy_result.run_id,
            snapshot_id=strategy_result.snapshot_id,
            strategy_count=strategy_result.strategy_count,
            success_count=strategy_result.success_count,
            failed_count=strategy_result.failed_count,
            invalid_count=strategy_result.invalid_count,
            not_implemented_count=strategy_result.not_implemented_count,
            message=strategy_result.message,
            error_code=error_code,
            error_message=strategy_result.error_message,
        )
        _commit_if_possible(db_session)
        return self._record_hermes_and_build_result(
            db_session,
            event=event,
            status=event_status,
            trace_id=trace_id,
            target=target,
            message=strategy_result.message,
            strategy_result=strategy_result,
            error_message=strategy_result.error_message,
        )

    def _call_strategy_signal_service(
        self,
        db_session: Any,
        *,
        trace_id: str,
        current_time_ms: int,
    ) -> StrategySignalRunResult:
        request = StrategySignalRunRequest(
            symbol=self._config.strategy_signal_symbol,
            base_interval_value=self._config.strategy_signal_base_interval,
            higher_interval_value=self._config.strategy_signal_higher_interval,
            lookback_base_count=self._settings.market_context_4h_lookback_count,
            lookback_higher_count=self._settings.market_context_1d_lookback_count,
            trigger_source=TRIGGER_SOURCE_SCHEDULER,
            ensure_latest_snapshot=True,
            dry_run=False,
            confirm_write=True,
            created_by="strategy_signal_scheduler",
            current_time_ms=current_time_ms,
            trace_id=trace_id,
        )
        if hasattr(self._strategy_signal_service, "run_strategy_signals"):
            return self._strategy_signal_service.run_strategy_signals(db_session, request=request)
        return self._strategy_signal_service(db_session=db_session, request=request)

    def _record_hermes_and_build_result(
        self,
        db_session: Any,
        *,
        event: Any,
        status: StrategySignalSchedulerStatus,
        trace_id: str,
        target: dict[str, Any],
        message: str,
        strategy_result: StrategySignalRunResult | None,
        error_message: str | None = None,
        force_not_required: bool = False,
    ) -> StrategySignalSchedulerResult:
        hermes_status, hermes_message, hermes_error, hermes_sent_at_utc = self._send_or_skip_hermes(
            event=event,
            status=status,
            trace_id=trace_id,
            target=target,
            strategy_result=strategy_result,
            error_message=error_message,
            force_not_required=force_not_required,
        )
        self._event_repository.record_hermes_result(
            db_session,
            event,
            hermes_status=hermes_status.value,
            hermes_message=hermes_message,
            hermes_error=hermes_error,
            hermes_sent_at_utc=hermes_sent_at_utc,
        )
        _commit_if_possible(db_session)
        return _build_scheduler_result_from_event(
            event,
            status=status,
            trace_id=trace_id,
            target=target,
            message=message,
            hermes_status=hermes_status,
            error_message=error_message,
        )

    def _send_or_skip_hermes(
        self,
        *,
        event: Any,
        status: StrategySignalSchedulerStatus,
        trace_id: str,
        target: dict[str, Any],
        strategy_result: StrategySignalRunResult | None,
        error_message: str | None,
        force_not_required: bool,
    ) -> tuple[StrategySignalSchedulerHermesStatus, str | None, str | None, datetime | None]:
        if not self._config.strategy_signal_hermes_enabled:
            return StrategySignalSchedulerHermesStatus.DISABLED, None, None, None
        if force_not_required or not _should_notify_status(self._config, status):
            return StrategySignalSchedulerHermesStatus.NOT_REQUIRED, None, None, None

        visible_body = _build_strategy_signal_visible_body(
            event=event,
            status=status,
            target=target,
            strategy_result=strategy_result,
            error_message=error_message,
        )
        alert_event = AlertEvent(
            alert_type=AlertType.STRATEGY_SIGNAL_SCHEDULER,
            severity=_alert_severity_for_status(status),
            title=_alert_title_for_status(status),
            summary=_alert_title_for_status(status),
            details={
                WECHAT_VISIBLE_BODY_DETAIL_KEY: visible_body,
                "event_id": getattr(event, "event_id", ""),
                "run_id": getattr(event, "run_id", "") or "",
                "snapshot_id": getattr(event, "snapshot_id", "") or "",
                "status": status.value,
                "trace_id": trace_id,
                "independent_strategy_signal_only": True,
                "no_large_model_call": True,
                "no_auto_trading": True,
            },
            source="app.scheduler.strategy_signal_scheduler_service",
            trace_id=trace_id,
        )
        try:
            send_result = self._alert_sender(
                alert_event,
                settings=self._settings,
                send_real_alert=True,
            )
        except Exception as exc:  # noqa: BLE001 - notification failure must not change strategy status.
            return StrategySignalSchedulerHermesStatus.FAILED, visible_body, str(exc), None

        if getattr(send_result, "status", None) == AlertSendStatus.SUBMITTED_TO_HERMES:
            sent_at_utc = getattr(send_result, "submitted_at_utc", None) or now_utc()
            return StrategySignalSchedulerHermesStatus.SENT, visible_body, None, sent_at_utc
        return (
            StrategySignalSchedulerHermesStatus.FAILED,
            visible_body,
            getattr(send_result, "error_message", "") or getattr(send_result, "message", "") or "Hermes not sent",
            None,
        )


def run_strategy_signal_scheduler_after_collect(
    *,
    db_session: Any,
    request: StrategySignalSchedulerRequest,
    service: StrategySignalSchedulerService | None = None,
) -> StrategySignalSchedulerResult:
    """Convenience app-service function used by the scheduler job wrapper."""

    active_service = service or StrategySignalSchedulerService()
    return active_service.run_after_collector_success(db_session, request=request)


def _build_target_context_from_upstream(
    *,
    request: StrategySignalSchedulerRequest,
    current_time_utc: datetime,
    config: SchedulerRuntimeConfig,
) -> dict[str, Any]:
    """Build the stage-17 target from the upstream collector identity.

    The scheduler run time is useful for audit and stage-16 freshness checks,
    but it is not stable enough to identify the Kline that the upstream
    collector slot represented. Target identity therefore comes from an
    explicit 4h collector open time when present, otherwise from the upstream
    scheduler slot. This helper is read-only: it does not query Binance, does
    not query formal Kline tables, and cannot repair or backfill data.
    """

    current_time_ms = utc_datetime_to_timestamp_ms(current_time_utc)
    slot_time_utc = _ensure_utc(request.upstream_slot_time_utc)
    explicit_open_time_ms = _explicit_base_open_time_ms_from_request(request)

    if explicit_open_time_ms is not None:
        target_base_open_time_ms = explicit_open_time_ms
        target_base_close_time_ms = explicit_open_time_ms + KLINE_4H_INTERVAL_MS
        target_source = "upstream_collector_result"
    else:
        target_base_close_time_utc = _target_base_close_time_from_slot(
            upstream_job_name=request.upstream_job_name,
            upstream_slot_time_utc=slot_time_utc,
            config=config,
        )
        target_base_close_time_ms = utc_datetime_to_timestamp_ms(target_base_close_time_utc)
        target_base_open_time_ms = target_base_close_time_ms - KLINE_4H_INTERVAL_MS
        target_source = "upstream_slot_time_utc"

    target_higher_open_time_ms = target_base_close_time_ms - KLINE_1D_INTERVAL_MS
    return {
        "current_time_ms": current_time_ms,
        "upstream_slot_time_utc": slot_time_utc,
        "target_source": target_source,
        "target_base_open_time_ms": target_base_open_time_ms,
        "target_base_open_time_utc": timestamp_ms_to_utc_datetime(target_base_open_time_ms),
        "target_base_close_time_ms": target_base_close_time_ms,
        "target_base_close_time_utc": timestamp_ms_to_utc_datetime(target_base_close_time_ms),
        "target_higher_open_time_ms": target_higher_open_time_ms,
        "target_higher_open_time_utc": timestamp_ms_to_utc_datetime(target_higher_open_time_ms),
    }


def _explicit_base_open_time_ms_from_request(request: StrategySignalSchedulerRequest) -> int | None:
    """Return explicit 4h collector open time only when it is interval-aligned."""

    if request.upstream_job_name != KLINE_4H_INCREMENTAL_JOB_NAME:
        return None
    value = request.upstream_latest_base_open_time_ms
    if value is None or value % KLINE_4H_INTERVAL_MS != 0:
        return None
    return int(value)


def _target_base_close_time_from_slot(
    *,
    upstream_job_name: str,
    upstream_slot_time_utc: datetime,
    config: SchedulerRuntimeConfig,
) -> datetime:
    """Calculate the target 4h close boundary from the upstream scheduler slot."""

    slot_time_utc = _ensure_utc(upstream_slot_time_utc)
    if upstream_job_name == KLINE_1D_INCREMENTAL_JOB_NAME:
        return slot_time_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    close_time_utc = slot_time_utc - timedelta(
        minutes=config.kline_4h_incremental_collect_utc_minutes_after_close
    )
    return close_time_utc.replace(second=0, microsecond=0)


def _trigger_reason_for_upstream_job(upstream_job_name: str) -> str | None:
    if upstream_job_name == KLINE_4H_INCREMENTAL_JOB_NAME:
        return STRATEGY_SIGNAL_TRIGGER_REASON_4H_SUCCESS
    if upstream_job_name == KLINE_1D_INCREMENTAL_JOB_NAME:
        return STRATEGY_SIGNAL_TRIGGER_REASON_1D_SUCCESS
    return None


def _is_utc_midnight_close_boundary(target_base_close_time_utc: datetime) -> bool:
    value = _ensure_utc(target_base_close_time_utc)
    return value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0


def _build_event_id(
    *,
    symbol: str,
    base_interval: str,
    higher_interval: str,
    target_base_close_time_utc: datetime,
    trace_id: str,
) -> str:
    target_text = target_base_close_time_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"SSS-{symbol}-{base_interval.upper()}-{higher_interval.upper()}-{target_text}-{trace_id[:8]}"


def _event_status_from_strategy_result(
    result: StrategySignalRunResult,
) -> tuple[StrategySignalSchedulerStatus, str | None]:
    if result.status == StrategyRunStatus.SUCCESS:
        return StrategySignalSchedulerStatus.SUCCESS, None
    if result.status == StrategyRunStatus.PARTIAL_SUCCESS:
        return StrategySignalSchedulerStatus.PARTIAL_SUCCESS, None
    if result.status == StrategyRunStatus.BLOCKED:
        return StrategySignalSchedulerStatus.BLOCKED, result.blocked_reason or "strategy_signal_blocked"
    return StrategySignalSchedulerStatus.FAILED, "strategy_signal_failed"


def _should_notify_status(config: SchedulerRuntimeConfig, status: StrategySignalSchedulerStatus) -> bool:
    if status == StrategySignalSchedulerStatus.SUCCESS:
        return config.strategy_signal_hermes_notify_success
    if status == StrategySignalSchedulerStatus.PARTIAL_SUCCESS:
        return config.strategy_signal_hermes_notify_partial_success
    if status == StrategySignalSchedulerStatus.BLOCKED:
        return config.strategy_signal_hermes_notify_blocked
    if status == StrategySignalSchedulerStatus.FAILED:
        return config.strategy_signal_hermes_notify_failed
    if status == StrategySignalSchedulerStatus.SKIPPED:
        return config.strategy_signal_hermes_notify_skipped
    return False


def _alert_severity_for_status(status: StrategySignalSchedulerStatus) -> AlertSeverity:
    if status == StrategySignalSchedulerStatus.FAILED:
        return AlertSeverity.ERROR
    if status == StrategySignalSchedulerStatus.BLOCKED:
        return AlertSeverity.WARNING
    if status == StrategySignalSchedulerStatus.PARTIAL_SUCCESS:
        return AlertSeverity.NOTICE
    return AlertSeverity.INFO


def _alert_title_for_status(status: StrategySignalSchedulerStatus) -> str:
    if status in (StrategySignalSchedulerStatus.SUCCESS, StrategySignalSchedulerStatus.PARTIAL_SUCCESS):
        return "BTC 独立策略信号已生成"
    return "BTC 策略信号调度异常"


def _build_strategy_signal_visible_body(
    *,
    event: Any,
    status: StrategySignalSchedulerStatus,
    target: dict[str, Any],
    strategy_result: StrategySignalRunResult | None,
    error_message: str | None,
) -> str:
    target_utc = format_datetime_with_timezone(target["target_base_open_time_utc"])
    target_prc = format_datetime_with_timezone(utc_aware_to_prc_aware(target["target_base_open_time_utc"]))
    header = "BTC 独立策略信号已生成" if status in {
        StrategySignalSchedulerStatus.SUCCESS,
        StrategySignalSchedulerStatus.PARTIAL_SUCCESS,
    } else "BTC 策略信号调度异常"
    lines = [
        f"【{header}】",
        f"周期：{getattr(event, 'symbol', '')} {getattr(event, 'base_interval', '')} + {getattr(event, 'higher_interval', '')}",
        f"目标K线：{target_utc} / {target_prc}",
        f"运行状态：{status.value}",
        f"策略数量：{getattr(event, 'strategy_count', 0)}",
        f"成功：{getattr(event, 'success_count', 0)}",
        f"失败：{getattr(event, 'failed_count', 0)}",
        f"无效：{getattr(event, 'invalid_count', 0)}",
        f"未实现：{getattr(event, 'not_implemented_count', 0)}",
    ]
    if strategy_result is not None and strategy_result.signals:
        lines.append("")
        for index, signal in enumerate(strategy_result.signals, start=1):
            lines.extend(
                [
                    f"{index}. {signal.strategy_name}",
                    f"状态：{signal.strategy_status.value}",
                    f"方向偏向：{signal.direction_bias.value}",
                    f"信号强度：{signal.signal_strength:.4f}",
                    f"风险等级：{signal.risk_level.value}",
                    f"理由：{_compact_text(signal.reason_text, max_length=180)}",
                ]
            )
    if status in (StrategySignalSchedulerStatus.BLOCKED, StrategySignalSchedulerStatus.FAILED):
        reason = (
            getattr(event, "error_message", None)
            or getattr(event, "error_code", None)
            or error_message
            or "unknown"
        )
        lines.append(f"原因：{_compact_text(str(reason), max_length=260)}")
    lines.extend(
        [
            f"run_id：{getattr(event, 'run_id', '') or ''}",
            f"snapshot_id：{getattr(event, 'snapshot_id', '') or ''}",
            f"event_id：{getattr(event, 'event_id', '')}",
            f"trace_id：{getattr(event, 'trace_id', '')}",
            "说明：这是独立策略信号，不是最终交易建议。",
            "本阶段未进行策略聚合，未调用大模型，系统未自动交易。",
        ]
    )
    return "\n".join(lines)


def _compact_text(value: str, *, max_length: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."


def _build_scheduler_result_from_event(
    event: Any,
    *,
    status: StrategySignalSchedulerStatus,
    trace_id: str,
    target: dict[str, Any],
    message: str,
    hermes_status: StrategySignalSchedulerHermesStatus | None,
    error_message: str | None = None,
) -> StrategySignalSchedulerResult:
    return StrategySignalSchedulerResult(
        status=status,
        event_id=getattr(event, "event_id", None),
        trace_id=trace_id,
        message=message,
        target_base_open_time_ms=target["target_base_open_time_ms"],
        run_id=getattr(event, "run_id", None),
        snapshot_id=getattr(event, "snapshot_id", None),
        strategy_count=int(getattr(event, "strategy_count", 0) or 0),
        success_count=int(getattr(event, "success_count", 0) or 0),
        failed_count=int(getattr(event, "failed_count", 0) or 0),
        invalid_count=int(getattr(event, "invalid_count", 0) or 0),
        not_implemented_count=int(getattr(event, "not_implemented_count", 0) or 0),
        hermes_status=hermes_status,
        error_message=error_message,
    )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("strategy signal scheduler time must be timezone-aware UTC")
    return value.astimezone(UTC)


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


def _default_alert_sender(*args: Any, **kwargs: Any) -> Any:
    from app.alerting.service import send_alert

    return send_alert(*args, **kwargs)


__all__ = [
    "StrategySignalSchedulerService",
    "run_strategy_signal_scheduler_after_collect",
]
