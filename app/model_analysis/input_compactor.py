"""24C model-review input compaction helpers.

This file belongs to `app/model_analysis`. It builds a bounded, model-facing
summary from stage-18 public material and 23F public strategy evidence.

Called by `app/model_analysis/prompt_builder.py::build_model_review_prompt`.
External services: none. MySQL: none. Redis: none. Hermes: none.
Large models: none. Trading execution: none.

This file does not re-run strategies, does not read strategy-private payloads,
does not generate advice, and does not send final strategy notifications.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

PROMPT_TARGET_CHAR_LIMIT = 8_000
PROMPT_HARD_CHAR_LIMIT = 10_000


def build_compacted_model_review_input_summary(
    *,
    material_pack: Any,
    material_json: Mapping[str, Any],
    summary_json: Mapping[str, Any],
    question_json: Mapping[str, Any],
    validation_plan_json: Mapping[str, Any],
    strategy_evidence: Mapping[str, Any],
    time_anchors: Mapping[str, Any],
    strategy_summaries: list[dict[str, Any]],
    truncated_strategy_count: int,
    original_material_json_char_count: int,
    original_strategy_evidence_char_count: int,
    aggressive: bool,
    emergency: bool = False,
) -> dict[str, Any]:
    """Build the model-facing 24C input summary from bounded public evidence.

    Parameters are already extracted from the material pack by the caller.
    Return value is JSON-ready and intentionally omits full material JSON and
    full 23F evidence JSON. Failure scenarios are represented as missing or
    truncated fields; the caller still applies reviewability and size guards.
    """

    evidence_summary = _compact_strategy_evidence_for_model(
        strategy_evidence,
        aggressive=aggressive,
        emergency=emergency,
    )
    legacy_strategy_limit = 2 if emergency else (4 if aggressive else 8)
    material_summary = _compact_material_summary_for_model(
        material_json=material_json,
        summary_json=summary_json,
        question_json=question_json,
        validation_plan_json=validation_plan_json,
        aggressive=aggressive,
        emergency=emergency,
    )
    compact_strategy_summaries = [
        _compact_legacy_strategy_summary(item, aggressive=aggressive, emergency=emergency)
        for item in strategy_summaries[:legacy_strategy_limit]
    ]
    compaction_mode = "emergency" if emergency else ("aggressive" if aggressive else "standard")
    return {
        "material_pack_id": str(getattr(material_pack, "material_pack_id", "")),
        "aggregation_run_id": str(getattr(material_pack, "aggregation_run_id", "")),
        "strategy_signal_run_id": str(getattr(material_pack, "strategy_signal_run_id", "")),
        "snapshot_id": str(getattr(material_pack, "snapshot_id", "")),
        "symbol": str(getattr(material_pack, "symbol", "")),
        "base_interval": str(getattr(material_pack, "base_interval", "")),
        "higher_interval": str(getattr(material_pack, "higher_interval", "")),
        "material_status": str(getattr(material_pack, "status", "")),
        "aggregation_version": str(getattr(material_pack, "aggregation_version", "")),
        "material_schema_version": str(getattr(material_pack, "material_schema_version", "")),
        "strategy_item_count": len(strategy_summaries),
        "truncated_strategy_count": truncated_strategy_count + max(len(strategy_summaries) - legacy_strategy_limit, 0),
        "strategy_summaries": compact_strategy_summaries,
        "time_anchors": _compact_time_anchors(time_anchors),
        "strategy_evidence": evidence_summary,
        "strategy_evidence_source": evidence_summary.get("source", ""),
        "strategy_evidence_aggregation_id": evidence_summary.get("aggregation_id", ""),
        "candidate_bias": evidence_summary.get("candidate_bias", ""),
        "decision_readiness": evidence_summary.get("decision_readiness", ""),
        "risk_gate_summary": evidence_summary.get("risk_gate_summary", {}),
        "evidence_missing": evidence_summary.get("evidence_missing", []),
        "model_review_focus": evidence_summary.get("model_review_focus", []),
        "material_summary": material_summary,
        "input_compaction": {
            "version": "24c_strategy_evidence_compaction_v1",
            "mode": compaction_mode,
            "target_char_limit": PROMPT_TARGET_CHAR_LIMIT,
            "hard_char_limit": PROMPT_HARD_CHAR_LIMIT,
            "material_json_chars_before_compaction": original_material_json_char_count,
            "strategy_evidence_chars_before_compaction": original_strategy_evidence_char_count,
            "full_material_json_not_sent": True,
            "full_strategy_evidence_not_sent": True,
        },
        "not_trading_advice": True,
    }


def json_char_count(value: Any) -> int:
    """Return a stable JSON character count for compaction diagnostics."""

    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")))


def compact_scalar(value: Any, *, max_chars: int = 300) -> Any:
    """Return a scalar safe for prompt input, truncating long strings."""

    if isinstance(value, (bool, int, float)) or value is None:
        return value
    text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def compact_list(value: Any, *, max_items: int = 5) -> list[Any]:
    """Return a short JSON-ready list."""

    if not isinstance(value, list):
        return []
    result: list[Any] = []
    for item in value[:max_items]:
        if isinstance(item, Mapping):
            result.append(compact_mapping(item, max_items=6))
        elif isinstance(item, list):
            result.append(compact_list(item, max_items=max_items))
        else:
            result.append(compact_scalar(item))
    return result


def compact_mapping(value: Mapping[str, Any], *, max_items: int = 12) -> dict[str, Any]:
    """Return a bounded mapping that preserves keys before truncation."""

    compact: dict[str, Any] = {}
    for index, (key, raw_value) in enumerate(value.items()):
        if index >= max_items:
            compact["truncated"] = True
            break
        if isinstance(raw_value, Mapping):
            compact[str(key)] = compact_mapping(raw_value, max_items=6)
        elif isinstance(raw_value, list):
            compact[str(key)] = compact_list(raw_value, max_items=6)
        else:
            compact[str(key)] = compact_scalar(raw_value)
    return compact


def as_mapping(value: Any) -> Mapping[str, Any]:
    """Return a mapping value or an empty mapping."""

    return value if isinstance(value, Mapping) else {}


def high_level_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    """Drop known large legacy strategy arrays from material summaries."""

    skipped = {"strategy_summaries", "strategies", "strategy_results"}
    return {str(key): raw_value for key, raw_value in value.items() if str(key) not in skipped}


def _compact_time_anchors(time_anchors: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "analysis_time_utc": compact_scalar(time_anchors.get("analysis_time_utc"), max_chars=40),
        "analysis_time_prc": compact_scalar(time_anchors.get("analysis_time_prc"), max_chars=40),
        "latest_base_kline_close_time_utc": compact_scalar(
            time_anchors.get("latest_base_kline_close_time_utc"),
            max_chars=40,
        ),
        "latest_higher_kline_close_time_utc": compact_scalar(
            time_anchors.get("latest_higher_kline_close_time_utc"),
            max_chars=40,
        ),
        "data_freshness_status": compact_scalar(time_anchors.get("data_freshness_status"), max_chars=40),
    }


def _compact_strategy_evidence_for_model(
    strategy_evidence: Mapping[str, Any],
    *,
    aggressive: bool,
    emergency: bool,
) -> dict[str, Any]:
    max_chain_items = 1 if emergency else (5 if aggressive else 8)
    max_summary_items = 1 if emergency else (3 if aggressive else 5)
    max_focus_items = 2 if emergency else 5
    compact = {
        "source": compact_scalar(strategy_evidence.get("source"), max_chars=80),
        "aggregation_id": compact_scalar(strategy_evidence.get("aggregation_id"), max_chars=80),
        "strategy_signal_run_id": compact_scalar(strategy_evidence.get("strategy_signal_run_id"), max_chars=80),
        "status": compact_scalar(strategy_evidence.get("status"), max_chars=40),
        "candidate_bias": compact_scalar(strategy_evidence.get("candidate_bias"), max_chars=40),
        "candidate_confidence": compact_scalar(strategy_evidence.get("candidate_confidence"), max_chars=40),
        "decision_readiness": compact_scalar(strategy_evidence.get("decision_readiness"), max_chars=80),
        "strategy_evidence_summary": _compact_strategy_evidence_summary(
            strategy_evidence.get("strategy_evidence_summary"),
            max_items=max_summary_items,
            max_evidence_items=max_summary_items,
            aggressive=aggressive,
            emergency=emergency,
        ),
        "decision_source_chain": _compact_decision_source_chain(
            strategy_evidence.get("decision_source_chain"),
            max_items=max_chain_items,
            aggressive=aggressive,
            emergency=emergency,
        ),
        "role_coverage_matrix": _compact_role_coverage_matrix(
            strategy_evidence.get("role_coverage_matrix"),
            max_roles=2 if emergency else (5 if aggressive else 8),
        ),
        "evidence_missing": _compact_evidence_missing(
            strategy_evidence.get("evidence_missing"),
            max_items=max_summary_items,
            emergency=emergency,
        ),
        "strategy_conflict_summary": _compact_conflict_summary(
            strategy_evidence.get("strategy_conflict_summary"),
            max_items=max_summary_items,
            emergency=emergency,
        ),
        "participation_summary": _compact_participation_summary(strategy_evidence.get("participation_summary")),
        "observe_only_summary": _compact_observe_only_summary(
            strategy_evidence.get("observe_only_summary"),
            max_items=max_summary_items,
            emergency=emergency,
        ),
        "risk_gate_summary": _compact_risk_gate_summary(
            strategy_evidence.get("risk_gate_summary"),
            emergency=emergency,
        ),
        "model_review_focus": _compact_focus_items(
            strategy_evidence.get("model_review_focus"),
            max_items=max_focus_items,
            emergency=emergency,
        ),
    }
    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def _compact_strategy_evidence_summary(
    value: Any,
    *,
    max_items: int,
    max_evidence_items: int,
    aggressive: bool,
    emergency: bool,
) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["truncated"] = True
                break
            if isinstance(item, list):
                result[str(key)] = [
                    _compact_evidence_item(
                        child,
                        max_evidence_items=max_evidence_items,
                        aggressive=aggressive,
                        emergency=emergency,
                    )
                    for child in item[:max_items]
                ]
            elif isinstance(item, Mapping):
                result[str(key)] = _compact_evidence_item(
                    item,
                    max_evidence_items=max_evidence_items,
                    aggressive=aggressive,
                    emergency=emergency,
                )
            else:
                result[str(key)] = compact_scalar(item, max_chars=100 if emergency else 180)
        return result
    if isinstance(value, list):
        return [
            _compact_evidence_item(
                item,
                max_evidence_items=max_evidence_items,
                aggressive=aggressive,
                emergency=emergency,
            )
            for item in value[:max_items]
        ]
    return compact_scalar(value, max_chars=120 if emergency else 220)


def _compact_evidence_item(
    value: Any,
    *,
    max_evidence_items: int,
    aggressive: bool,
    emergency: bool,
) -> Any:
    if not isinstance(value, Mapping):
        return compact_scalar(value, max_chars=90 if emergency else 160)
    reason_chars = 50 if emergency else (120 if aggressive else 180)
    compact: dict[str, Any] = {}
    keys = (
        "strategy_name",
        "strategy_version",
        "strategy_role",
        "provides",
        "participation_mode",
        "decision_weight",
        "can_veto",
        "veto_scope",
        "candidate_bias",
        "filter_decision",
        "risk_gate_decision",
        "risk_scope",
        "risk_level",
        "context_summary",
        "summary",
        "reason_text",
        "reason_codes",
        "evidence_items",
        "supporting_evidence",
        "opposing_evidence",
        "key_levels",
    )
    if emergency:
        keys = (
            "strategy_name",
            "strategy_role",
            "participation_mode",
            "candidate_bias",
            "filter_decision",
            "risk_gate_decision",
            "risk_scope",
            "risk_level",
            "summary",
            "reason_text",
            "reason_codes",
            "evidence_items",
        )
    for key in keys:
        if key not in value:
            continue
        raw_value = value.get(key)
        if key in {"reason_codes", "provides"}:
            compact[key] = compact_list(raw_value, max_items=max_evidence_items)
        elif key in {"evidence_items", "supporting_evidence", "opposing_evidence"}:
            compact[key] = _compact_evidence_text_list(raw_value, max_items=max_evidence_items, emergency=emergency)
        elif key == "key_levels":
            compact[key] = _compact_key_levels(raw_value, max_items=max_evidence_items, emergency=emergency)
        elif key in {"summary", "reason_text", "context_summary"}:
            compact[key] = compact_scalar(raw_value, max_chars=reason_chars)
        else:
            compact[key] = compact_scalar(raw_value, max_chars=80)
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


def _compact_decision_source_chain(
    value: Any,
    *,
    max_items: int,
    aggressive: bool,
    emergency: bool,
) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [
        _compact_evidence_item(
            item,
            max_evidence_items=1 if emergency else (2 if aggressive else 3),
            aggressive=aggressive,
            emergency=emergency,
        )
        for item in value[:max_items]
    ]


def _compact_role_coverage_matrix(value: Any, *, max_roles: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for index, (role, detail) in enumerate(value.items()):
        if index >= max_roles:
            result["truncated"] = True
            break
        if not isinstance(detail, Mapping):
            result[str(role)] = compact_scalar(detail, max_chars=80)
            continue
        role_item_limit = 2 if max_roles <= 2 else 5
        result[str(role)] = {
            key: compact_list(detail.get(key), max_items=role_item_limit)
            if isinstance(detail.get(key), list)
            else compact_scalar(detail.get(key), max_chars=80)
            for key in (
                "present",
                "status",
                "coverage_status",
                "provided",
                "provides",
                "required",
                "missing",
                "missing_provides",
            )
            if detail.get(key) not in (None, "", [], {})
        }
    return result


def _compact_evidence_missing(value: Any, *, max_items: int, emergency: bool) -> list[Any]:
    if not isinstance(value, list):
        if isinstance(value, Mapping):
            return [_compact_missing_item(value, emergency=emergency)]
        return []
    return [_compact_missing_item(item, emergency=emergency) for item in value[:max_items]]


def _compact_missing_item(value: Any, *, emergency: bool) -> Any:
    if not isinstance(value, Mapping):
        return compact_scalar(value, max_chars=50 if emergency else 140)
    return {
        key: compact_scalar(value.get(key), max_chars=50 if emergency else 140)
        for key in ("strategy_role", "role", "provides", "missing", "reason", "reason_code", "strategy_name")
        if value.get(key) not in (None, "", [], {})
    }


def _compact_conflict_summary(value: Any, *, max_items: int, emergency: bool) -> list[Any]:
    if not isinstance(value, list):
        if isinstance(value, Mapping):
            return [_compact_conflict_item(value, emergency=emergency)]
        return []
    return [_compact_conflict_item(item, emergency=emergency) for item in value[:max_items]]


def _compact_conflict_item(value: Any, *, emergency: bool) -> Any:
    if not isinstance(value, Mapping):
        return compact_scalar(value, max_chars=50 if emergency else 160)
    return {
        key: compact_scalar(value.get(key), max_chars=50 if emergency else 160)
        for key in ("conflict_type", "strategy_role", "strategy_name", "reason", "summary", "severity")
        if value.get(key) not in (None, "", [], {})
    }


def _compact_participation_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (int, float, bool)) or item is None:
            result[str(key)] = item
        elif str(key) in {
            "observe_only",
            "evidence_only",
            "advisory",
            "decision_participant",
            "disabled",
            "failed",
            "invalid",
            "counts",
            "mode_counts",
        }:
            result[str(key)] = (
                compact_mapping(as_mapping(item), max_items=6)
                if isinstance(item, Mapping)
                else compact_scalar(item, max_chars=80)
            )
    return result


def _compact_observe_only_summary(value: Any, *, max_items: int, emergency: bool) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): compact_scalar(item, max_chars=80 if emergency else 140)
            if not isinstance(item, Mapping)
            else {
                sub_key: compact_scalar(item.get(sub_key), max_chars=80 if emergency else 140)
                for sub_key in ("strategy_name", "reason", "summary")
                if item.get(sub_key) not in (None, "", [], {})
            }
            for key, item in list(value.items())[:max_items]
        }
    if isinstance(value, list):
        return [
            _compact_evidence_item(item, max_evidence_items=1, aggressive=True, emergency=emergency)
            for item in value[:max_items]
        ]
    return compact_scalar(value, max_chars=100 if emergency else 180)


def _compact_risk_gate_summary(value: Any, *, emergency: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "risk_gate_decision",
        "risk_scope",
        "global_market_risk",
        "candidate_risk",
        "long_feasibility",
        "short_feasibility",
        "risk_level",
        "reason_codes",
        "reason_text",
        "summary",
    ):
        if key not in value:
            continue
        if key == "reason_codes":
            result[key] = compact_list(value.get(key), max_items=3)
        else:
            result[key] = compact_scalar(value.get(key), max_chars=60 if emergency else 180)
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _compact_focus_items(value: Any, *, max_items: int, emergency: bool) -> Any:
    if isinstance(value, list):
        return [compact_scalar(item, max_chars=60 if emergency else 180) for item in value[:max_items]]
    if isinstance(value, Mapping):
        return {
            str(key): compact_scalar(item, max_chars=60 if emergency else 180)
            for key, item in list(value.items())[:max_items]
        }
    return compact_scalar(value, max_chars=60 if emergency else 220)


def _compact_evidence_text_list(value: Any, *, max_items: int, emergency: bool) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [compact_scalar(item, max_chars=50 if emergency else 140) for item in value[:max_items]]


def _compact_key_levels(value: Any, *, max_items: int, emergency: bool) -> list[Any]:
    if not isinstance(value, list):
        return []
    compact_levels: list[Any] = []
    for item in value[:max_items]:
        if not isinstance(item, Mapping):
            compact_levels.append(compact_scalar(item, max_chars=80 if emergency else 120))
            continue
        compact_levels.append(
            {
                key: compact_scalar(item.get(key), max_chars=60)
                for key in (
                    "level_type",
                    "level_group",
                    "zone_low",
                    "zone_high",
                    "zone_mid",
                    "strength_score",
                    "confidence_score",
                    "role_flip_status",
                    "reason",
                )
                if item.get(key) not in (None, "", [], {})
            }
        )
    return compact_levels


def _compact_material_summary_for_model(
    *,
    material_json: Mapping[str, Any],
    summary_json: Mapping[str, Any],
    question_json: Mapping[str, Any],
    validation_plan_json: Mapping[str, Any],
    aggressive: bool,
    emergency: bool,
) -> dict[str, Any]:
    summary = {
        "kline_window_summary": _compact_kline_window_summary(material_json.get("kline_window_summary")),
        "math_summary": _compact_math_material_summary(material_json, aggressive=aggressive, emergency=emergency),
    }
    if emergency:
        return summary
    summary["material_summary"] = compact_mapping(high_level_summary(summary_json), max_items=5)
    summary["review_questions"] = compact_list(
        question_json.get("questions", []) if isinstance(question_json, dict) else [],
        max_items=4,
    )
    summary["validation_focus"] = compact_mapping(validation_plan_json, max_items=5)
    return summary


def _compact_kline_window_summary(value: Any) -> dict[str, Any]:
    window = as_mapping(value)
    base = as_mapping(window.get("latest_base_kline"))
    higher = as_mapping(window.get("latest_higher_kline"))
    return {
        "latest_base_kline": _compact_kline_item(base),
        "latest_higher_kline": _compact_kline_item(higher),
        "base_window_count": compact_scalar(window.get("base_window_count"), max_chars=20),
        "higher_window_count": compact_scalar(window.get("higher_window_count"), max_chars=20),
    }


def _compact_kline_item(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: compact_scalar(value.get(key), max_chars=40)
        for key in ("open_time_utc", "open_time_ms", "close")
        if value.get(key) not in (None, "", [], {})
    }


def _compact_math_material_summary(
    material_json: Mapping[str, Any],
    *,
    aggressive: bool,
    emergency: bool,
) -> dict[str, Any]:
    max_items = 1 if emergency else (2 if aggressive else 3)
    return {
        "swing": _compact_selected_mapping(
            as_mapping(material_json.get("swing")),
            ("structure_state", "market_bias", "confidence_score", "reason_text", "major_points"),
            max_items=max_items,
        ),
        "volatility": _compact_selected_mapping(
            as_mapping(material_json.get("volatility")),
            ("volatility_state", "range_expansion_state", "atr_percent", "risk_level", "reason_text"),
            max_items=max_items,
        ),
        "support_resistance": _compact_selected_mapping(
            as_mapping(material_json.get("support_resistance")),
            ("nearest_support", "nearest_resistance", "key_levels", "reason_text"),
            max_items=max_items,
        ),
    }


def _compact_selected_mapping(value: Mapping[str, Any], keys: tuple[str, ...], *, max_items: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        if key not in value:
            continue
        raw_value = value.get(key)
        if isinstance(raw_value, list):
            result[key] = compact_list(raw_value, max_items=max_items)
        elif isinstance(raw_value, Mapping):
            result[key] = compact_mapping(raw_value, max_items=max_items)
        else:
            result[key] = compact_scalar(raw_value, max_chars=160)
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _compact_legacy_strategy_summary(
    value: Mapping[str, Any],
    *,
    aggressive: bool,
    emergency: bool,
) -> dict[str, Any]:
    max_reason_items = 1 if emergency else (2 if aggressive else 3)
    text_chars = 50 if emergency else (120 if aggressive else 180)
    compact: dict[str, Any] = {}
    for key in (
        "strategy_name",
        "strategy_version",
        "strategy_role",
        "analysis_hypothesis_direction",
        "evidence_quality",
        "risk_level",
        "summary",
        "reason_codes",
        "missing_evidence",
    ):
        raw_value = value.get(key)
        if key in {"reason_codes", "missing_evidence"}:
            compact[key] = compact_list(raw_value, max_items=max_reason_items)
        elif key == "summary":
            compact[key] = compact_scalar(raw_value, max_chars=text_chars)
        else:
            compact[key] = compact_scalar(raw_value, max_chars=80)
    return {key: item for key, item in compact.items() if item not in (None, "", [], {})}


__all__ = [
    "PROMPT_HARD_CHAR_LIMIT",
    "PROMPT_TARGET_CHAR_LIMIT",
    "as_mapping",
    "build_compacted_model_review_input_summary",
    "compact_list",
    "compact_mapping",
    "compact_scalar",
    "high_level_summary",
    "json_char_count",
]
