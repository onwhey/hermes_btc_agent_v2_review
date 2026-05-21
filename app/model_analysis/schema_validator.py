"""Schema validator for stage-19 model analysis provider output.

This file belongs to `app/model_analysis`. It validates structured provider
output and rejects any field that would turn a review into executable trading
content.

Called by `app/model_analysis/service.py` and tests.
External services: none. MySQL: none. Redis: none. Hermes: none. Real model
calls: none. Trading execution: none.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from app.model_analysis.types import ReviewDecision, SchemaValidationResult

REQUIRED_FIELDS = frozenset(
    {
        "review_decision",
        "evidence_quality",
        "logic_consistency",
        "risk_acceptability",
        "strategy_conflict_level",
        "missing_evidence",
        "risk_warnings",
        "human_review_questions",
        "validation_focus",
        "human_review_required",
        "not_trading_advice",
        "is_final_trading_advice",
        "is_trading_signal",
        "is_executable",
        "auto_trading_allowed",
        "summary_text",
    }
)

FORBIDDEN_TRADING_FIELDS = frozenset(
    {
        "entry_price",
        "stop_loss",
        "take_profit",
        "position_size",
        "leverage",
        "order_type",
        "final_advice",
        "buy_now",
        "sell_now",
    }
)

EVIDENCE_QUALITY_VALUES = frozenset({"strong", "moderate", "weak", "insufficient", "unknown"})
LOGIC_CONSISTENCY_VALUES = frozenset({"consistent", "minor_conflict", "conflicting", "unknown"})
RISK_ACCEPTABILITY_VALUES = frozenset({"acceptable", "caution", "unacceptable", "unknown"})
CONFLICT_LEVEL_VALUES = frozenset({"none", "low", "medium", "high", "unknown"})

ENUM_ALLOWED_VALUES: dict[str, frozenset[str]] = {
    "evidence_quality": EVIDENCE_QUALITY_VALUES,
    "logic_consistency": LOGIC_CONSISTENCY_VALUES,
    "risk_acceptability": RISK_ACCEPTABILITY_VALUES,
    "strategy_conflict_level": CONFLICT_LEVEL_VALUES,
}

CONTROLLED_ENUM_ALIASES: dict[str, dict[str, str]] = {
    "evidence_quality": {
        "low": "weak",
        "medium": "moderate",
        "high": "strong",
    }
}

REVIEW_DECISION_HUMAN_REVIEW_NORMALIZATION_REASON = "require_more_evidence_requires_human_review"
SCHEMA_NORMALIZATION_POLICY_VERSION = "schema_normalization_policy_v2"


def build_schema_normalization_policy_hash(
    *,
    policy_version: str | None = None,
    required_fields: frozenset[str] | None = None,
    forbidden_fields: frozenset[str] | None = None,
    enum_allowed_values: Mapping[str, frozenset[str]] | None = None,
    enum_aliases: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    """Hash schema and normalization rules that affect accepted outputs."""

    canonical = {
        "policy_version": policy_version or SCHEMA_NORMALIZATION_POLICY_VERSION,
        "required_fields": sorted(required_fields or REQUIRED_FIELDS),
        "forbidden_trading_fields": sorted(forbidden_fields or FORBIDDEN_TRADING_FIELDS),
        "enum_allowed_values": {
            field_name: sorted(values) for field_name, values in (enum_allowed_values or ENUM_ALLOWED_VALUES).items()
        },
        "controlled_enum_aliases": {
            field_name: dict(values) for field_name, values in (enum_aliases or CONTROLLED_ENUM_ALIASES).items()
        },
        "human_review_semantics": {
            "require_more_evidence": {
                "human_review_required": True,
                "reason": REVIEW_DECISION_HUMAN_REVIEW_NORMALIZATION_REASON,
            },
            "wait": {"human_review_required_false_allowed": True},
        },
    }
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


SCHEMA_NORMALIZATION_POLICY_HASH = build_schema_normalization_policy_hash()


def validate_model_review_output(output: Mapping[str, Any]) -> SchemaValidationResult:
    """Validate and normalize provider output.

    Parameters: structured provider output.
    Return value: validation result with normalized fields when valid.
    Failure scenarios: missing required fields, invalid enum values,
    `not_trading_advice != True`, or forbidden trading fields.
    External effects: none.
    """

    forbidden_path = _find_forbidden_path(output)
    if forbidden_path:
        return SchemaValidationResult(
            is_valid=False,
            error_code="schema_forbidden_trading_field",
            error_message=f"forbidden trading field present: {forbidden_path}",
        )

    missing = sorted(REQUIRED_FIELDS - set(output.keys()))
    if missing:
        return SchemaValidationResult(
            is_valid=False,
            error_code="schema_missing_required_field",
            error_message=f"missing fields: {', '.join(missing)}",
            missing_fields=tuple(missing),
        )
    if output.get("not_trading_advice") is not True:
        return SchemaValidationResult(
            is_valid=False,
            error_code="schema_not_trading_advice_false",
            error_message="not_trading_advice must be true",
        )
    if not isinstance(output.get("human_review_required"), bool):
        return SchemaValidationResult(
            is_valid=False,
            error_code="schema_human_review_required_not_boolean",
            error_message="human_review_required must be boolean",
        )
    for field_name in (
        "is_final_trading_advice",
        "is_trading_signal",
        "is_executable",
        "auto_trading_allowed",
    ):
        if output.get(field_name) is not False:
            return SchemaValidationResult(
                is_valid=False,
                error_code="schema_safety_flag_not_false",
                error_message=f"{field_name} must be false",
            )

    normalized_enum_output, enum_normalizations = _normalize_controlled_enum_aliases(output)
    checks = (
        ("review_decision", set(item.value for item in ReviewDecision)),
        ("evidence_quality", EVIDENCE_QUALITY_VALUES),
        ("logic_consistency", LOGIC_CONSISTENCY_VALUES),
        ("risk_acceptability", RISK_ACCEPTABILITY_VALUES),
        ("strategy_conflict_level", CONFLICT_LEVEL_VALUES),
    )
    for field_name, allowed_values in checks:
        value = str(normalized_enum_output.get(field_name, ""))
        if value not in allowed_values:
            return SchemaValidationResult(
                is_valid=False,
                error_code="schema_invalid_enum_value",
                error_message=f"{field_name} is invalid: {value}",
            )

    normalized_semantic_output, semantic_normalizations = _normalize_human_review_semantics(normalized_enum_output)

    normalized: dict[str, Any] = {
        "review_decision": str(normalized_semantic_output["review_decision"]),
        "evidence_quality": str(normalized_enum_output["evidence_quality"]),
        "logic_consistency": str(normalized_enum_output["logic_consistency"]),
        "risk_acceptability": str(normalized_enum_output["risk_acceptability"]),
        "strategy_conflict_level": str(normalized_enum_output["strategy_conflict_level"]),
        "human_review_required": bool(normalized_semantic_output["human_review_required"]),
        "missing_evidence": _list_field(output.get("missing_evidence")),
        "rejection_reasons": _list_field(output.get("rejection_reasons")),
        "risk_warnings": _list_field(output.get("risk_warnings")),
        "conditions_to_reconsider": _list_field(output.get("conditions_to_reconsider")),
        "validation_focus": _list_field(output.get("validation_focus")),
        "human_review_questions": _list_field(output.get("human_review_questions")),
        "summary_text": _text_field(output.get("summary_text")),
        "not_trading_advice": True,
        "not_trading_advice_text": _text_field(
            output.get("not_trading_advice_text")
            or "这是大模型审查结果，不是最终交易建议，也不是可执行交易信号。"
        ),
    }
    return SchemaValidationResult(
        is_valid=True,
        normalized_output=normalized,
        enum_normalizations=tuple([*enum_normalizations, *semantic_normalizations]),
    )


def _normalize_controlled_enum_aliases(output: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Apply narrow enum aliases before validation without guessing meaning."""

    normalized = dict(output)
    warnings: list[dict[str, str]] = []
    for field_name, alias_map in CONTROLLED_ENUM_ALIASES.items():
        raw_value = output.get(field_name)
        if not isinstance(raw_value, str):
            continue
        normalized_value = alias_map.get(raw_value.strip().lower())
        if normalized_value is None:
            continue
        normalized[field_name] = normalized_value
        warnings.append(
            {
                "field": field_name,
                "original_value": raw_value,
                "normalized_value": normalized_value,
                "reason": "controlled_schema_enum_alias",
            }
        )
    return normalized, warnings


