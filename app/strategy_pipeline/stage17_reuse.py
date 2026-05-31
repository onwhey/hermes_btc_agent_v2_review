"""Stage-25A helper for reusing a prior successful stage-17/16 run.

This file belongs to `app/strategy_pipeline`. It handles only the case where
the manual pipeline calls stage 17 and receives a duplicate skip for the same
Kline slot. It then reads the existing scheduler event log to recover a prior
successful strategy_signal_run_id.

Called by `app/strategy_pipeline/service.py::_run_confirmed_pipeline`.
External services: none. MySQL: reads `strategy_signal_scheduler_event_log`
through the injected repository. Redis: none. Hermes: none. Large models:
none. Trading execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.scheduler.strategy_signal_scheduler_types import StrategySignalSchedulerStatus
from app.strategy_pipeline.types import StrategyPipelineRequest, StrategyPipelineStatus, status_value
from app.strategy_pipeline.utils import PipelineState, compact_object, require_slot, text_or_none


@dataclass(frozen=True)
class Stage17ResolutionOutcome:
    """Outcome for stage-17 success or duplicate-reuse resolution."""

    should_continue: bool
    should_retry_failed_stage17: bool = False
    status: StrategyPipelineStatus | None = None
    message: str = ""
    error_code: str | None = None
    error_message: str | None = None


def resolve_stage17_retry_preflight(
    db_session: Any,
    *,
    request: StrategyPipelineRequest,
    state: PipelineState,
    repository: Any,
) -> Stage17ResolutionOutcome | None:
    """Resolve reusable successful stage-17 output before calling stage 17.

    When a reusable success exists, it wins even if the retry flag is set. A
    failed/blocked historical event is intentionally not retried here; 25A first
    lets stage 17 return its duplicate skip, then performs the explicit retry
    recovery path from that duplicate context.
    """

    if not request.retry_failed_stage17:
        return None

    slot = require_slot(state.kline_slot_utc)
    reusable_event = repository.get_latest_reusable_stage17_scheduler_event(
        db_session,
        symbol=request.symbol,
        base_interval=request.base_interval,
        higher_interval=request.higher_interval,
        target_base_open_time_utc=slot,
    )
    if reusable_event is not None:
        _record_reused_stage17_event(state, reusable_event)
        return Stage17ResolutionOutcome(should_continue=True)
    return None


def resolve_stage17_result_or_reusable_duplicate(
    db_session: Any,
    *,
    request: StrategyPipelineRequest,
    state: PipelineState,
    repository: Any,
    stage17_result: Any,
) -> Stage17ResolutionOutcome:
    """Resolve stage-17 output into a strategy_signal_run_id for later stages.

    Parameters: caller-owned DB session, 25A request/state, pipeline
    repository, and the stage-17 result object.
    Return value: `should_continue=True` when a strategy_signal_run_id is ready
    for explicit 23F. Otherwise returns blocking metadata.
    Failure scenarios: stage 17 failed/blocked, duplicate skip has no reusable
    success event, or the successful event has an empty run_id.
    External effects: none; this helper never reruns stage 16 and never writes
    to MySQL.
    """

    state.details["stage17_result"] = compact_object(stage17_result)
    status = status_value(getattr(stage17_result, "status", ""))
    if status in {StrategySignalSchedulerStatus.SUCCESS.value, StrategySignalSchedulerStatus.PARTIAL_SUCCESS.value}:
        run_id = text_or_none(getattr(stage17_result, "run_id", None))
        if not run_id:
            return Stage17ResolutionOutcome(
                should_continue=False,
                status=StrategyPipelineStatus.BLOCKED,
                message="Stage 17 completed but did not return a strategy_signal_run_id.",
                error_code="strategy_signal_run_missing",
                error_message=getattr(stage17_result, "error_message", None),
            )
        state.strategy_signal_run_id = run_id
        return Stage17ResolutionOutcome(should_continue=True)

    if status != StrategySignalSchedulerStatus.SKIPPED.value:
        return Stage17ResolutionOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message=str(getattr(stage17_result, "message", "") or "Stage 17 did not produce a reusable result."),
            error_code=status,
            error_message=getattr(stage17_result, "error_message", None),
        )
    if not _is_duplicate_skip(stage17_result):
        return Stage17ResolutionOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message=str(getattr(stage17_result, "message", "") or "Stage 17 returned skipped."),
            error_code=status,
            error_message=getattr(stage17_result, "error_message", None),
        )

    slot = require_slot(state.kline_slot_utc)
    reusable_event = repository.get_latest_reusable_stage17_scheduler_event(
        db_session,
        symbol=request.symbol,
        base_interval=request.base_interval,
        higher_interval=request.higher_interval,
        target_base_open_time_utc=slot,
    )
    run_id = text_or_none(getattr(reusable_event, "run_id", None)) if reusable_event is not None else None
    if not run_id:
        if request.retry_failed_stage17:
            in_progress_event = repository.get_latest_in_progress_stage17_scheduler_event_for_slot(
                db_session,
                symbol=request.symbol,
                base_interval=request.base_interval,
                higher_interval=request.higher_interval,
                target_base_open_time_utc=slot,
            )
            if in_progress_event is not None:
                return _build_in_progress_blocked_outcome(state, in_progress_event)
            retryable_event = repository.get_latest_retryable_failed_stage17_scheduler_event_for_slot(
                db_session,
                symbol=request.symbol,
                base_interval=request.base_interval,
                higher_interval=request.higher_interval,
                target_base_open_time_utc=slot,
            )
            if retryable_event is not None:
                return _build_retry_outcome_from_previous_event(state, retryable_event)
        return Stage17ResolutionOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message="Stage 17 skipped a duplicate target, but no successful reusable strategy signal run was found.",
            error_code="stage17_duplicate_reusable_run_not_found",
            error_message=getattr(stage17_result, "error_message", None),
        )

    _record_reused_stage17_event(state, reusable_event)
    return Stage17ResolutionOutcome(should_continue=True)


def record_stage17_retry_success(state: PipelineState, retry_result: Any) -> Stage17ResolutionOutcome:
    """Record a successful manual retry result from stage 16."""

    run_id = text_or_none(getattr(retry_result, "run_id", None))
    if not run_id:
        return Stage17ResolutionOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message="Retry of failed stage 17 completed but did not return a strategy_signal_run_id.",
            error_code="strategy_signal_run_missing_after_stage17_retry",
            error_message=getattr(retry_result, "error_message", None),
        )
    state.strategy_signal_run_id = run_id
    state.details["new_strategy_signal_run_id"] = run_id
    state.details["stage17_retry_result"] = compact_object(retry_result)
    return Stage17ResolutionOutcome(should_continue=True)


def _record_reused_stage17_event(state: PipelineState, reusable_event: Any) -> None:
    event_id = text_or_none(getattr(reusable_event, "event_id", None))
    run_id = text_or_none(getattr(reusable_event, "run_id", None))
    state.strategy_signal_run_id = run_id
    state.details.update(
        {
            "reused_stage17_duplicate": True,
            "reused_strategy_signal_run_id": run_id,
            "reused_stage17_event_id": event_id,
            "stage17_reuse_result": {
                "event_id": event_id,
                "status": status_value(getattr(reusable_event, "status", "")),
                "run_id": run_id,
                "created_at_utc": getattr(reusable_event, "created_at_utc", None),
                "target_base_open_time_utc": getattr(reusable_event, "target_base_open_time_utc", None),
            },
        }
    )


def _build_in_progress_blocked_outcome(state: PipelineState, event: Any) -> Stage17ResolutionOutcome:
    event_id = text_or_none(getattr(event, "event_id", None))
    event_status = status_value(getattr(event, "status", ""))
    state.details.update(
        {
            "stage17_retry_blocked_by_in_progress": True,
            "stage17_in_progress_event_id": event_id,
            "stage17_in_progress_status": event_status,
        }
    )
    return Stage17ResolutionOutcome(
        should_continue=False,
        status=StrategyPipelineStatus.BLOCKED,
        message="Manual retry is blocked because an existing stage-17 event is still running or waiting.",
        error_code="stage17_event_in_progress",
    )


def _build_retry_outcome_from_previous_event(state: PipelineState, previous_event: Any) -> Stage17ResolutionOutcome:
    previous_status = status_value(getattr(previous_event, "status", ""))
    previous_run_id = text_or_none(getattr(previous_event, "run_id", None))
    event_id = text_or_none(getattr(previous_event, "event_id", None))

    if previous_status in {"running", "waiting_upstream"}:
        return Stage17ResolutionOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message="Manual retry is blocked because an existing stage-17 event is still running or waiting.",
            error_code="stage17_event_in_progress",
        )
    if previous_status not in {"failed", "blocked"} or previous_run_id:
        return Stage17ResolutionOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message="Manual retry is allowed only for failed/blocked stage-17 events without a reusable SSR.",
            error_code="stage17_event_not_retryable",
        )

    retry_reason = (
        f"manual retry requested for previous stage-17 event {event_id or ''} "
        f"with status={previous_status}"
    )
    state.details.update(
        {
            "retry_failed_stage17": True,
            "retry_reason": retry_reason,
            "previous_stage17_event_id": event_id,
            "previous_stage17_status": previous_status,
            "previous_stage17_error_code": text_or_none(getattr(previous_event, "error_code", None)),
        }
    )
    return Stage17ResolutionOutcome(
        should_continue=False,
        should_retry_failed_stage17=True,
        message="Manual retry of failed/blocked stage-17 event is allowed.",
    )


def _is_duplicate_skip(stage17_result: Any) -> bool:
    message = str(getattr(stage17_result, "message", "") or "").lower()
    if "duplicate" in message and "target" in message:
        return True
    details = getattr(stage17_result, "details", {}) or {}
    return isinstance(details, dict) and bool(details.get("duplicate_skip_reason"))


__all__ = [
    "Stage17ResolutionOutcome",
    "record_stage17_retry_success",
    "resolve_stage17_result_or_reusable_duplicate",
    "resolve_stage17_retry_preflight",
]
