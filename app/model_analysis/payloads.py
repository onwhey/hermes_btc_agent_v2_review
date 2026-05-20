"""Payload helpers for stage-19 model analysis.

This file belongs to `app/model_analysis`. It contains deterministic helpers
for IDs, review-version keys, service results, and repository payloads.

Called by `app/model_analysis/service.py`.
External services: none. MySQL: none. Redis: none. Hermes: none. Real model
calls: none. Trading execution: none.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Mapping

from app.core.config import AppSettings
from app.model_analysis.types import (
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    MODEL_REVIEW_MODEL_KEY_DEFAULT,
    MODEL_REVIEW_MODEL_ROLE_DEFAULT,
    MODEL_REVIEW_MODE_DEFAULT,
    ModelAnalysisRequest,
    ModelAnalysisResultPersistencePayload,
    ModelAnalysisRunPersistencePayload,
    ModelAnalysisServiceResult,
    ModelAnalysisStatus,
    ModelProviderResult,
    PromptBuildResult,
    ReviewDecision,
)


def build_model_analysis_run_id(material_pack_id: str) -> str:
    """Return a non-unique attempt id prefix plus random suffix."""

    stable = uuid.uuid5(uuid.NAMESPACE_URL, f"model-analysis-run:{material_pack_id}").hex[:12]
    return f"MAR-{stable}-{uuid.uuid4().hex[:8]}"


def build_model_analysis_result_id(material_pack_id: str) -> str:
    """Return a final result id prefix plus random suffix."""

    stable = uuid.uuid5(uuid.NAMESPACE_URL, f"model-analysis-result:{material_pack_id}").hex[:12]
    return f"MARES-{stable}-{uuid.uuid4().hex[:8]}"


def build_review_version_key(
    *,
    material_pack_id: str,
    model_provider: str,
    model_key: str,
    model_name: str,
    model_version: str,
    profile_hash: str,
    prompt_template_hash: str,
    prompt_template_version: str,
    review_schema_version: str,
    review_mode: str,
) -> str:
    """Build the single-column final-result idempotency key."""

    raw_key = "|".join(
        (
            material_pack_id,
            model_key,
            model_provider,
            model_name,
            model_version,
            profile_hash,
            prompt_template_hash,
            prompt_template_version,
            review_schema_version,
            review_mode,
        )
    )
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def build_invalid_request_result(
    request: ModelAnalysisRequest,
    *,
    model_analysis_run_id: str,
    trace_id: str,
    error_message: str,
) -> ModelAnalysisServiceResult:
    """Return a compact parameter-error result."""

    return ModelAnalysisServiceResult(
        status=ModelAnalysisStatus.FAILED,
        exit_code=EXIT_PARAMETER_ERROR,
        model_analysis_run_id=model_analysis_run_id,
        model_analysis_result_id=None,
        review_version_key=None,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=None,
        strategy_signal_run_id=None,
        trace_id=trace_id,
        message="Model analysis request parameters are invalid.",
        error_message=error_message,
    )


def build_blocked_result(
    request: ModelAnalysisRequest,
    *,
    model_analysis_run_id: str,
    review_version_key: str | None,
    trace_id: str,
    aggregation_run_id: str | None = None,
    strategy_signal_run_id: str | None = None,
    input_char_count: int = 0,
    input_byte_count: int = 0,
    output_char_count: int = 0,
    output_byte_count: int = 0,
    message: str,
    error_code: str,
    error_message: str | None = None,
    model_key: str | None = None,
    model_role: str | None = None,
    analysis_mode: str | None = None,
) -> ModelAnalysisServiceResult:
    """Return a compact blocked result without final trading semantics."""

    return ModelAnalysisServiceResult(
        status=ModelAnalysisStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        model_analysis_run_id=model_analysis_run_id,
        model_analysis_result_id=None,
        review_version_key=review_version_key,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=aggregation_run_id,
        strategy_signal_run_id=strategy_signal_run_id,
        trace_id=trace_id,
        review_decision=ReviewDecision.BLOCKED.value,
        model_key=model_key,
        model_role=model_role,
        analysis_mode=analysis_mode,
        evidence_quality="unknown",
        risk_acceptability="unknown",
        strategy_conflict_level="unknown",
        human_review_required=False,
        input_char_count=input_char_count,
        input_byte_count=input_byte_count,
        output_char_count=output_char_count,
        output_byte_count=output_byte_count,
        message=message,
        error_code=error_code,
        error_message=error_message,
    )


def build_failed_result(
    request: ModelAnalysisRequest,
    *,
    model_analysis_run_id: str,
    review_version_key: str | None,
    trace_id: str,
    message: str,
    error_message: str,
    error_code: str | None = None,
    aggregation_run_id: str | None = None,
    strategy_signal_run_id: str | None = None,
) -> ModelAnalysisServiceResult:
    """Return a compact failed result."""

    return ModelAnalysisServiceResult(
        status=ModelAnalysisStatus.FAILED,
        exit_code=EXIT_FAILED,
        model_analysis_run_id=model_analysis_run_id,
        model_analysis_result_id=None,
        review_version_key=review_version_key,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=aggregation_run_id,
        strategy_signal_run_id=strategy_signal_run_id,
        trace_id=trace_id,
        message=message,
        error_code=error_code,
        error_message=error_message,
    )


def build_success_result(
    request: ModelAnalysisRequest,
    *,
    model_analysis_run_id: str,
    model_analysis_result_id: str,
    review_version_key: str,
    material_pack: Any,
    normalized: Mapping[str, Any],
    prompt: PromptBuildResult,
    provider_result: ModelProviderResult,
    details: Mapping[str, Any],
) -> ModelAnalysisServiceResult:
    """Return a compact success result from schema-valid provider output."""

    review_decision = str(normalized["review_decision"])
    return ModelAnalysisServiceResult(
        status=ModelAnalysisStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        model_analysis_run_id=model_analysis_run_id,
        model_analysis_result_id=model_analysis_result_id,
        review_version_key=review_version_key,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=optional_text(getattr(material_pack, "aggregation_run_id", None)),
        strategy_signal_run_id=optional_text(getattr(material_pack, "strategy_signal_run_id", None)),
        trace_id=request.trace_id,
        review_decision=review_decision,
        model_key=optional_text(details.get("model_key")),
        model_role=optional_text(details.get("model_role")),
        analysis_mode=optional_text(details.get("analysis_mode")),
        evidence_quality=str(normalized["evidence_quality"]),
        risk_acceptability=str(normalized["risk_acceptability"]),
        strategy_conflict_level=str(normalized["strategy_conflict_level"]),
        human_review_required=bool(normalized["human_review_required"]),
        input_char_count=prompt.input_char_count,
        input_byte_count=prompt.input_byte_count,
        output_char_count=provider_result.output_char_count,
        output_byte_count=provider_result.output_byte_count,
        raw_response_char_count=int(getattr(provider_result, "raw_response_char_count", 0) or 0),
        raw_response_byte_count=int(getattr(provider_result, "raw_response_byte_count", 0) or 0),
        estimated_cost=optional_text(details.get("estimated_cost")),
        cost_currency=optional_text(details.get("cost_currency")),
        message="Stage-19 model review completed.",
        details=details,
    )


def build_skipped_result_from_existing(
    request: ModelAnalysisRequest,
    *,
    existing_result: Any,
    model_analysis_run_id: str,
    trace_id: str,
    details: Mapping[str, Any],
) -> ModelAnalysisServiceResult:
    """Return skipped/already_exists from a final result row."""

    return ModelAnalysisServiceResult(
        status=ModelAnalysisStatus.SKIPPED,
        exit_code=EXIT_SUCCESS,
        model_analysis_run_id=model_analysis_run_id,
        model_analysis_result_id=str(getattr(existing_result, "model_analysis_result_id", "")),
        review_version_key=str(getattr(existing_result, "review_version_key", "")),
        material_pack_id=request.material_pack_id,
        aggregation_run_id=optional_text(getattr(existing_result, "aggregation_run_id", None)),
        strategy_signal_run_id=optional_text(getattr(existing_result, "strategy_signal_run_id", None)),
        trace_id=trace_id,
        review_decision=str(getattr(existing_result, "review_decision", "")),
        evidence_quality=str(getattr(existing_result, "evidence_quality", "")),
        risk_acceptability=str(getattr(existing_result, "risk_acceptability", "")),
        strategy_conflict_level=str(getattr(existing_result, "strategy_conflict_level", "")),
        human_review_required=bool(getattr(existing_result, "human_review_required", False)),
        message="Stage-19 model review skipped: already_exists final result.",
        details=details,
    )


def build_run_payload(
    *,
    request: ModelAnalysisRequest,
    material_pack: Any | None,
    prompt: PromptBuildResult | None,
    provider_metadata: Any,
    provider_result: ModelProviderResult | None,
    model_analysis_run_id: str,
    review_version_key: str,
    trace_id: str,
    status: ModelAnalysisStatus,
    human_review_required: bool,
    error_code: str | None,
    error_message: str | None,
    settings: AppSettings,
) -> ModelAnalysisRunPersistencePayload:
    """Build one attempt-row repository payload."""

    return ModelAnalysisRunPersistencePayload(
        model_analysis_run_id=model_analysis_run_id,
        review_version_key=review_version_key,
        material_pack_id=request.material_pack_id,
        aggregation_run_id=optional_text(getattr(material_pack, "aggregation_run_id", None)) or "",
        strategy_signal_run_id=optional_text(getattr(material_pack, "strategy_signal_run_id", None)) or "",
        snapshot_id=optional_text(getattr(material_pack, "snapshot_id", None)) or "",
        symbol=optional_text(getattr(material_pack, "symbol", None)) or "",
        base_interval=optional_text(getattr(material_pack, "base_interval", None)) or "",
        higher_interval=optional_text(getattr(material_pack, "higher_interval", None)) or "",
        review_schema_version=str(
            getattr(provider_metadata, "review_schema_version", settings.model_review_schema_version)
        ),
        prompt_template_version=str(
            getattr(provider_metadata, "prompt_template_version", settings.model_review_prompt_template_version)
        ),
        model_provider=provider_metadata.provider_name,
        model_name=provider_metadata.model_name,
        model_version=provider_metadata.model_version,
        review_mode=str(getattr(provider_metadata, "analysis_mode", MODEL_REVIEW_MODE_DEFAULT)),
        model_key=str(getattr(provider_metadata, "model_key", MODEL_REVIEW_MODEL_KEY_DEFAULT)),
        model_role=str(getattr(provider_metadata, "model_role", MODEL_REVIEW_MODEL_ROLE_DEFAULT)),
        analysis_mode=str(getattr(provider_metadata, "analysis_mode", MODEL_REVIEW_MODE_DEFAULT)),
        chain_id=getattr(provider_metadata, "chain_id", None),
        chain_step=getattr(provider_metadata, "chain_step", None),
        parent_model_analysis_run_id=getattr(provider_metadata, "parent_model_analysis_run_id", None),
        comparison_group_id=getattr(provider_metadata, "comparison_group_id", None),
        status=status,
        input_material_hash=prompt.input_material_hash if prompt else "",
        input_summary_json=prompt.input_summary if prompt else {},
        input_char_count=prompt.input_char_count if prompt else 0,
        input_byte_count=prompt.input_byte_count if prompt else 0,
        output_char_count=provider_result.output_char_count if provider_result else 0,
        output_byte_count=provider_result.output_byte_count if provider_result else 0,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        human_review_required=human_review_required,
        trigger_source=request.trigger_source,
        created_by=request.created_by,
        trace_id=trace_id,
        error_code=error_code,
        error_message=error_message,
        hermes_enabled=settings.model_review_hermes_enabled,
        hermes_status=None,
        hermes_message=None,
        hermes_error=None,
        hermes_sent_at_utc=None,
        profile_version=optional_text(getattr(provider_metadata, "profile_version", None)),
        profile_hash=optional_text(getattr(provider_metadata, "profile_hash", None)),
        api_style=optional_text(getattr(provider_metadata, "api_style", None)),
        provider_request_id=optional_text(getattr(provider_result, "provider_request_id", None)),
        finish_reason=optional_text(getattr(provider_result, "finish_reason", None)),
        request_payload_hash=optional_text(getattr(provider_metadata, "request_payload_hash", None)),
        rendered_prompt_hash=_sha256_text(prompt.prompt_text) if prompt else None,
        prompt_template_hash=optional_text(getattr(provider_metadata, "prompt_template_hash", None)),
        request_params_summary_json=dict(getattr(provider_metadata, "request_params_summary_json", {}) or {}),
        capabilities_json=dict(getattr(provider_metadata, "capabilities_json", {}) or {}),
        response_metadata_summary_json=dict(getattr(provider_result, "response_metadata", {}) or {}),
        provider_usage_json=dict(getattr(provider_metadata, "provider_usage_json", {}) or {}),
        raw_request_hash=optional_text(getattr(provider_metadata, "raw_request_hash", None)),
        raw_response_hash=optional_text(getattr(provider_result, "raw_response_hash", None)),
        raw_request_storage_ref=optional_text(getattr(provider_metadata, "raw_request_storage_ref", None)),
        raw_response_storage_ref=optional_text(getattr(provider_metadata, "raw_response_storage_ref", None)),
        raw_response_char_count=int(getattr(provider_result, "raw_response_char_count", 0) or 0),
        raw_response_byte_count=int(getattr(provider_result, "raw_response_byte_count", 0) or 0),
        input_token_count=getattr(provider_metadata, "input_token_count", None),
        output_token_count=getattr(provider_metadata, "output_token_count", None),
        total_token_count=getattr(provider_metadata, "total_token_count", None),
        estimated_cost=optional_text(getattr(provider_metadata, "estimated_cost", None)),
        cost_currency=optional_text(getattr(provider_metadata, "cost_currency", None)),
    )


def build_result_payload(
    *,
    result_id: str,
    model_analysis_run_id: str,
    review_version_key: str,
    material_pack: Any,
    normalized: Mapping[str, Any],
) -> ModelAnalysisResultPersistencePayload:
    """Build one final-row repository payload."""

    return ModelAnalysisResultPersistencePayload(
        model_analysis_result_id=result_id,
        model_analysis_run_id=model_analysis_run_id,
        review_version_key=review_version_key,
        material_pack_id=str(getattr(material_pack, "material_pack_id")),
        aggregation_run_id=str(getattr(material_pack, "aggregation_run_id")),
        strategy_signal_run_id=str(getattr(material_pack, "strategy_signal_run_id")),
        review_decision=str(normalized["review_decision"]),
        human_review_required=bool(normalized["human_review_required"]),
        evidence_quality=str(normalized["evidence_quality"]),
        logic_consistency=str(normalized["logic_consistency"]),
        risk_acceptability=str(normalized["risk_acceptability"]),
        strategy_conflict_level=str(normalized["strategy_conflict_level"]),
        missing_evidence_json=list(normalized.get("missing_evidence", [])),
        rejection_reasons_json=list(normalized.get("rejection_reasons", [])),
        risk_warnings_json=list(normalized.get("risk_warnings", [])),
        conditions_to_reconsider_json=list(normalized.get("conditions_to_reconsider", [])),
        validation_focus_json=list(normalized.get("validation_focus", [])),
        human_review_questions_json=list(normalized.get("human_review_questions", [])),
        summary_text=str(normalized.get("summary_text", "")),
        not_trading_advice_text=str(normalized.get("not_trading_advice_text", "")),
    )


def is_human_review_decision(review_decision: str) -> bool:
    """Return whether a schema-valid decision asks for human review."""

    return review_decision == ReviewDecision.HUMAN_REVIEW_REQUIRED.value


def optional_text(value: Any) -> str | None:
    """Return `None` for missing values and `str(value)` otherwise."""

    if value is None:
        return None
    return str(value)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "build_blocked_result",
    "build_failed_result",
    "build_invalid_request_result",
    "build_model_analysis_result_id",
    "build_model_analysis_run_id",
    "build_result_payload",
    "build_review_version_key",
    "build_run_payload",
    "build_skipped_result_from_existing",
    "build_success_result",
    "is_human_review_decision",
    "optional_text",
]