def _normalize_human_review_semantics(output: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Normalize safe review-decision semantics without changing trading fields."""

    normalized = dict(output)
    warnings: list[dict[str, str]] = []
    if (
        normalized.get("review_decision") == ReviewDecision.REQUIRE_MORE_EVIDENCE.value
        and normalized.get("human_review_required") is False
    ):
        normalized["human_review_required"] = True
        warnings.append(
            {
                "field": "human_review_required",
                "original_value": "false",
                "normalized_value": "true",
                "review_decision": ReviewDecision.REQUIRE_MORE_EVIDENCE.value,
                "reason": REVIEW_DECISION_HUMAN_REVIEW_NORMALIZATION_REASON,
            }
        )
    return normalized, warnings


def _find_forbidden_path(value: Any, *, path: str = "") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text in FORBIDDEN_TRADING_FIELDS:
                return child_path
            nested_path = _find_forbidden_path(child, path=child_path)
            if nested_path:
                return nested_path
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            nested_path = _find_forbidden_path(child, path=child_path)
            if nested_path:
                return nested_path
    return None


def _list_field(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [str(value)]


def _text_field(value: Any) -> str:
    return "" if value is None else str(value)


__all__ = [
    "CONTROLLED_ENUM_ALIASES",
    "ENUM_ALLOWED_VALUES",
    "FORBIDDEN_TRADING_FIELDS",
    "REQUIRED_FIELDS",
    "SCHEMA_NORMALIZATION_POLICY_HASH",
    "SCHEMA_NORMALIZATION_POLICY_VERSION",
    "REVIEW_DECISION_HUMAN_REVIEW_NORMALIZATION_REASON",
    "build_schema_normalization_policy_hash",
    "validate_model_review_output",
]
