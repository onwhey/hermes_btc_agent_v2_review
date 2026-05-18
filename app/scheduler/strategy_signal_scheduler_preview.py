"""Read-only preview for the stage-17 strategy signal scheduler.

This file belongs to `app/scheduler`. It supports the manual check script by
computing the same target Kline and idempotency decision used by
`StrategySignalSchedulerService`, but it never writes scheduler events and
never calls stage 16.

Called by: `StrategySignalSchedulerService.preview_after_collector_success`.
External services: none.
Database impact: read-only lookup of `strategy_signal_scheduler_event_log`.
Redis impact: none.
Hermes impact: none.
DeepSeek impact: none.
Trading impact: none.
Formal Kline impact: never modifies `market_kline_4h` or `market_kline_1d`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.scheduler.slot_state import KLINE_1D_INCREMENTAL_JOB_NAME
from app.scheduler.strategy_signal_scheduler_service import (
    _build_target_context_from_upstream,
    _ensure_utc,
    _is_utc_midnight_close_boundary,
    _trigger_reason_for_upstream_job,
)
from app.scheduler.strategy_signal_scheduler_types import (
    StrategySignalSchedulerHermesStatus,
    StrategySignalSchedulerRequest,
    StrategySignalSchedulerResult,
    StrategySignalSchedulerStatus,
)


def preview_strategy_signal_scheduler_after_collect(
    *,
    service: Any,
    db_session: Any,
    request: StrategySignalSchedulerRequest,
) -> StrategySignalSchedulerResult:
    """Preview one simulated scheduler post-collector hook.

    Parameters: a `StrategySignalSchedulerService` instance, caller-owned DB
    session, and simulated upstream collector request.
    Return value: a `StrategySignalSchedulerResult` whose `details` contain
    `check_status` plus target UTC times.
    Failure scenarios: repository read failures propagate; no partial writes can
    be produced because this helper never calls write methods or commits.
    External effects: read-only database lookup only. It does not call stage 16,
    stage 15, Binance, Redis, Hermes, scripts, or any trading capability.
    """

    config = service._config
    event_repository = service._event_repository
    active_now = _ensure_utc(request.current_time_utc)
    trace_id = request.trace_id or request.upstream_trace_id or uuid.uuid4().hex
    target = _build_target_context_from_upstream(
        request=request,
        current_time_utc=active_now,
        config=config,
    )
    target_details = _build_target_details(target)
    trigger_reason = _trigger_reason_for_upstream_job(request.upstream_job_name)
    if trigger_reason is None:
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SKIPPED,
            event_id=None,
            trace_id=trace_id,
            message=f"unsupported upstream job for strategy signal scheduler: {request.upstream_job_name}",
            target_base_open_time_ms=target["target_base_open_time_ms"],
            hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
            details={
                **target_details,
                "check_status": "skipped",
                "check_reason": "unsupported_upstream_job",
            },
        )

    if not config.strategy_signal_scheduler_enabled:
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SKIPPED,
            event_id=None,
            trace_id=trace_id,
            message="Strategy signal scheduler is disabled by configuration.",
            target_base_open_time_ms=target["target_base_open_time_ms"],
            hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
            details={
                **target_details,
                "check_status": "disabled",
                "check_reason": "strategy_signal_scheduler_disabled",
            },
        )

    existing_event = event_repository.get_event_by_target(
        db_session,
        symbol=config.strategy_signal_symbol,
        base_interval=config.strategy_signal_base_interval,
        higher_interval=config.strategy_signal_higher_interval,
        target_base_open_time_ms=target["target_base_open_time_ms"],
    )

    if request.upstream_job_name == KLINE_1D_INCREMENTAL_JOB_NAME:
        return _preview_1d_success(
            existing_event=existing_event,
            trace_id=trace_id,
            target=target,
            target_details=target_details,
        )

    return _preview_4h_success(
        existing_event=existing_event,
        trace_id=trace_id,
        target=target,
        target_details=target_details,
    )


def _preview_4h_success(
    *,
    existing_event: Any | None,
    trace_id: str,
    target: dict[str, Any],
    target_details: dict[str, Any],
) -> StrategySignalSchedulerResult:
    if existing_event is not None:
        return _build_existing_event_preview_result(
            existing_event,
            status=StrategySignalSchedulerStatus.SKIPPED,
            trace_id=trace_id,
            target=target,
            target_details=target_details,
            check_status="skipped",
            message=f"dry-run: existing stage-17 event status={getattr(existing_event, 'status', '')}",
        )

    if _is_utc_midnight_close_boundary(target["target_base_close_time_utc"]):
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.WAITING_UPSTREAM,
            event_id=None,
            trace_id=trace_id,
            message="dry-run: 4h UTC 00:00 close would write waiting_upstream and wait for 1d.",
            target_base_open_time_ms=target["target_base_open_time_ms"],
            hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
            details={
                **target_details,
                "check_status": "would_waiting_upstream",
                "check_reason": "utc_midnight_wait_for_1d",
            },
        )

    return StrategySignalSchedulerResult(
        status=StrategySignalSchedulerStatus.RUNNING,
        event_id=None,
        trace_id=trace_id,
        message="dry-run: confirmed run would call stage-17 scheduler service and then stage 16.",
        target_base_open_time_ms=target["target_base_open_time_ms"],
        hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
        details={
            **target_details,
            "check_status": "would_trigger",
            "check_reason": "normal_4h_collector_success",
        },
    )


def _preview_1d_success(
    *,
    existing_event: Any | None,
    trace_id: str,
    target: dict[str, Any],
    target_details: dict[str, Any],
) -> StrategySignalSchedulerResult:
    if not _is_utc_midnight_close_boundary(target["target_base_close_time_utc"]):
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SKIPPED,
            event_id=None,
            trace_id=trace_id,
            message="dry-run: 1d collector only continues stage 17 at the UTC 00:00 close boundary.",
            target_base_open_time_ms=target["target_base_open_time_ms"],
            hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
            details={
                **target_details,
                "check_status": "skipped",
                "check_reason": "not_utc_midnight_boundary",
            },
        )
    if existing_event is None:
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SKIPPED,
            event_id=None,
            trace_id=trace_id,
            message="dry-run: 1d collector has no waiting 4h scheduler event to continue.",
            target_base_open_time_ms=target["target_base_open_time_ms"],
            hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
            details={
                **target_details,
                "check_status": "skipped",
                "check_reason": "missing_waiting_upstream_event",
            },
        )
    if getattr(existing_event, "status", "") != StrategySignalSchedulerStatus.WAITING_UPSTREAM.value:
        return _build_existing_event_preview_result(
            existing_event,
            status=StrategySignalSchedulerStatus.SKIPPED,
            trace_id=trace_id,
            target=target,
            target_details=target_details,
            check_status="skipped",
            message=f"dry-run: 1d trigger would skip existing status={getattr(existing_event, 'status', '')}",
        )

    return _build_existing_event_preview_result(
        existing_event,
        status=StrategySignalSchedulerStatus.RUNNING,
        trace_id=trace_id,
        target=target,
        target_details=target_details,
        check_status="would_trigger",
        message="dry-run: 1d collector would continue waiting_upstream and call stage 17 once.",
    )


def _build_existing_event_preview_result(
    event: Any,
    *,
    status: StrategySignalSchedulerStatus,
    trace_id: str,
    target: dict[str, Any],
    target_details: dict[str, Any],
    check_status: str,
    message: str,
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
        hermes_status=StrategySignalSchedulerHermesStatus.NOT_REQUIRED,
        error_message=getattr(event, "error_message", None),
        details={
            **target_details,
            "check_status": check_status,
            "check_reason": str(getattr(event, "status", "") or ""),
        },
    )


def _build_target_details(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_base_open_time_utc": target["target_base_open_time_utc"],
        "target_base_close_time_utc": target["target_base_close_time_utc"],
        "target_higher_open_time_utc": target["target_higher_open_time_utc"],
        "target_base_open_time_ms": target["target_base_open_time_ms"],
        "target_base_close_time_ms": target["target_base_close_time_ms"],
        "target_higher_open_time_ms": target["target_higher_open_time_ms"],
        "target_source": target["target_source"],
    }


__all__ = ["preview_strategy_signal_scheduler_after_collect"]
