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
    status: StrategyPipelineStatus | None = None
    message: str = ""
    error_code: str | None = None
    error_message: str | None = None


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
        return Stage17ResolutionOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message="Stage 17 skipped a duplicate target, but no successful reusable strategy signal run was found.",
            error_code="stage17_duplicate_reusable_run_not_found",
            error_message=getattr(stage17_result, "error_message", None),
        )

    event_id = text_or_none(getattr(reusable_event, "event_id", None))
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
    return Stage17ResolutionOutcome(should_continue=True)


def _is_duplicate_skip(stage17_result: Any) -> bool:
    message = str(getattr(stage17_result, "message", "") or "").lower()
    if "duplicate" in message and "target" in message:
        return True
    details = getattr(stage17_result, "details", {}) or {}
    return isinstance(details, dict) and bool(details.get("duplicate_skip_reason"))


__all__ = ["Stage17ResolutionOutcome", "resolve_stage17_result_or_reusable_duplicate"]
