"""Prompt summary builder for stage-19 model analysis review gate.

This file belongs to `app/model_analysis`. It extracts a compact, bounded
review summary from a stage-18 `analysis_material_pack` row.

Called by `app/model_analysis/service.py`.
External services: none. MySQL: none. Redis: none. Hermes: none. Real model
calls: none. Trading execution: none.

The builder intentionally does not store or persist the full prompt. The
service may pass `prompt_text` to the mock provider, while persistence keeps
only `input_summary_json` plus size counters and a hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from app.core.config import AppSettings
from app.model_analysis.material_input import (
    build_time_anchor_summary,
    extract_strategy_evidence,
)
from app.model_analysis.schema_validator import ENUM_ALLOWED_VALUES
from app.model_analysis.types import PromptBuildResult

STRATEGY_SUMMARY_KEYS = (
    "strategy_name",
    "strategy_version",
    "strategy_role",
    "enabled",
    "status",
    "analysis_hypothesis_direction",
    "evidence_quality",
    "risk_level",
    "summary",
    "reason_codes",
    "missing_evidence",
)

PROMPT_STRATEGY_KEY_ALIASES = {
    "strategy_name": "name",
    "strategy_version": "version",
    "strategy_role": "role",
    "analysis_hypothesis_direction": "direction",
}

REVIEW_OUTPUT_JSON_SKELETON: dict[str, Any] = {
    "agreement_with_23f": "insufficient_evidence",
    "review_decision": "need_more_evidence",
    "main_objection": "",
    "strongest_counterargument": "",
    "missing_evidence": [],
    "disputed_strategy_points": [],
    "overestimated_evidence": [],
    "underestimated_evidence": [],
    "scenario_review": {
        "main_scenario": "",
        "opposite_scenario": "",
        "risk_scenario": "",
        "no_trade_scenario": "",
    },
    "discipline_check": {
        "chasing_risk": "unknown",
        "risk_reward_quality": "unknown",
        "stop_condition_clarity": "unknown",
        "overtrading_risk": "unknown",
    },
    "recommendation_to_advice_layer": "need_more_evidence",
    "evidence_refs": [],
    "time_freshness_assessment": "",
    "boundary_flags": [],
    "quality_flags": [],
    "confidence": "unknown",
    "summary": "",
    "not_trading_advice": True,
    "human_review_required": True,
    "is_final_trading_advice": False,
    "is_trading_signal": False,
    "is_executable": False,
    "auto_trading_allowed": False,
    # Backward-compatible stage-19 result fields. The validator also derives
    # these from 24C fields when a provider omits them.
    "evidence_quality": "unknown",
    "logic_consistency": "unknown",
    "risk_acceptability": "unknown",
    "strategy_conflict_level": "unknown",
    "rejection_reasons": [],
    "risk_warnings": [],
    "conditions_to_reconsider": [],
    "human_review_questions": [],
    "validation_focus": [],
    "summary_text": "",
}

REVIEW_OUTPUT_ALLOWED_ENUM_VALUES: dict[str, list[str]] = {
    "review_decision": [
        "accept",
        "downgrade",
        "risk_reject",
        "need_more_evidence",
        "accept_for_further_review",
        "reject_candidate",
        "require_more_evidence",
        "wait",
        "human_review_required",
        "blocked",
    ],
    "agreement_with_23f": ["agree", "partial", "disagree", "insufficient_evidence"],
    "recommendation_to_advice_layer": [
        "accept_for_further_review",
        "wait",
        "need_more_evidence",
        "risk_reject",
        "downgrade",
        "human_review_required",
    ],
    "discipline_check_value": ["ok", "caution", "poor", "unclear", "unknown"],
    "confidence": ["high", "medium", "low", "unknown"],
    **{field_name: sorted(values) for field_name, values in ENUM_ALLOWED_VALUES.items()},
}

REVIEW_DECISION_SEMANTIC_RULES: dict[str, Any] = {
    "require_more_evidence": {
        "human_review_required": True,
        "rule": "When review_decision=require_more_evidence, human_review_required must be true.",
    },
    "wait": {
        "human_review_required_false_allowed": True,
        "rule": "If evidence is insufficient but no human intervention is required, use review_decision=wait with human_review_required=false.",
    },
}

PROMPT_TEMPLATE_POLICY_VERSION = "review_prompt_policy_v3_strategy_evidence"


def build_prompt_template_hash(
    *,
    policy_version: str | None = None,
    skeleton: Mapping[str, Any] | None = None,
    allowed_enum_values: Mapping[str, Any] | None = None,
    semantic_rules: Mapping[str, Any] | None = None,
    output_rules: tuple[str, ...] | None = None,
) -> str:
    """Hash the prompt template rules that affect model-review output shape."""

    canonical = {
        "policy_version": policy_version or PROMPT_TEMPLATE_POLICY_VERSION,
        "required_output_json_skeleton": skeleton or REVIEW_OUTPUT_JSON_SKELETON,
        "allowed_enum_values": allowed_enum_values or REVIEW_OUTPUT_ALLOWED_ENUM_VALUES,
        "review_decision_semantic_rules": semantic_rules or REVIEW_DECISION_SEMANTIC_RULES,
        "output_rules": list(output_rules or REVIEW_OUTPUT_RULES),
    }
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


REVIEW_OUTPUT_RULES = (
    "JSON object only; no markdown/code fence/prose; include all skeleton keys.",
    "Enum fields must use allowed_enum_values exactly.",
    "No trading/action fields: entry_price, stop_loss, take_profit, position_size, leverage, order_type, final_advice, buy_now, sell_now.",
    "not_trading_advice=true; human_review_required=boolean; final/signal/executable/auto flags=false.",
    "Act as an independent risk review officer: rebut 23F first, then decide whether any evidence can be accepted.",
    "Every major judgment must cite evidence_refs from the provided material pack.",
    "Do not use material-external news, macro, on-chain, account, position, or old BTC market-memory information.",
    "Do not treat 23F candidate_bias as fact and do not output formal entry / stop_loss / take_profit.",
    "If evidence is insufficient, use review_decision=need_more_evidence or wait.",
    "review_decision=require_more_evidence requires human_review_required=true.",
    "If evidence is insufficient but no human intervention is required, use review_decision=wait and human_review_required=false.",
)

PROMPT_TEMPLATE_HASH = build_prompt_template_hash()

REVIEW_INSTRUCTIONS = "\n".join(
    (
        "You are an independent risk review officer, not a strategy and not a final trader.",
        "Your task is to review whether the 23F strategy evidence chain is reliable.",
        "You must rebut 23F first, then decide whether to accept, downgrade, reject, or request more evidence.",
        "Review material only, not trades.",
        *REVIEW_OUTPUT_RULES,
        "The output must conform to review_schema_v2_strategy_evidence.",
    )
)

REVIEW_PROVIDER_SYSTEM_MESSAGE = "\n".join(
    (
        "You are a strict JSON-only independent risk review officer.",
        "Return exactly one JSON object that conforms to the user's required_output_json_skeleton.",
        *REVIEW_OUTPUT_RULES,
    )
)


def build_model_review_prompt(material_pack: Any, *, settings: AppSettings) -> PromptBuildResult:
    """Build a bounded review prompt from one successful material pack.

    Parameters: `material_pack` is an ORM row or test object with stage-18
    material fields; `settings` supplies item and length limits.
    Return value: compact prompt input and counters.
    Failure scenarios: invalid JSON fields are tolerated as empty summaries so
    the schema/provider can require more evidence.
    External effects: none.
    """

    material_json = _json_field(material_pack, "material_json")
    summary_json = _json_field(material_pack, "summary_json")
    question_json = _json_field(material_pack, "question_json")
    validation_plan_json = _json_field(material_pack, "validation_plan_json")
    strategy_evidence = extract_strategy_evidence(material_pack)
    time_anchors = build_time_anchor_summary(material_pack)

    strategy_summaries, total_strategy_count = _strategy_summaries(
        (material_json, summary_json),
        max_strategy_items=settings.model_review_max_strategy_items,
        max_reason_items=settings.model_review_max_reason_items_per_strategy,
    )
    truncated_strategy_count = max(total_strategy_count - len(strategy_summaries), 0)

    input_summary: dict[str, Any] = {
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
        "truncated_strategy_count": truncated_strategy_count,
        "strategy_summaries": strategy_summaries,
        "time_anchors": dict(time_anchors),
        "strategy_evidence": _compact_strategy_evidence(strategy_evidence),
        "strategy_evidence_source": _compact_scalar(strategy_evidence.get("source")),
        "strategy_evidence_aggregation_id": _compact_scalar(strategy_evidence.get("aggregation_id")),
        "candidate_bias": _compact_scalar(strategy_evidence.get("candidate_bias")),
        "decision_readiness": _compact_scalar(strategy_evidence.get("decision_readiness")),
        "strategy_evidence_summary": _compact_mapping(
            _as_mapping(strategy_evidence.get("strategy_evidence_summary")),
            max_items=16,
        ),
        "decision_source_chain": _compact_list(strategy_evidence.get("decision_source_chain"), max_items=10),
        "role_coverage_matrix": _compact_mapping(
            _as_mapping(strategy_evidence.get("role_coverage_matrix")),
            max_items=12,
        ),
        "evidence_missing": _compact_list(strategy_evidence.get("evidence_missing"), max_items=12),
        "strategy_conflict_summary": _compact_list(
            strategy_evidence.get("strategy_conflict_summary"),
            max_items=10,
        ),
        "risk_gate_summary": _compact_mapping(_as_mapping(strategy_evidence.get("risk_gate_summary")), max_items=12),
        "model_review_focus": _compact_list(strategy_evidence.get("model_review_focus"), max_items=12)
        or _compact_mapping(_as_mapping(strategy_evidence.get("model_review_focus")), max_items=12),
        "kline_window_summary": _compact_mapping(_as_mapping(material_json.get("kline_window_summary")), max_items=8),
        "market_material_summary": _compact_mapping(
            {
                "swing": material_json.get("swing"),
                "volatility": material_json.get("volatility"),
                "support_resistance": material_json.get("support_resistance"),
            },
            max_items=8,
        ),
        "material_summary": _compact_mapping(_high_level_summary(summary_json)),
        "review_questions": _compact_list(question_json.get("questions", []) if isinstance(question_json, dict) else []),
        "validation_focus": _compact_mapping(validation_plan_json),
        "not_trading_advice": True,
    }
    prompt_input = {
        "instructions": REVIEW_INSTRUCTIONS,
        "allowed_enum_values": REVIEW_OUTPUT_ALLOWED_ENUM_VALUES,
        "review_decision_semantic_rules": REVIEW_DECISION_SEMANTIC_RULES,
        "required_output_json_skeleton": REVIEW_OUTPUT_JSON_SKELETON,
        "input_summary": _prompt_input_summary(input_summary),
    }
    prompt_text = json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    input_hash = hashlib.sha256(
        json.dumps(input_summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return PromptBuildResult(
        prompt_text=prompt_text,
        input_summary=input_summary,
        input_material_hash=input_hash,
        input_char_count=len(prompt_text),
        input_byte_count=len(prompt_text.encode("utf-8")),
        strategy_item_count=len(strategy_summaries),
        truncated_strategy_count=truncated_strategy_count,
    )


def _json_field(row: Any, field_name: str) -> Mapping[str, Any]:
    value = getattr(row, field_name, {})
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def _strategy_summaries(
    sources: tuple[Mapping[str, Any], ...],
    *,
    max_strategy_items: int,
    max_reason_items: int,
) -> tuple[list[dict[str, Any]], int]:
    collected: list[Mapping[str, Any]] = []
    for source in sources:
        _collect_strategy_like_items(source, collected, depth=0)

    unique_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in collected:
        compact = _compact_strategy_item(item, max_reason_items=max_reason_items)
        identity = (
            str(compact.get("strategy_name", "")),
            str(compact.get("strategy_version", "")),
            str(compact.get("strategy_role", "")),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique_items.append(compact)
    return unique_items[:max_strategy_items], len(unique_items)


def _collect_strategy_like_items(value: Any, collected: list[Mapping[str, Any]], *, depth: int) -> None:
    if depth > 8:
        return
    if isinstance(value, Mapping):
        if _looks_like_strategy_item(value):
            collected.append(value)
            return
        for key, child in value.items():
            if str(key) == "strategy_evidence":
                continue
            _collect_strategy_like_items(child, collected, depth=depth + 1)
        return
    if isinstance(value, list):
        for child in value:
            _collect_strategy_like_items(child, collected, depth=depth + 1)


def _looks_like_strategy_item(value: Mapping[str, Any]) -> bool:
    keys = set(value.keys())
    return "strategy_name" in keys or {"strategy_version", "strategy_role"} <= keys or "analysis_hypothesis_direction" in keys


def _compact_strategy_item(value: Mapping[str, Any], *, max_reason_items: int) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in STRATEGY_SUMMARY_KEYS:
        raw_value = value.get(key)
        if key in {"reason_codes", "missing_evidence"}:
            compact_value = _compact_list(raw_value, max_items=max_reason_items)
        else:
            compact_value = _compact_scalar(raw_value)
        if key == "enabled" and compact_value is True:
            continue
        if key == "status" and compact_value == "success":
            continue
        if compact_value not in (None, "", []):
            compact[key] = compact_value
    return compact


def _prompt_input_summary(input_summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return a prompt-only summary that keeps persisted input_summary unchanged."""

    compact = dict(input_summary)
    strategy_summaries = input_summary.get("strategy_summaries", [])
    if isinstance(strategy_summaries, list):
        prompt_strategy_summaries = strategy_summaries[:12]
        compact["strategy_summaries"] = [
            _prompt_strategy_item(item) if isinstance(item, Mapping) else item
            for item in prompt_strategy_summaries
        ]
        compact["prompt_strategy_summaries_truncated"] = max(len(strategy_summaries) - len(prompt_strategy_summaries), 0)
    return compact


