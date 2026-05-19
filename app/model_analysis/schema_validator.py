"""Schema validator for stage-19 model analysis provider output.

This file belongs to `app/model_analysis`. It validates structured provider
output and rejects any field that would turn a review into executable trading
content.

Called by `app/model_analysis/service.py` and tests.
External services: none. MySQL: none. Redis: none. Hermes: none. Real model
calls: none. Trading execution: none.
"""

from __future__ import annotations

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

    checks = (
        ("review_decision", set(item.value for item in ReviewDecision)),
        ("evidence_quality", EVIDENCE_QUALITY_VALUES),
        ("logic_consistency", LOGIC_CONSISTENCY_VALUES),
        ("risk_acceptability", RISK_ACCEPTABILITY_VALUES),
        ("strategy_conflict_level", CONFLICT_LEVEL_VALUES),
    )
    for field_name, allowed_values in checks:
        value = str(output.get(field_name, ""))
        if value not in allowed_values:
            return SchemaValidationResult(
                is_valid=False,
                error_code="schema_invalid_enum_value",
                error_message=f"{field_name} is invalid: {value}",
            )

    normalized: dict[str, Any] = {
        "review_decision": str(output["review_decision"]),
        "evidence_quality": str(output["evidence_quality"]),
        "logic_consistency": str(output["logic_consistency"]),
        "risk_acceptability": str(output["risk_acceptability"]),
        "strategy_conflict_level": str(output["strategy_conflict_level"]),
        "human_review_required": bool(output["human_review_required"]),
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
    return SchemaValidationResult(is_valid=True, normalized_output=normalized)


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


__all__ = ["FORBIDDEN_TRADING_FIELDS", "REQUIRED_FIELDS", "validate_model_review_output"]
