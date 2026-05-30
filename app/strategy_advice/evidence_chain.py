"""Evidence-chain summary helpers for stage-24D strategy advice display.

This file belongs to `app/strategy_advice`. It converts already persisted
stage-23F strategy evidence aggregation rows and stage-24C model-review rows
into a bounded summary for stage-21 advice notification payloads.

Called by `app/strategy_advice/service.py::StrategyAdviceService`. It does not
read databases by itself, does not re-run strategy aggregation, does not call
model providers, does not send Hermes, does not read Redis, and does not touch
Kline tables. It also does not read any strategy-private payload fields or
produce executable trading instructions.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping

EVIDENCE_CHAIN_SUMMARY_SCHEMA_VERSION = "strategy_advice_evidence_chain_summary_v1"

LOW_WEIGHT_QUALITY_FLAGS = frozenset(
    {
        "low_quality",
        "summary_only",
        "missing_evidence_refs",
        "missing_strongest_counterargument",
        "possible_23f_restatement",
        "missing_time_freshness_assessment",
    }
)
REJECTED_ERROR_CODES = frozenset(
    {
        "boundary_violation",
        "parse_failed",
        "schema_invalid",
        "schema_missing_required_field",
        "schema_invalid_enum_value",
        "model_call_skipped",
        "real_model_disabled",
        "stale_data",
        "input_char_limit_exceeded",
    }
)
REJECTED_BOUNDARY_CODES = frozenset({"boundary_violation"})


def build_evidence_chain_summary(
    *,
    strategy_evidence_row: Any | None,
    model_review_candidates: tuple[Any, ...],
    material_pack_id: str,
    strategy_signal_run_id: str | None,
) -> dict[str, Any]:
    """Build a compact 24D summary for 21A notification payloads.

    Parameters: a stage-23F row or `None`, model-review candidate rows from
    stage 24C, and the source material/run ids. Return value is JSON-ready and
    bounded. Failure scenarios are represented as missing/rejected summaries so
    advice generation can remain transparent without pretending evidence exists.
    External effects: none.
    """

    strategy_chain = _build_strategy_evidence_chain(
        strategy_evidence_row,
        fallback_strategy_signal_run_id=strategy_signal_run_id,
    )
    model_review = _build_model_review_summary(
        model_review_candidates,
        material_pack_id=material_pack_id,
        expected_strategy_evidence_aggregation_id=_text(strategy_chain.get("aggregation_id")),
    )
    return {
        "schema_version": EVIDENCE_CHAIN_SUMMARY_SCHEMA_VERSION,
        "strategy_evidence_chain": strategy_chain,
        "model_review_summary": model_review,
        "strategy_evidence_status": strategy_chain.get("status", "missing"),
        "model_review_status": model_review.get("status", "missing"),
        "model_review_adoption_status": model_review.get("adoption_status", "missing"),
        "not_trading_advice": True,
        "is_final_trading_advice": False,
        "is_trading_signal": False,
        "is_executable": False,
        "auto_trading_allowed": False,
    }


def build_missing_evidence_chain_summary(
    *,
    material_pack_id: str,
    strategy_signal_run_id: str | None,
    reason: str,
) -> dict[str, Any]:
    """Return a transparent summary when 24D lookup itself cannot complete."""

    return {
        "schema_version": EVIDENCE_CHAIN_SUMMARY_SCHEMA_VERSION,
        "strategy_evidence_chain": {
            "source": "missing",
            "aggregation_id": None,
            "strategy_signal_run_id": strategy_signal_run_id,
            "status": "missing",
            "missing_reason": reason,
            "not_trading_advice": True,
        },
        "model_review_summary": {
            "source": "missing",
            "material_pack_id": material_pack_id,
            "status": "missing",
            "adoption_status": "missing",
            "adoption_reason": reason,
            "model_review_adoptable": False,
        },
        "strategy_evidence_status": "missing",
        "model_review_status": "missing",
        "model_review_adoption_status": "missing",
        "not_trading_advice": True,
        "is_final_trading_advice": False,
        "is_trading_signal": False,
        "is_executable": False,
        "auto_trading_allowed": False,
    }


def _build_strategy_evidence_chain(
    row: Any | None,
    *,
    fallback_strategy_signal_run_id: str | None,
) -> dict[str, Any]:
    if row is None:
        return {
            "source": "missing",
            "aggregation_id": None,
            "strategy_signal_run_id": fallback_strategy_signal_run_id,
            "status": "missing",
            "missing_reason": "strategy_evidence_missing",
            "key_strategy_points": [],
            "strategy_conflicts": [],
            "risk_gate_summary": {},
            "evidence_missing": [{"reason_code": "strategy_evidence_missing"}],
            "model_review_focus": [],
            "not_trading_advice": True,
        }

    evidence_summary = _json_mapping(getattr(row, "strategy_evidence_summary_json", "{}"))
    source_chain = _json_list(getattr(row, "decision_source_chain_json", "[]"))
    conflicts = _json_any_list(getattr(row, "strategy_conflict_summary_json", "[]"))
    missing = _json_any_list(getattr(row, "evidence_missing_json", "[]"))
    risk_gate = _json_mapping(getattr(row, "risk_gate_summary_json", "{}"))
    focus = _json_any_list(getattr(row, "model_review_focus_json", "[]"))
    return {
        "source": "strategy_evidence_aggregation_result",
        "aggregation_id": _text(getattr(row, "aggregation_id", "")),
        "strategy_signal_run_id": _text(getattr(row, "strategy_signal_run_id", "")) or fallback_strategy_signal_run_id,
        "status": _text(getattr(row, "status", "")),
        "candidate_bias": _text(getattr(row, "candidate_bias", "")),
        "candidate_confidence": _number_text(getattr(row, "candidate_confidence", "")),
        "decision_readiness": _text(getattr(row, "decision_readiness", "")),
        "key_strategy_points": _key_strategy_points(
            decision_source_chain=source_chain,
            evidence_summary=evidence_summary,
        ),
        "strategy_conflicts": [_compact_mapping(item, max_items=8) for item in missing_or_list(conflicts)[:3]],
        "risk_gate_summary": _compact_risk_gate_summary(risk_gate),
        "evidence_missing": [_compact_mapping(item, max_items=8) for item in missing_or_list(missing)[:3]],
        "model_review_focus": [_compact_scalar(item, max_chars=180) for item in missing_or_list(focus)[:5]],
        "trace_id": _text(getattr(row, "trace_id", "")),
        "not_trading_advice": bool(getattr(row, "not_trading_advice", True)),
    }


def _build_model_review_summary(
    candidates: tuple[Any, ...],
    *,
    material_pack_id: str,
    expected_strategy_evidence_aggregation_id: str,
) -> dict[str, Any]:
    prepared = [
        _prepare_model_review_candidate(candidate, expected_strategy_evidence_aggregation_id)
        for candidate in candidates
    ]
    prepared = [item for item in prepared if item is not None]
    if not prepared:
        return {
            "source": "missing",
            "material_pack_id": material_pack_id,
            "status": "missing",
            "adoption_status": "missing",
            "adoption_reason": "model_review_missing",
            "model_review_adoptable": False,
        }

    matched = [item for item in prepared if item["aggregation_match"] == "matched"]
    selection_pool = matched if matched else prepared
    selected = sorted(selection_pool, key=_model_review_sort_key)[0]
    return selected["summary"]


def _prepare_model_review_candidate(
    candidate: Any,
    expected_strategy_evidence_aggregation_id: str,
) -> dict[str, Any] | None:
    run_row, result_row = _split_model_review_candidate(candidate)
    if run_row is None and result_row is None:
        return None

    input_summary = _json_mapping(getattr(run_row, "input_summary_json", "{}")) if run_row is not None else {}
    response_metadata = (
        _json_mapping(getattr(run_row, "response_metadata_summary_json", "{}")) if run_row is not None else {}
    )
    review_payload = _extract_review_payload_24c(result_row)
    strategy_evidence_id = (
        _text(input_summary.get("strategy_evidence_aggregation_id"))
        or _text(_mapping(input_summary.get("strategy_evidence")).get("aggregation_id"))
        or _text(review_payload.get("strategy_evidence_aggregation_id"))
    )
    aggregation_match = "not_required"
    if expected_strategy_evidence_aggregation_id:
        aggregation_match = (
            "matched"
            if strategy_evidence_id == expected_strategy_evidence_aggregation_id
            else "not_verified"
        )

    adoption_status, adoption_reason = _model_review_adoption_status(
        run_row=run_row,
        result_row=result_row,
        review_payload=review_payload,
        response_metadata=response_metadata,
    )
    summary = _model_review_summary_from_candidate(
        run_row=run_row,
        result_row=result_row,
        review_payload=review_payload,
        response_metadata=response_metadata,
        strategy_evidence_id=strategy_evidence_id,
        adoption_status=adoption_status,
        adoption_reason=adoption_reason,
        aggregation_match=aggregation_match,
    )
    return {
        "summary": summary,
        "category": _selection_category(summary),
        "created_at_sort": _created_at_sort_value(run_row=run_row, result_row=result_row),
        "aggregation_match": aggregation_match,
    }


def _model_review_adoption_status(
    *,
    run_row: Any | None,
    result_row: Any | None,
    review_payload: Mapping[str, Any],
    response_metadata: Mapping[str, Any],
) -> tuple[str, str]:
    run_status = _text(getattr(run_row, "status", ""))
    run_error = _text(getattr(run_row, "error_code", ""))
    schema_error = _text(response_metadata.get("schema_error_code"))
    boundary_flags = _list_value(review_payload.get("boundary_flags")) or _list_value(
        response_metadata.get("boundary_flags")
    )
    quality_flags = _list_value(review_payload.get("quality_flags")) or _list_value(
        response_metadata.get("quality_flags")
    )
    is_mock = _is_mock_model(run_row)

    if result_row is None:
        return "rejected", run_error or "model_review_no_final_result"
    if run_status and run_status != "success":
        return "rejected", run_error or f"model_review_run_{run_status}"
    if not review_payload:
        return "rejected", "model_review_24c_payload_missing"
    if schema_error:
        return "rejected", schema_error
    if run_error in REJECTED_ERROR_CODES:
        return "rejected", run_error
    boundary_reason = _boundary_rejection_reason(boundary_flags)
    if boundary_reason:
        return "rejected", boundary_reason
    if not _safety_flags_are_false(run_row):
        return "rejected", "model_safety_flags_not_false"
    if is_mock:
        return "test_only", "mock_review_is_test_only"
    low_weight_reason = _low_weight_quality_reason(quality_flags)
    if low_weight_reason:
        return "low_weight", low_weight_reason
    return "adopted", "usable_model_review"


def _model_review_summary_from_candidate(
    *,
    run_row: Any | None,
    result_row: Any | None,
    review_payload: Mapping[str, Any],
    response_metadata: Mapping[str, Any],
    strategy_evidence_id: str,
    adoption_status: str,
    adoption_reason: str,
    aggregation_match: str,
) -> dict[str, Any]:
    boundary_flags = _list_value(review_payload.get("boundary_flags")) or _list_value(
        response_metadata.get("boundary_flags")
    )
    quality_flags = _list_value(review_payload.get("quality_flags")) or _list_value(
        response_metadata.get("quality_flags")
    )
    return {
        "source": "model_analysis_result" if result_row is not None else "model_analysis_run",
        "model_analysis_run_id": _text(getattr(run_row, "model_analysis_run_id", "")),
        "model_analysis_result_id": _text(getattr(result_row, "model_analysis_result_id", "")),
        "material_pack_id": _text(getattr(run_row, "material_pack_id", ""))
        or _text(getattr(result_row, "material_pack_id", "")),
        "strategy_evidence_aggregation_id": strategy_evidence_id,
        "strategy_signal_run_id": _text(getattr(run_row, "strategy_signal_run_id", ""))
        or _text(getattr(result_row, "strategy_signal_run_id", "")),
        "provider": _text(getattr(run_row, "model_provider", "")),
        "model_key": _text(getattr(run_row, "model_key", "")),
        "model_name": _text(getattr(run_row, "model_name", "")),
        "model_version": _text(getattr(run_row, "model_version", "")),
        "profile_hash": _text(getattr(run_row, "profile_hash", "")),
        "model_role": _text(getattr(run_row, "model_role", "")),
        "analysis_mode": _text(getattr(run_row, "analysis_mode", "")),
        "status": _text(getattr(run_row, "status", "")) or ("success" if result_row is not None else "missing"),
        "error_code": _text(getattr(run_row, "error_code", "")),
        "schema_error_code": _text(response_metadata.get("schema_error_code")),
        "review_decision": _model_review_decision_from_candidate(
            result_row=result_row,
            review_payload=review_payload,
        ),
        "evidence_quality": _text(getattr(result_row, "evidence_quality", "")),
        "risk_acceptability": _text(getattr(result_row, "risk_acceptability", "")),
        "strategy_conflict_level": _text(getattr(result_row, "strategy_conflict_level", "")),
        "human_review_required": bool(getattr(result_row, "human_review_required", False)),
        "agreement_with_23f": _text(review_payload.get("agreement_with_23f")),
        "main_objection": _bounded_text(_text(review_payload.get("main_objection")), max_length=220),
        "strongest_counterargument": _bounded_text(
            _text(review_payload.get("strongest_counterargument")),
            max_length=220,
        ),
        "missing_evidence": [_compact_scalar(item, max_chars=160) for item in _list_value(review_payload.get("missing_evidence"))[:3]],
        "disputed_strategy_points": [
            _compact_scalar(item, max_chars=160) for item in _list_value(review_payload.get("disputed_strategy_points"))[:3]
        ],
        "recommendation_to_advice_layer": _text(review_payload.get("recommendation_to_advice_layer")),
        "evidence_refs": [_compact_scalar(item, max_chars=120) for item in _list_value(review_payload.get("evidence_refs"))[:5]],
        "quality_flags": quality_flags[:8],
        "boundary_flags": boundary_flags[:8],
        "adoption_status": adoption_status,
        "adoption_reason": adoption_reason,
        "model_review_adoptable": adoption_status == "adopted",
        "is_low_weight": adoption_status == "low_weight",
        "is_mock_review": _is_mock_model(run_row),
        "aggregation_match": aggregation_match,
        "not_trading_advice": True,
        "is_final_trading_advice": False,
        "is_trading_signal": False,
        "is_executable": False,
        "auto_trading_allowed": False,
    }


def _key_strategy_points(
    *,
    decision_source_chain: list[Any],
    evidence_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for item in decision_source_chain:
        if not isinstance(item, Mapping):
            continue
        points.append(_compact_strategy_point(item))
        if len(points) >= 5:
            return points
    for item in _iter_evidence_summary_items(evidence_summary):
        points.append(_compact_strategy_point(item))
        if len(points) >= 5:
            break
    return points


def _model_review_decision_from_candidate(
    *,
    result_row: Any | None,
    review_payload: Mapping[str, Any],
) -> str:
    """Prefer the persisted 24C result enum over legacy JSON compatibility fields."""

    persisted_decision = _text(getattr(result_row, "review_decision", ""))
    if persisted_decision:
        return persisted_decision
    return _text(review_payload.get("review_decision"))


def _compact_strategy_point(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "strategy_name": _text(item.get("strategy_name")),
            "strategy_role": _text(item.get("strategy_role")),
            "participation_mode": _text(item.get("participation_mode")),
            "candidate_bias": _text(item.get("candidate_bias")),
            "filter_decision": _text(item.get("filter_decision")),
            "risk_gate_decision": _text(item.get("risk_gate_decision")),
            "risk_scope": _text(item.get("risk_scope")),
            "summary": _bounded_text(
                _text(item.get("summary") or item.get("reason_text") or item.get("context_summary")),
                max_length=180,
            ),
            "reason_codes": _list_value(item.get("reason_codes"))[:3],
        }.items()
        if value not in ("", [], {})
    }


def _iter_evidence_summary_items(evidence_summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    for value in evidence_summary.values():
        if isinstance(value, Mapping):
            items.append(value)
        elif isinstance(value, list):
            items.extend(item for item in value if isinstance(item, Mapping))
    return items


def _compact_risk_gate_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _compact_scalar(value.get(key), max_chars=180)
        for key in (
            "risk_gate_decision",
            "risk_scope",
            "global_market_risk",
            "candidate_risk",
            "long_feasibility",
            "short_feasibility",
            "risk_level",
            "reason_text",
            "summary",
        )
        if value.get(key) not in (None, "", [], {})
    } | ({"reason_codes": _list_value(value.get("reason_codes"))[:3]} if value.get("reason_codes") else {})


def _selection_category(summary: Mapping[str, Any]) -> int:
    status = _text(summary.get("adoption_status"))
    if status == "adopted":
        return 0
    if status == "low_weight":
        return 1
    if status == "test_only":
        return 2
    return 3


def _model_review_sort_key(item: Mapping[str, Any]) -> tuple[int, str]:
    return (int(item.get("category", 9)), _text(item.get("created_at_sort")))


def _created_at_sort_value(*, run_row: Any | None, result_row: Any | None) -> str:
    value = getattr(result_row, "created_at_utc", None) or getattr(run_row, "created_at_utc", None)
    if isinstance(value, datetime):
        # Reverse chronological by sorting on inverted timestamp text.
        return f"{9999999999 - int(value.timestamp()):010d}"
    return _text(value)


def _split_model_review_candidate(candidate: Any) -> tuple[Any | None, Any | None]:
    run_row = getattr(candidate, "model_analysis_run", None)
    result_row = getattr(candidate, "model_analysis_result", None)
    if run_row is not None or result_row is not None:
        return run_row, result_row
    if hasattr(candidate, "model_analysis_run_id") and hasattr(candidate, "review_decision"):
        return None, candidate
    if hasattr(candidate, "model_analysis_run_id"):
        return candidate, None
    return None, None


def _extract_review_payload_24c(result_row: Any | None) -> Mapping[str, Any]:
    focus = _json_any_list(getattr(result_row, "validation_focus_json", "[]")) if result_row is not None else []
    for item in focus:
        if not isinstance(item, Mapping):
            continue
        payload = item.get("review_payload_24c")
        if isinstance(payload, Mapping):
            return payload
    return {}


def _boundary_rejection_reason(boundary_flags: list[Any]) -> str:
    for flag in boundary_flags:
        if isinstance(flag, Mapping):
            code = _text(flag.get("code"))
            reason = _text(flag.get("reason"))
            if code in REJECTED_BOUNDARY_CODES or reason in REJECTED_BOUNDARY_CODES:
                return code or reason
        elif _text(flag) in REJECTED_BOUNDARY_CODES:
            return _text(flag)
    return ""


def _low_weight_quality_reason(quality_flags: list[Any]) -> str:
    for flag in quality_flags:
        if isinstance(flag, Mapping):
            code = _text(flag.get("code"))
            if code in LOW_WEIGHT_QUALITY_FLAGS:
                return code
        elif _text(flag) in LOW_WEIGHT_QUALITY_FLAGS:
            return _text(flag)
    return ""


def _safety_flags_are_false(run_row: Any | None) -> bool:
    if run_row is None:
        return True
    return not any(
        bool(getattr(run_row, field_name, False))
        for field_name in (
            "is_final_trading_advice",
            "is_trading_signal",
            "is_executable",
            "auto_trading_allowed",
        )
    )


def _is_mock_model(run_row: Any | None) -> bool:
    provider = _text(getattr(run_row, "model_provider", "")).lower()
    model_key = _text(getattr(run_row, "model_key", "")).lower()
    model_name = _text(getattr(run_row, "model_name", "")).lower()
    return provider == "mock" or model_key == "mock_review" or model_name == "mock_review"


def missing_or_list(value: Any) -> list[Any]:
    """Return a list for renderer/payload compaction helpers."""

    return value if isinstance(value, list) else ([] if value in (None, "", {}) else [value])


def _json_mapping(value: Any) -> Mapping[str, Any]:
    parsed = _json_load(value, default={})
    return parsed if isinstance(parsed, Mapping) else {}


def _json_list(value: Any) -> list[Any]:
    parsed = _json_load(value, default=[])
    return parsed if isinstance(parsed, list) else []


def _json_any_list(value: Any) -> list[Any]:
    parsed = _json_load(value, default=[])
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, Mapping):
        return [parsed]
    return []


def _json_load(value: Any, *, default: Any) -> Any:
    if isinstance(value, (Mapping, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Mapping):
        return [dict(value)]
    if value in (None, ""):
        return []
    return [value]


def _compact_mapping(value: Any, *, max_items: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"value": _compact_scalar(value, max_chars=180)} if value not in (None, "") else {}
    result: dict[str, Any] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= max_items:
            result["truncated"] = True
            break
        if isinstance(item, Mapping):
            result[str(key)] = _compact_mapping(item, max_items=5)
        elif isinstance(item, list):
            result[str(key)] = [_compact_scalar(child, max_chars=120) for child in item[:4]]
        else:
            result[str(key)] = _compact_scalar(item, max_chars=180)
    return result


def _compact_scalar(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return _compact_mapping(value, max_items=5)
    text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 15]}...[truncated]"


def _bounded_text(value: str, *, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 15]}...[truncated]"


def _number_text(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return _text(value)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value"):
        return str(value.value)
    return str(value).strip()


__all__ = [
    "EVIDENCE_CHAIN_SUMMARY_SCHEMA_VERSION",
    "build_evidence_chain_summary",
    "build_missing_evidence_chain_summary",
]