def _prompt_strategy_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        PROMPT_STRATEGY_KEY_ALIASES.get(str(key), str(key)): value
        for key, value in item.items()
    }


def _compact_strategy_evidence(strategy_evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return bounded public 23F evidence without private strategy payloads."""

    keys = (
        "source",
        "aggregation_id",
        "strategy_signal_run_id",
        "status",
        "candidate_bias",
        "candidate_confidence",
        "decision_readiness",
        "strategy_evidence_summary",
        "decision_source_chain",
        "role_coverage_matrix",
        "evidence_missing",
        "strategy_conflict_summary",
        "participation_summary",
        "observe_only_summary",
        "risk_gate_summary",
        "model_review_focus",
    )
    compact: dict[str, Any] = {}
    for key in keys:
        value = strategy_evidence.get(key)
        if isinstance(value, Mapping):
            compact[key] = _compact_mapping(value, max_items=12)
        elif isinstance(value, list):
            compact[key] = _compact_list(value, max_items=10)
        else:
            compact[key] = _compact_scalar(value)
    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def _high_level_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    skipped = {"strategy_summaries", "strategies", "strategy_results"}
    return {str(key): raw_value for key, raw_value in value.items() if str(key) not in skipped}


def _compact_mapping(value: Mapping[str, Any], *, max_items: int = 12) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for index, (key, raw_value) in enumerate(value.items()):
        if index >= max_items:
            compact["truncated"] = True
            break
        if isinstance(raw_value, Mapping):
            compact[str(key)] = _compact_mapping(raw_value, max_items=6)
        elif isinstance(raw_value, list):
            compact[str(key)] = _compact_list(raw_value, max_items=6)
        else:
            compact[str(key)] = _compact_scalar(raw_value)
    return compact


def _compact_list(value: Any, *, max_items: int = 5) -> list[Any]:
    if not isinstance(value, list):
        return []
    result: list[Any] = []
    for item in value[:max_items]:
        if isinstance(item, Mapping):
            result.append(_compact_mapping(item, max_items=6))
        elif isinstance(item, list):
            result.append(_compact_list(item, max_items=max_items))
        else:
            result.append(_compact_scalar(item))
    return result


def _compact_scalar(value: Any, *, max_chars: int = 300) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "REVIEW_OUTPUT_JSON_SKELETON",
    "REVIEW_OUTPUT_ALLOWED_ENUM_VALUES",
    "REVIEW_DECISION_SEMANTIC_RULES",
    "REVIEW_OUTPUT_RULES",
    "PROMPT_TEMPLATE_HASH",
    "PROMPT_TEMPLATE_POLICY_VERSION",
    "REVIEW_PROVIDER_SYSTEM_MESSAGE",
    "build_prompt_template_hash",
    "build_model_review_prompt",
]
