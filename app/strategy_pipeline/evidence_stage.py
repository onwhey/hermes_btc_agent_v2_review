"""Stage-25A wrapper for explicit 24A/23F evidence aggregation.

This file belongs to `app/strategy_pipeline`. It calls or reuses the existing
stage-23F aggregation service for one already persisted strategy signal run.
It does not implement 23F aggregation rules, does not rerun strategies, does
not read private strategy payloads, does not call models or Hermes, and does
not perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import AppSettings
from app.strategy.aggregation.evidence_service import StrategyEvidenceAggregationService
from app.strategy.aggregation.evidence_types import (
    EvidenceAggregationRequest,
    EvidenceAggregationStatus,
)
from app.strategy_pipeline.types import (
    PIPELINE_STEP_STAGE23F,
    StrategyPipelineRequest,
    StrategyPipelineStatus,
)
from app.strategy_pipeline.utils import PipelineState, compact_object, rollback_if_possible


@dataclass(frozen=True)
class Stage23FOutcome:
    """Result of the explicit 25A evidence-aggregation stage."""

    should_continue: bool
    status: StrategyPipelineStatus | None = None
    message: str = ""
    error_code: str | None = None
    error_message: str | None = None


def run_or_reuse_stage23f_for_pipeline(
    db_session: Any,
    *,
    request: StrategyPipelineRequest,
    state: PipelineState,
    settings: AppSettings,
    repository: Any,
    evidence_service: Any | None = None,
) -> Stage23FOutcome:
    """Explicitly run or reuse stage-23F for the current pipeline run.

    Parameters: caller-owned DB session, 25A request/state, settings,
    repository, and optional injected 23F service.
    Return value: `should_continue=True` when stage 18 may consume the 23F
    result. Blocking/failure metadata is returned otherwise.
    Failure scenarios: disabled 23F config, missing run id, unusable existing
    aggregation, service failure, or missing persisted aggregation id.
    External effects: the existing 23F service may write
    `strategy_evidence_aggregation_result` in confirm-write mode. This wrapper
    itself does not send Hermes or call models.
    """

    state.current_step = PIPELINE_STEP_STAGE23F
    if not state.strategy_signal_run_id:
        return Stage23FOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message="Stage 23F cannot run because stage 16 did not return a strategy_signal_run_id.",
            error_code="strategy_signal_run_missing",
        )
    if not settings.strategy_evidence_aggregation_enabled:
        return Stage23FOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message="STRATEGY_EVIDENCE_AGGREGATION_ENABLED=false blocks explicit 24A/23F aggregation.",
            error_code="strategy_evidence_aggregation_disabled",
        )

    existing = repository.get_latest_strategy_evidence_aggregation(
        db_session,
        strategy_signal_run_id=state.strategy_signal_run_id,
    )
    if existing is not None:
        _record_existing_aggregation(state, existing)
        if _aggregation_status_is_unusable(getattr(existing, "status", "")):
            return Stage23FOutcome(
                should_continue=False,
                status=StrategyPipelineStatus.BLOCKED,
                message="Stage 23F aggregation exists but is not usable.",
                error_code="strategy_evidence_aggregation_unusable",
            )
        return Stage23FOutcome(should_continue=True)

    active_service = evidence_service or StrategyEvidenceAggregationService()
    try:
        result = active_service.run_strategy_evidence_aggregation(
            db_session,
            request=EvidenceAggregationRequest(
                strategy_signal_run_id=state.strategy_signal_run_id,
                trigger_source=request.trigger_source,
                dry_run=False,
                confirm_write=True,
                created_by=request.created_by,
                trace_id=request.trace_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - convert explicit post-step errors.
        rollback_if_possible(db_session)
        return Stage23FOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.FAILED,
            message="Stage 24A/23F evidence aggregation raised before returning a structured result.",
            error_code="strategy_evidence_aggregation_exception",
            error_message=str(exc),
        )

    state.strategy_evidence_aggregation_id = str(getattr(result, "aggregation_id", "") or "")
    state.details["stage23f_result"] = {
        **compact_object(result),
        "pipeline_resolution": "created_by_explicit_25a_stage",
    }
    if result.status == EvidenceAggregationStatus.FAILED:
        return Stage23FOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.FAILED,
            message=result.message or "Stage 23F aggregation failed.",
            error_code=result.error_code or "strategy_evidence_aggregation_failed",
            error_message=result.error_message,
        )
    if result.status == EvidenceAggregationStatus.BLOCKED:
        return Stage23FOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message=result.message or "Stage 23F aggregation was blocked.",
            error_code=result.error_code or "strategy_evidence_aggregation_blocked",
            error_message=result.error_message,
        )
    if not result.database_written or not state.strategy_evidence_aggregation_id:
        return Stage23FOutcome(
            should_continue=False,
            status=StrategyPipelineStatus.BLOCKED,
            message="Stage 23F did not persist a usable aggregation result.",
            error_code="strategy_evidence_aggregation_not_written",
            error_message=result.error_message,
        )
    return Stage23FOutcome(should_continue=True)


def _record_existing_aggregation(state: PipelineState, aggregation: Any) -> None:
    state.strategy_evidence_aggregation_id = str(getattr(aggregation, "aggregation_id", "") or "")
    state.details["stage23f_result"] = {
        "aggregation_id": state.strategy_evidence_aggregation_id,
        "status": str(getattr(aggregation, "status", "") or ""),
        "candidate_bias": str(getattr(aggregation, "candidate_bias", "") or ""),
        "decision_readiness": str(getattr(aggregation, "decision_readiness", "") or ""),
        "pipeline_resolution": "reused_existing_23f_aggregation",
    }


def _aggregation_status_is_unusable(status: Any) -> bool:
    return str(getattr(status, "value", status) or "").lower() in {"failed", "blocked"}


__all__ = ["Stage23FOutcome", "run_or_reuse_stage23f_for_pipeline"]
