"""Compact summary builders for stage-20A model review aggregation.

This file belongs to `app/model_review_aggregation`. It summarizes already
validated stage-19 result rows into bounded fields for stage 20A and future
stage 21. It does not call external services, databases, Redis, Hermes, large
models, formal Kline tables, or trading execution capabilities.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from app.model_review_aggregation.candidate_rules import text_attr


def summarize_accepted_model_results(candidates: Sequence[Any]) -> Mapping[str, Any]:
    """Return compact consensus and evidence summaries for accepted rows."""

    if not candidates:
        return empty_summaries()
    review_decisions = [text_attr(candidate.model_analysis_result, "review_decision") for candidate in candidates]
    evidence_values = [text_attr(candidate.model_analysis_result, "evidence_quality") for candidate in candidates]
    risk_values = [text_attr(candidate.model_analysis_result, "risk_acceptability") for candidate in candidates]
    conflict_values = [text_attr(candidate.model_analysis_result, "strategy_conflict_level") for candidate in candidates]
    missing_evidence: list[Any] = []
    risk_warnings: list[Any] = []
    human_questions: list[Any] = []
    for candidate in candidates:
        result = candidate.model_analysis_result
        missing_evidence.extend(json_list(getattr(result, "missing_evidence_json", "[]")))
        risk_warnings.extend(json_list(getattr(result, "risk_warnings_json", "[]")))
        human_questions.extend(json_list(getattr(result, "human_review_questions_json", "[]")))
    model_disagreement = {
        "review_decision_values": sorted(set(review_decisions)),
        "evidence_quality_values": sorted(set(evidence_values)),
        "risk_acceptability_values": sorted(set(risk_values)),
        "strategy_conflict_values": sorted(set(conflict_values)),
        "has_disagreement": any(
            len(set(values)) > 1
            for values in (review_decisions, evidence_values, risk_values, conflict_values)
        ),
    }
    model_consensus_level = "single_model" if len(candidates) == 1 else (
        "disagreement" if model_disagreement["has_disagreement"] else "consensus"
    )
    return {
        "review_decision_summary": summary_value(review_decisions),
        "evidence_quality_summary": summary_value(evidence_values),
        "risk_acceptability_summary": summary_value(risk_values),
        "strategy_conflict_summary": summary_value(conflict_values),
        "model_consensus_level": model_consensus_level,
        "allowed_advice_mode": allowed_advice_mode(risk_values, evidence_values),
        "model_disagreement_json": model_disagreement,
        "risk_warnings_json": bounded_list(risk_warnings),
        "missing_evidence_json": bounded_list(missing_evidence),
        "human_review_questions_json": bounded_list(human_questions),
    }


def empty_summaries() -> Mapping[str, Any]:
    """Return default summaries when no model review result is usable."""

    return {
        "review_decision_summary": "no_model_review_result",
        "evidence_quality_summary": "no_model_review_result",
        "risk_acceptability_summary": "no_model_review_result",
        "strategy_conflict_summary": "no_model_review_result",
        "model_consensus_level": "none",
        "allowed_advice_mode": "wait_only",
        "model_disagreement_json": {"has_disagreement": False},
        "risk_warnings_json": [],
        "missing_evidence_json": [],
        "human_review_questions_json": [],
    }


def build_model_results_summary(candidates: Sequence[Any]) -> Mapping[str, Any]:
    """Return bounded accepted-result metadata without raw model content."""

    items: list[Mapping[str, Any]] = []
    for candidate in candidates:
        run = candidate.model_analysis_run
        result = candidate.model_analysis_result
        items.append(
            {
                "model_analysis_run_id": text_attr(run, "model_analysis_run_id"),
                "model_analysis_result_id": text_attr(result, "model_analysis_result_id"),
                "material_pack_id": text_attr(result, "material_pack_id"),
                "model_provider": text_attr(run, "model_provider"),
                "model_name": text_attr(run, "model_name"),
                "model_key": text_attr(run, "model_key"),
                "model_role": text_attr(run, "model_role"),
                "review_decision": text_attr(result, "review_decision"),
                "evidence_quality": text_attr(result, "evidence_quality"),
                "risk_acceptability": text_attr(result, "risk_acceptability"),
                "strategy_conflict_level": text_attr(result, "strategy_conflict_level"),
                "human_review_required": bool(getattr(result, "human_review_required", False)),
                "created_at_utc": getattr(result, "created_at_utc", None),
            }
        )
    return {"accepted_results": items}


def build_summary_text(
    *,
    prefix: str,
    review_decision: str,
    evidence_quality: str,
    risk_acceptability: str,
    conflict_level: str,
) -> str:
    """Return the bounded human-readable stage-20A summary text."""

    return (
        f"{prefix} 聚合摘要：review_decision={review_decision}；"
        f"evidence_quality={evidence_quality}；risk_acceptability={risk_acceptability}；"
        f"strategy_conflict_level={conflict_level}。该输出不是最终交易建议。"
    )


def summary_value(values: Sequence[str]) -> str:
    """Summarize one model-result dimension across accepted rows."""

    filtered = [value for value in values if value]
    if not filtered:
        return "unknown"
    unique_values = sorted(set(filtered))
    if len(unique_values) == 1:
        return unique_values[0]
    return "disagreement:" + ",".join(unique_values)


def allowed_advice_mode(risk_values: Sequence[str], evidence_values: Sequence[str]) -> str:
    """Return the future-stage advice mode boundary implied by model review."""

    lowered_risk = {value.lower() for value in risk_values}
    lowered_evidence = {value.lower() for value in evidence_values}
    if "unacceptable" in lowered_risk or "insufficient" in lowered_evidence:
        return "wait_only"
    return "review_summary_only"


def json_list(raw_value: Any) -> list[Any]:
    """Decode a small JSON list field from stage-19 result rows."""

    if isinstance(raw_value, list):
        return raw_value
    if not raw_value:
        return []
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def bounded_list(values: Sequence[Any], *, limit: int = 20) -> list[Any]:
    """Bound persisted JSON arrays so stage 20A never stores large dumps."""

    return list(values[:limit])


__all__ = [
    "allowed_advice_mode",
    "bounded_list",
    "build_model_results_summary",
    "build_summary_text",
    "empty_summaries",
    "json_list",
    "summarize_accepted_model_results",
    "summary_value",
]
