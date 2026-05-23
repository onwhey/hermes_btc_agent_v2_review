"""Result and request validation helpers for stage-21A strategy advice.

This file belongs to `app/strategy_advice`. It converts in-memory lifecycle
plans into compact service results and validates CLI-only stage-21A requests.
It does not access external services, MySQL, Redis, Hermes, model providers, or
trading execution capabilities.
"""

from __future__ import annotations

from typing import Any, AbstractSet

from app.strategy_advice.payload_builder import bool_attr, optional_text_attr, text_attr
from app.strategy_advice.plan import LifecyclePlan
from app.strategy_advice.schema import (
    EXIT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    StrategyAdviceRequest,
    StrategyAdviceResult,
    StrategyAdviceServiceStatus,
    load_json_text,
)


def result_from_lifecycle_plan(*, request: StrategyAdviceRequest, plan: LifecyclePlan) -> StrategyAdviceResult:
    """Build the public service result from a dry-run or persisted plan."""

    aggregation_row = plan.aggregation_row
    notification_payload = plan.lifecycle_payload.notification_payload_json
    return StrategyAdviceResult(
        status=StrategyAdviceServiceStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        review_id=plan.lifecycle_payload.review_id,
        review_aggregation_run_id=request.review_aggregation_run_id,
        trace_id=request.trace_id,
        lifecycle_action=plan.lifecycle_action,
        lifecycle_reason=plan.lifecycle_reason,
        advice_id=plan.result_advice_id,
        result_advice_id=plan.result_advice_id,
        reviewed_advice_id=plan.reviewed_advice_id,
        previous_advice_id=plan.previous_advice_id,
        advice_code=plan.advice_code,
        advice_path=plan.advice_path,
        advice_status=plan.advice_status,
        advice_action=plan.candidate.advice_action,
        directional_bias=plan.candidate.directional_bias,
        trade_permission=plan.candidate.trade_permission,
        material_pack_id=optional_text_attr(aggregation_row, "material_pack_id"),
        strategy_signal_run_id=optional_text_attr(aggregation_row, "strategy_signal_run_id"),
        snapshot_id=optional_text_attr(aggregation_row, "snapshot_id"),
        model_review_invoked=bool_attr(aggregation_row, "model_review_invoked"),
        model_review_invocation_mode=text_attr(aggregation_row, "model_review_invocation_mode") or "none",
        model_review_reused=bool_attr(aggregation_row, "model_review_reused"),
        reused_model_analysis_run_id=optional_text_attr(aggregation_row, "reused_model_analysis_run_id"),
        model_review_skip_reason=text_attr(aggregation_row, "model_review_skip_reason"),
        model_review_block_reason=optional_text_attr(aggregation_row, "model_review_block_reason"),
        invoked_model_keys_json=tuple(
            str(item) for item in load_json_text(getattr(aggregation_row, "invoked_model_keys_json", "[]"), [])
        ),
        invoked_model_roles_json=tuple(
            str(item) for item in load_json_text(getattr(aggregation_row, "invoked_model_roles_json", "[]"), [])
        ),
        model_review_basis=text_attr(aggregation_row, "model_review_basis") or "none",
        model_review_expired=bool_attr(aggregation_row, "model_review_expired"),
        model_review_chain_status=text_attr(aggregation_row, "model_review_chain_status") or "not_started",
        latest_model_review_at_utc=getattr(aggregation_row, "latest_model_review_at_utc", None),
        notification_required=True,
        notification_level=plan.lifecycle_payload.notification_level,
        notification_reason=plan.lifecycle_payload.notification_reason,
        notification_payload_json=notification_payload,
        created_advice_count=1 if plan.advice_payload is not None else 0,
        updated_advice_count=1 if plan.status_update_row is not None else 0,
        lifecycle_review_count=1,
        event_count=len(plan.event_payloads),
        trade_setup_count=len(plan.trade_setup_payloads),
        dry_run=request.dry_run,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        summary_text=plan.candidate.summary_text,
        details={
            "semantic_signature": plan.candidate.semantic_signature,
            "stage21a_calls_model": False,
            "stage21a_sends_hermes": False,
        },
    )


def validate_strategy_advice_request(
    *,
    request: StrategyAdviceRequest,
    review_id: str,
    allowed_trigger_sources: AbstractSet[str],
) -> StrategyAdviceResult | None:
    """Validate a stage-21A request before reading or writing any rows."""

    problems: list[str] = []
    if not request.review_aggregation_run_id.strip():
        problems.append("review_aggregation_run_id is required")
    if request.trigger_source not in allowed_trigger_sources:
        problems.append("trigger_source supports only cli in stage 21A")
    if request.dry_run and request.confirm_write:
        problems.append("dry_run and confirm_write cannot both be true")
    if not request.dry_run and not request.confirm_write:
        problems.append("non-dry-run strategy advice lifecycle requires confirm_write")
    if not problems:
        return None
    message = "; ".join(problems)
    return StrategyAdviceResult(
        status=StrategyAdviceServiceStatus.FAILED,
        exit_code=EXIT_PARAMETER_ERROR,
        review_id=review_id,
        review_aggregation_run_id=request.review_aggregation_run_id,
        trace_id=request.trace_id,
        dry_run=request.dry_run,
        notification_required=False,
        notification_level="none",
        notification_reason="invalid request",
        summary_text="Stage 21A request is invalid and wrote nothing.",
        error_code="invalid_request",
        error_message=message,
    )


def failed_strategy_advice_result(
    *,
    request: StrategyAdviceRequest,
    review_id: str,
    error_code: str,
    error_message: str,
    aggregation_row: Any | None = None,
) -> StrategyAdviceResult:
    """Build a structured failure result without writing new rows."""

    return StrategyAdviceResult(
        status=StrategyAdviceServiceStatus.FAILED,
        exit_code=EXIT_FAILED,
        review_id=review_id,
        review_aggregation_run_id=request.review_aggregation_run_id,
        trace_id=request.trace_id,
        material_pack_id=optional_text_attr(aggregation_row, "material_pack_id") if aggregation_row is not None else None,
        strategy_signal_run_id=(
            optional_text_attr(aggregation_row, "strategy_signal_run_id") if aggregation_row is not None else None
        ),
        snapshot_id=optional_text_attr(aggregation_row, "snapshot_id") if aggregation_row is not None else None,
        dry_run=request.dry_run,
        notification_required=False,
        notification_level="none",
        notification_reason=error_code,
        summary_text="Stage 21A failed before completing advice lifecycle persistence.",
        error_code=error_code,
        error_message=error_message,
    )


__all__ = [
    "failed_strategy_advice_result",
    "result_from_lifecycle_plan",
    "validate_strategy_advice_request",
]
