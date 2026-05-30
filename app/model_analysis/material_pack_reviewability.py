"""Stage-18 material-pack reviewability checks for stage 19.

This file belongs to `app/model_analysis`. It validates whether a stage-18
`analysis_material_pack` row can be consumed by the model review gate.

Called by `app/model_analysis/service.py` and tests. External services: none.
MySQL: none in this file. Redis: none. Hermes: none. DeepSeek: none. Trading
execution: none. It never reads Kline tables directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from app.model_analysis.material_input import (
    build_time_anchor_summary,
    extract_strategy_evidence,
    strategy_evidence_missing_fields,
    time_anchor_missing_fields,
)
from app.model_analysis.types import ModelAnalysisStatus

REVIEWABLE_MATERIAL_PACK_STATUSES = frozenset(
    {
        ModelAnalysisStatus.SUCCESS.value,
        ModelAnalysisStatus.PARTIAL_SUCCESS.value,
    }
)
PARTIAL_SUCCESS_REQUIRED_JSON_FIELDS = (
    "material_json",
    "summary_json",
    "validation_plan_json",
    "data_window_json",
    "future_leakage_guard_json",
)
PARTIAL_SUCCESS_QUESTION_FIELDS = (
    "question_json",
    "question_list_json",
    "stage19_question_json",
)


@dataclass(frozen=True)
class MaterialPackReviewability:
    """Internal reviewability check result for stage-18 material packs."""

    is_reviewable: bool
    error_code: str = ""
    message: str = ""
    error_message: str | None = None


def validate_material_pack_reviewability(material_pack: Any) -> MaterialPackReviewability:
    """Validate whether stage 19 may consume a stage-18 material pack."""

    status = str(getattr(material_pack, "status", "")).strip().lower()
    if status == ModelAnalysisStatus.SUCCESS.value:
        return _validate_24c_strategy_evidence_and_time(material_pack)
    if status != ModelAnalysisStatus.PARTIAL_SUCCESS.value:
        return MaterialPackReviewability(
            is_reviewable=False,
            error_code="material_pack_status_not_reviewable",
            message="analysis_material_pack status is not reviewable.",
            error_message=f"status={status or 'unknown'}",
        )
    partial_result = _validate_partial_success_material_pack(material_pack)
    if not partial_result.is_reviewable:
        return partial_result
    return _validate_24c_strategy_evidence_and_time(material_pack)


def _validate_24c_strategy_evidence_and_time(material_pack: Any) -> MaterialPackReviewability:
    """Require the public 23F evidence bridge and review time anchors."""

    strategy_evidence = extract_strategy_evidence(material_pack)
    if not strategy_evidence:
        return MaterialPackReviewability(
            is_reviewable=False,
            error_code="strategy_evidence_missing",
            message="analysis_material_pack strategy_evidence is required for 24C model review.",
            error_message="material_json.strategy_evidence is missing or empty",
        )
    source = str(strategy_evidence.get("source", "") or "")
    if source != "strategy_evidence_aggregation_result":
        return MaterialPackReviewability(
            is_reviewable=False,
            error_code="strategy_evidence_source_not_23f",
            message="analysis_material_pack strategy_evidence does not come from 23F aggregation.",
            error_message=f"strategy_evidence.source={source or 'unknown'}",
        )
    missing_evidence = strategy_evidence_missing_fields(strategy_evidence)
    if missing_evidence:
        return MaterialPackReviewability(
            is_reviewable=False,
            error_code="strategy_evidence_incomplete",
            message="analysis_material_pack strategy_evidence is incomplete.",
            error_message=f"missing_or_empty={', '.join(missing_evidence)}",
        )

    time_anchors = build_time_anchor_summary(material_pack)
    missing_time = time_anchor_missing_fields(time_anchors)
    if missing_time:
        return MaterialPackReviewability(
            is_reviewable=False,
            error_code="material_pack_time_anchor_missing",
            message="analysis_material_pack required time anchors are missing.",
            error_message=f"missing_or_empty={', '.join(missing_time)}",
        )
    freshness_status = str(time_anchors.get("data_freshness_status", "") or "")
    if freshness_status != "fresh":
        return MaterialPackReviewability(
            is_reviewable=False,
            error_code="stale_data",
            message="analysis_material_pack time anchors are not fresh enough for model review.",
            error_message=f"data_freshness_status={freshness_status}",
        )
    return MaterialPackReviewability(is_reviewable=True)


def _validate_partial_success_material_pack(material_pack: Any) -> MaterialPackReviewability:
    parsed_fields = {
        field_name: _parse_material_pack_json_field(material_pack, field_name)
        for field_name in PARTIAL_SUCCESS_REQUIRED_JSON_FIELDS
    }
    missing_core_fields = [
        field_name for field_name, value in parsed_fields.items() if not _has_non_empty_material(value)
    ]
    if not _has_question_material(material_pack, material_json=parsed_fields["material_json"]):
        missing_core_fields.append("question_json")
    if not str(getattr(material_pack, "snapshot_id", "") or "").strip():
        missing_core_fields.append("snapshot_id")
    if not str(getattr(material_pack, "strategy_signal_run_id", "") or "").strip():
        missing_core_fields.append("strategy_signal_run_id")

    summary_json = _as_mapping(parsed_fields["summary_json"])
    material_json = _as_mapping(parsed_fields["material_json"])
    strategy_conflict_points = _as_mapping(material_json.get("strategy_conflict_points"))
    failed_strategy_count = _first_int_value(
        "failed_strategy_count",
        strategy_conflict_points,
        material_json,
        summary_json,
        material_pack,
    )
    invalid_strategy_count = _first_int_value(
        "invalid_strategy_count",
        strategy_conflict_points,
        material_json,
        summary_json,
        material_pack,
    )
    if failed_strategy_count > 0 or invalid_strategy_count > 0:
        return MaterialPackReviewability(
            is_reviewable=False,
            error_code="material_pack_partial_failed_or_invalid_strategy",
            message=(
                "analysis_material_pack partial_success is not reviewable because strategy material "
                "contains failed or invalid results."
            ),
            error_message=(
                f"failed_strategy_count={failed_strategy_count}; "
                f"invalid_strategy_count={invalid_strategy_count}"
            ),
        )

    effective_strategy_count = _first_int_value(
        "effective_strategy_count",
        summary_json,
        strategy_conflict_points,
        material_json,
        material_pack,
    )
    if effective_strategy_count < 1:
        missing_core_fields.append("effective_strategy_count")

    if missing_core_fields:
        return MaterialPackReviewability(
            is_reviewable=False,
            error_code="material_pack_partial_core_incomplete",
            message=(
                "analysis_material_pack partial_success is not reviewable because core material "
                "is incomplete."
            ),
            error_message=f"missing_or_empty={', '.join(sorted(set(missing_core_fields)))}",
        )

    return MaterialPackReviewability(is_reviewable=True)


def _parse_material_pack_json_field(material_pack: Any, field_name: str) -> Any:
    value = getattr(material_pack, field_name, None)
    if isinstance(value, (Mapping, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _has_non_empty_material(value: Any) -> bool:
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _has_question_material(material_pack: Any, *, material_json: Any) -> bool:
    for field_name in PARTIAL_SUCCESS_QUESTION_FIELDS:
        if _has_non_empty_material(_parse_material_pack_json_field(material_pack, field_name)):
            return True
    material_mapping = _as_mapping(material_json)
    return _has_non_empty_material(material_mapping.get("question_list_for_stage19"))


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_int_value(field_name: str, *sources: Any) -> int:
    for source in sources:
        value = getattr(source, field_name, None)
        if isinstance(source, Mapping):
            value = source.get(field_name)
        parsed = _maybe_int(value)
        if parsed is not None:
            return parsed
    return 0


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["MaterialPackReviewability", "validate_material_pack_reviewability"]
