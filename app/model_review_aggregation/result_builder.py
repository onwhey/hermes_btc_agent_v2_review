"""Result and persistence payload builders for stage-20A aggregation.

This file belongs to `app/model_review_aggregation`. It turns deterministic
stage-20A decisions into result DTOs and repository payloads. It does not call
external services, databases, Redis, Hermes, large models, formal Kline tables,
or trading execution capabilities.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.core.config import AppSettings
from app.model_review_aggregation.candidate_rules import (
    candidate_result_created_at,
    candidate_run_id,
    count_model_run_statuses,
    text_attr,
)
from app.model_review_aggregation.fingerprint import build_review_input_fingerprint
from app.model_review_aggregation.schema import (
    AGGREGATION_MODE_SINGLE_OR_REUSE,
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    REVIEW_INPUT_FINGERPRINT_VERSION,
    ModelReviewAggregationPersistencePayload,
    ModelReviewAggregationRequest,
    ModelReviewAggregationResult,
    ModelReviewAggregationStatus,
)
from app.model_review_aggregation.summarizer import (
    build_model_results_summary,
    build_summary_text,
    empty_summaries,
    summarize_accepted_model_results,
)

NO_MODEL_CALL_TEXT = "本轮未调用大模型。"
CONFIG_BLOCK_REAL_MODEL = "MODEL_REVIEW_REAL_MODEL_ENABLED=false"


def validate_request(
    request: ModelReviewAggregationRequest,
    *,
    review_aggregation_run_id: str,
    trace_id: str,
    settings: AppSettings,
    allowed_trigger_sources: set[str] | frozenset[str],
) -> ModelReviewAggregationResult | None:
    """Return an invalid-request result or None when the request is valid."""

    problems: list[str] = []
    if not request.material_pack_id.strip():
        problems.append("material_pack_id is required")
    if request.trigger_source not in allowed_trigger_sources:
        problems.append("trigger_source supports only cli in stage 20A")
    if request.dry_run and request.confirm_write:
        problems.append("dry_run and confirm_write cannot both be true")
    if not request.dry_run and not request.confirm_write:
        problems.append("non-dry-run model review aggregation requires confirm_write")
    if settings.model_review_reuse_max_base_bars < 0:
        problems.append("MODEL_REVIEW_REUSE_MAX_BASE_BARS must be >= 0")
    if not problems:
        return None
    return ModelReviewAggregationResult(
        status=ModelReviewAggregationStatus.FAILED,
        exit_code=EXIT_PARAMETER_ERROR,
        review_aggregation_run_id=review_aggregation_run_id,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=None,
        strategy_signal_run_id=None,
        snapshot_id=None,
        trace_id=trace_id,
        model_review_invoked=False,
        model_review_invocation_mode="none",
        model_review_skip_reason=f"{NO_MODEL_CALL_TEXT} 请求参数无效。",
        model_review_block_reason="; ".join(problems),
        model_review_reuse_max_base_bars=settings.model_review_reuse_max_base_bars,
        summary_text=f"{NO_MODEL_CALL_TEXT} 请求参数无效：{'; '.join(problems)}",
        error_code="invalid_request",
        error_message="; ".join(problems),
    )


def build_failed_lookup_result(
    *,
    request: ModelReviewAggregationRequest,
    review_aggregation_run_id: str,
    trace_id: str,
    error_message: str,
    material_pack: Any | None = None,
) -> ModelReviewAggregationResult:
    """Return a failed result for database read errors."""

    return ModelReviewAggregationResult(
        status=ModelReviewAggregationStatus.FAILED,
        exit_code=EXIT_FAILED,
        review_aggregation_run_id=review_aggregation_run_id,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=text_attr(material_pack, "aggregation_run_id") if material_pack is not None else None,
        strategy_signal_run_id=text_attr(material_pack, "strategy_signal_run_id") if material_pack is not None else None,
        snapshot_id=text_attr(material_pack, "snapshot_id") if material_pack is not None else None,
        trace_id=trace_id,
        model_review_invoked=False,
        model_review_invocation_mode="none",
        model_review_skip_reason=f"{NO_MODEL_CALL_TEXT} 阶段 20A 读取数据失败。",
        model_review_block_reason="database_lookup_failed",
        summary_text=f"{NO_MODEL_CALL_TEXT} 阶段 20A 读取数据失败。",
        error_code="model_review_aggregation_lookup_failed",
        error_message=error_message,
    )


def build_material_missing_result(
    *,
    request: ModelReviewAggregationRequest,
    review_aggregation_run_id: str,
    trace_id: str,
    settings: AppSettings,
) -> ModelReviewAggregationResult:
    """Return a blocked result when the stage-18 material pack is absent."""

    skip_reason = f"{NO_MODEL_CALL_TEXT} analysis_material_pack 不存在。"
    return ModelReviewAggregationResult(
        status=ModelReviewAggregationStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        review_aggregation_run_id=review_aggregation_run_id,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=None,
        strategy_signal_run_id=None,
        snapshot_id=None,
        trace_id=trace_id,
        model_review_invoked=False,
        model_review_invocation_mode="none",
        model_review_reused=False,
        reused_model_analysis_run_id=None,
        model_review_skip_reason=skip_reason,
        model_review_block_reason="analysis_material_pack not found",
        model_review_basis="missing_material_pack",
        model_review_reuse_status="material_pack_not_found",
        model_review_reuse_max_base_bars=settings.model_review_reuse_max_base_bars,
        summary_text=skip_reason,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        error_code="material_pack_not_found",
        error_message=skip_reason,
    )


def build_success_result_from_candidate(
    *,
    request: ModelReviewAggregationRequest,
    review_aggregation_run_id: str,
    material_pack: Any,
    candidate: Any,
    model_runs: Sequence[Any],
    input_model_result_count: int,
    run_counts: Mapping[str, int],
    trace_id: str,
    reused: bool,
    reuse_base_bars: int,
    reuse_status: str,
    settings: AppSettings,
) -> ModelReviewAggregationResult:
    """Return a success result for current or reused stage-19 review rows."""

    accepted_results = (candidate,)
    summaries = summarize_accepted_model_results(accepted_results)
    latest_at = candidate_result_created_at(candidate)
    reused_run_id = candidate_run_id(candidate) if reused else None
    if reused:
        skip_reason = (
            f"{NO_MODEL_CALL_TEXT} 复用旧阶段 19 模型审查结果；"
            f"reuse_base_bars={reuse_base_bars}，max_base_bars={settings.model_review_reuse_max_base_bars}。"
        )
        basis = "reused_model_review"
        invocation_mode = "reused"
    else:
        skip_reason = f"{NO_MODEL_CALL_TEXT} 当前 material_pack 已有可用阶段 19 模型审查结果。"
        basis = "current_model_review"
        invocation_mode = "none"
    details = build_common_details(
        material_pack=material_pack,
        selected_candidate=candidate,
        model_runs=model_runs,
        input_model_result_count=input_model_result_count,
        summaries=summaries,
        reused=reused,
    )
    summary_text = build_summary_text(
        prefix=skip_reason,
        review_decision=summaries["review_decision_summary"],
        evidence_quality=summaries["evidence_quality_summary"],
        risk_acceptability=summaries["risk_acceptability_summary"],
        conflict_level=summaries["strategy_conflict_summary"],
    )
    return ModelReviewAggregationResult(
        status=ModelReviewAggregationStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        review_aggregation_run_id=review_aggregation_run_id,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=text_attr(material_pack, "aggregation_run_id"),
        strategy_signal_run_id=text_attr(material_pack, "strategy_signal_run_id"),
        snapshot_id=text_attr(material_pack, "snapshot_id"),
        trace_id=trace_id,
        accepted_model_result_count=len(accepted_results),
        failed_model_result_count=run_counts["failed"],
        blocked_model_result_count=run_counts["blocked"],
        skipped_model_result_count=run_counts["skipped"],
        model_review_invoked=False,
        model_review_invocation_mode=invocation_mode,
        model_review_reused=reused,
        reused_model_analysis_run_id=reused_run_id,
        model_review_skip_reason=skip_reason,
        model_review_block_reason=None,
        model_review_basis=basis,
        latest_model_review_at_utc=latest_at,
        model_review_reuse_status=reuse_status,
        model_review_reuse_base_bars=reuse_base_bars,
        model_review_reuse_max_base_bars=settings.model_review_reuse_max_base_bars,
        model_review_expired=False,
        review_decision_summary=summaries["review_decision_summary"],
        evidence_quality_summary=summaries["evidence_quality_summary"],
        risk_acceptability_summary=summaries["risk_acceptability_summary"],
        strategy_conflict_summary=summaries["strategy_conflict_summary"],
        summary_text=summary_text,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        error_code=None,
        error_message=None,
        details=details,
    )


def build_expired_blocked_result(
    *,
    request: ModelReviewAggregationRequest,
    review_aggregation_run_id: str,
    material_pack: Any,
    candidate: Any,
    model_runs: Sequence[Any],
    input_model_result_count: int,
    run_counts: Mapping[str, int],
    trace_id: str,
    settings: AppSettings,
) -> ModelReviewAggregationResult:
    """Return a blocked result for an otherwise compatible but expired review."""

    reuse_base_bars = int(getattr(candidate, "reuse_base_bars", 0) or 0)
    config_blocked = not bool(settings.model_review_real_model_enabled)
    if config_blocked:
        error_code = "model_review_expired_but_real_model_disabled"
        block_reason = CONFIG_BLOCK_REAL_MODEL
        skip_reason = (
            f"{NO_MODEL_CALL_TEXT} 旧模型审查已过期，reuse_base_bars={reuse_base_bars} "
            f"超过 max_base_bars={settings.model_review_reuse_max_base_bars}；{CONFIG_BLOCK_REAL_MODEL}。"
        )
    else:
        error_code = "model_review_expired"
        block_reason = "20A does not trigger stage 19 automatically; run stage 19 manually."
        skip_reason = f"{NO_MODEL_CALL_TEXT} 旧模型审查已过期，20A 第一版只聚合和判断复用，不自动触发阶段 19。"
    summaries = empty_summaries()
    details = build_common_details(
        material_pack=material_pack,
        selected_candidate=candidate,
        model_runs=model_runs,
        input_model_result_count=input_model_result_count,
        summaries=summaries,
        reused=False,
    )
    return ModelReviewAggregationResult(
        status=ModelReviewAggregationStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        review_aggregation_run_id=review_aggregation_run_id,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=text_attr(material_pack, "aggregation_run_id"),
        strategy_signal_run_id=text_attr(material_pack, "strategy_signal_run_id"),
        snapshot_id=text_attr(material_pack, "snapshot_id"),
        trace_id=trace_id,
        accepted_model_result_count=0,
        failed_model_result_count=run_counts["failed"],
        blocked_model_result_count=run_counts["blocked"],
        skipped_model_result_count=run_counts["skipped"],
        model_review_invoked=False,
        model_review_invocation_mode="none",
        model_review_reused=False,
        reused_model_analysis_run_id=None,
        model_review_skip_reason=skip_reason,
        model_review_block_reason=block_reason,
        model_review_basis="expired_model_review_not_used",
        latest_model_review_at_utc=candidate_result_created_at(candidate),
        model_review_reuse_status=error_code,
        model_review_reuse_base_bars=reuse_base_bars,
        model_review_reuse_max_base_bars=settings.model_review_reuse_max_base_bars,
        model_review_expired=True,
        review_decision_summary=summaries["review_decision_summary"],
        evidence_quality_summary=summaries["evidence_quality_summary"],
        risk_acceptability_summary=summaries["risk_acceptability_summary"],
        strategy_conflict_summary=summaries["strategy_conflict_summary"],
        summary_text=skip_reason,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        error_code=error_code,
        error_message=skip_reason,
        details=details,
    )


def build_no_result_blocked_result(
    *,
    request: ModelReviewAggregationRequest,
    review_aggregation_run_id: str,
    material_pack: Any,
    model_runs: Sequence[Any],
    input_model_result_count: int,
    run_counts: Mapping[str, int],
    trace_id: str,
    reason_code: str,
    settings: AppSettings,
) -> ModelReviewAggregationResult:
    """Return a blocked result when no usable stage-19 result exists."""

    config_blocked = not bool(settings.model_review_real_model_enabled)
    block_reason = CONFIG_BLOCK_REAL_MODEL if config_blocked else "No successful stage-19 model review result exists."
    skip_reason = f"{NO_MODEL_CALL_TEXT} 未找到可用阶段 19 模型审查结果。"
    if config_blocked:
        skip_reason = f"{skip_reason} {CONFIG_BLOCK_REAL_MODEL}。"
    summaries = empty_summaries()
    details = build_common_details(
        material_pack=material_pack,
        selected_candidate=None,
        model_runs=model_runs,
        input_model_result_count=input_model_result_count,
        summaries=summaries,
        reused=False,
    )
    return ModelReviewAggregationResult(
        status=ModelReviewAggregationStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        review_aggregation_run_id=review_aggregation_run_id,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=text_attr(material_pack, "aggregation_run_id"),
        strategy_signal_run_id=text_attr(material_pack, "strategy_signal_run_id"),
        snapshot_id=text_attr(material_pack, "snapshot_id"),
        trace_id=trace_id,
        accepted_model_result_count=0,
        failed_model_result_count=run_counts["failed"],
        blocked_model_result_count=run_counts["blocked"],
        skipped_model_result_count=run_counts["skipped"],
        model_review_invoked=False,
        model_review_invocation_mode="none",
        model_review_reused=False,
        reused_model_analysis_run_id=None,
        model_review_skip_reason=skip_reason,
        model_review_block_reason=block_reason,
        model_review_basis="material_only_without_model_review",
        latest_model_review_at_utc=None,
        model_review_reuse_status=reason_code,
        model_review_reuse_base_bars=None,
        model_review_reuse_max_base_bars=settings.model_review_reuse_max_base_bars,
        model_review_expired=False,
        review_decision_summary=summaries["review_decision_summary"],
        evidence_quality_summary=summaries["evidence_quality_summary"],
        risk_acceptability_summary=summaries["risk_acceptability_summary"],
        strategy_conflict_summary=summaries["strategy_conflict_summary"],
        summary_text=skip_reason,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        error_code=reason_code,
        error_message=skip_reason,
        details=details,
    )


def build_common_details(
    *,
    material_pack: Any,
    selected_candidate: Any | None,
    model_runs: Sequence[Any],
    input_model_result_count: int,
    summaries: Mapping[str, Any],
    reused: bool,
) -> Mapping[str, Any]:
    """Return bounded detail fields for result DTOs and persistence."""

    selected_run = selected_candidate.model_analysis_run if selected_candidate is not None else None
    review_fingerprint, review_fingerprint_details = build_review_input_fingerprint(
        material_pack,
        selected_run,
    )
    selected_results = (selected_candidate,) if selected_candidate is not None else ()
    return {
        "input_model_run_count": len(model_runs),
        "input_model_result_count": input_model_result_count,
        "aggregation_mode": AGGREGATION_MODE_SINGLE_OR_REUSE,
        "invoked_model_keys_json": [],
        "invoked_model_roles_json": [],
        "model_review_chain_status": "not_started",
        "model_review_partial_failure_reason": None,
        "review_input_fingerprint": review_fingerprint,
        "review_input_fingerprint_version": REVIEW_INPUT_FINGERPRINT_VERSION,
        "review_input_fingerprint_details": review_fingerprint_details,
        "model_results_summary_json": build_model_results_summary(selected_results),
        "model_disagreement_json": summaries["model_disagreement_json"],
        "risk_warnings_json": summaries["risk_warnings_json"],
        "missing_evidence_json": summaries["missing_evidence_json"],
        "human_review_questions_json": summaries["human_review_questions_json"],
        "model_consensus_level": summaries["model_consensus_level"],
        "allowed_advice_mode": summaries["allowed_advice_mode"],
        "directional_trade_allowed": False,
        "reused_model_review_created_at_utc": (
            candidate_result_created_at(selected_candidate) if reused and selected_candidate is not None else None
        ),
    }


def build_persistence_payload(
    *,
    request: ModelReviewAggregationRequest,
    material_pack: Any,
    result: ModelReviewAggregationResult,
    model_runs: Sequence[Any],
    input_model_result_count: int,
) -> ModelReviewAggregationPersistencePayload:
    """Return the repository payload for one compact stage-20A row."""

    details = result.details or {}
    run_counts = count_model_run_statuses(model_runs)
    return ModelReviewAggregationPersistencePayload(
        review_aggregation_run_id=result.review_aggregation_run_id,
        material_pack_id=result.material_pack_id,
        aggregation_run_id=text_attr(material_pack, "aggregation_run_id"),
        strategy_signal_run_id=text_attr(material_pack, "strategy_signal_run_id"),
        snapshot_id=text_attr(material_pack, "snapshot_id"),
        symbol=text_attr(material_pack, "symbol"),
        base_interval=text_attr(material_pack, "base_interval"),
        higher_interval=text_attr(material_pack, "higher_interval"),
        status=result.status,
        trigger_source=request.trigger_source,
        created_by=request.created_by,
        trace_id=result.trace_id,
        input_model_run_count=int(details.get("input_model_run_count", len(model_runs))),
        input_model_result_count=input_model_result_count,
        accepted_model_result_count=result.accepted_model_result_count,
        failed_model_result_count=result.failed_model_result_count or run_counts["failed"],
        blocked_model_result_count=result.blocked_model_result_count or run_counts["blocked"],
        skipped_model_result_count=result.skipped_model_result_count or run_counts["skipped"],
        aggregation_mode=str(details.get("aggregation_mode") or AGGREGATION_MODE_SINGLE_OR_REUSE),
        model_review_invoked=False,
        model_review_invocation_mode=result.model_review_invocation_mode,
        model_review_reused=result.model_review_reused,
        reused_model_analysis_run_id=result.reused_model_analysis_run_id,
        reused_model_review_created_at_utc=details.get("reused_model_review_created_at_utc"),
        model_review_skip_reason=result.model_review_skip_reason,
        model_review_block_reason=result.model_review_block_reason,
        invoked_model_keys_json=list(details.get("invoked_model_keys_json") or []),
        invoked_model_roles_json=list(details.get("invoked_model_roles_json") or []),
        model_review_chain_status=str(details.get("model_review_chain_status") or "not_started"),
        model_review_partial_failure_reason=details.get("model_review_partial_failure_reason"),
        latest_model_review_at_utc=result.latest_model_review_at_utc,
        model_review_basis=result.model_review_basis,
        model_review_reuse_status=result.model_review_reuse_status,
        model_review_reuse_base_bars=result.model_review_reuse_base_bars,
        model_review_reuse_max_base_bars=result.model_review_reuse_max_base_bars,
        model_review_expired=result.model_review_expired,
        review_input_fingerprint=str(details.get("review_input_fingerprint") or ""),
        review_input_fingerprint_version=str(details.get("review_input_fingerprint_version") or ""),
        review_decision_summary=result.review_decision_summary,
        evidence_quality_summary=result.evidence_quality_summary,
        risk_acceptability_summary=result.risk_acceptability_summary,
        strategy_conflict_summary=result.strategy_conflict_summary,
        model_consensus_level=str(details.get("model_consensus_level") or "none"),
        allowed_advice_mode=str(details.get("allowed_advice_mode") or "wait_only"),
        directional_trade_allowed=False,
        model_results_summary_json=dict(details.get("model_results_summary_json") or {}),
        model_disagreement_json=dict(details.get("model_disagreement_json") or {}),
        risk_warnings_json=list(details.get("risk_warnings_json") or []),
        missing_evidence_json=list(details.get("missing_evidence_json") or []),
        human_review_questions_json=list(details.get("human_review_questions_json") or []),
        summary_text=result.summary_text,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        error_code=result.error_code,
        error_message=result.error_message,
    )


__all__ = [
    "CONFIG_BLOCK_REAL_MODEL",
    "NO_MODEL_CALL_TEXT",
    "build_expired_blocked_result",
    "build_failed_lookup_result",
    "build_material_missing_result",
    "build_no_result_blocked_result",
    "build_persistence_payload",
    "build_success_result_from_candidate",
    "validate_request",
]
