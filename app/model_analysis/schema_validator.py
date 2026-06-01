"""Schema validator for stage-24C strategy-evidence model review output.

This file belongs to `app/model_analysis`. It validates structured provider
output and maps the 24C review JSON into the existing stage-19/20 persistence
shape without turning the model into a final trading-advice generator.

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
        "agreement_with_23f",
        "review_decision",
        "main_objection",
        "strongest_counterargument",
        "missing_evidence",
        "disputed_strategy_points",
        "overestimated_evidence",
        "underestimated_evidence",
        "scenario_review",
        "discipline_check",
        "recommendation_to_advice_layer",
        "evidence_refs",
        "time_freshness_assessment",
        "boundary_flags",
        "quality_flags",
        "confidence",
        "summary",
        "human_review_required",
        "not_trading_advice",
        "is_final_trading_advice",
        "is_trading_signal",
        "is_executable",
        "auto_trading_allowed",
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
AGREEMENT_WITH_23F_VALUES = frozenset({"agree", "partial", "disagree", "insufficient_evidence"})
MODEL_REVIEW_DECISION_VALUES = frozenset(
    {
        "accept",
        "downgrade",
        "risk_reject",
        "need_more_evidence",
        ReviewDecision.ACCEPT_FOR_FURTHER_REVIEW.value,
        ReviewDecision.REJECT_CANDIDATE.value,
        ReviewDecision.REQUIRE_MORE_EVIDENCE.value,
        ReviewDecision.WAIT.value,
        ReviewDecision.HUMAN_REVIEW_REQUIRED.value,
        ReviewDecision.BLOCKED.value,
    }
)
RECOMMENDATION_TO_ADVICE_LAYER_VALUES = frozenset(
    {
        ReviewDecision.ACCEPT_FOR_FURTHER_REVIEW.value,
        ReviewDecision.WAIT.value,
        "need_more_evidence",
        "risk_reject",
        "downgrade",
        ReviewDecision.HUMAN_REVIEW_REQUIRED.value,
    }
)
DISCIPLINE_CHECK_VALUES = frozenset({"ok", "caution", "poor", "unclear", "unknown"})
CONFIDENCE_VALUES = frozenset({"high", "medium", "low", "unknown"})

ENUM_ALLOWED_VALUES: dict[str, frozenset[str]] = {
    "agreement_with_23f": AGREEMENT_WITH_23F_VALUES,
    "review_decision": MODEL_REVIEW_DECISION_VALUES,
    "recommendation_to_advice_layer": RECOMMENDATION_TO_ADVICE_LAYER_VALUES,
    "confidence": CONFIDENCE_VALUES,
    "discipline_check_value": DISCIPLINE_CHECK_VALUES,
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
    },
    "review_decision": {
        "stale_data": "need_more_evidence",
    },
}

REVIEW_DECISION_HUMAN_REVIEW_NORMALIZATION_REASON = "require_more_evidence_requires_human_review"
SCHEMA_NORMALIZATION_POLICY_VERSION = "schema_normalization_policy_v3_strategy_evidence"


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
            "need_more_evidence": {
                "legacy_review_decision": ReviewDecision.REQUIRE_MORE_EVIDENCE.value,
                "human_review_required": True,
            },
            "wait": {"human_review_required_false_allowed": True},
            "boundary_violation": {"persistable_but_not_high_confidence": True},
            "low_quality": {"persistable_but_requires_human_review": True},
        },
    }
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


SCHEMA_NORMALIZATION_POLICY_HASH = build_schema_normalization_policy_hash()


def validate_model_review_output(output: Mapping[str, Any]) -> SchemaValidationResult:
    """Validate and normalize provider output.

    Parameters: structured provider output from a mock or configured real
    model. Return value: validation result with normalized fields when valid.
    Failure scenarios: missing required fields, invalid enums, non-JSON shape,
    or unsafe safety flags are invalid. Trading-action fields are preserved as
    boundary flags so the result can be audited without treating it as usable
    advice.
    External effects: none.
    """

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

    scenario_review = output.get("scenario_review")
    if not isinstance(scenario_review, Mapping):
        return SchemaValidationResult(
            is_valid=False,
            error_code="schema_invalid_nested_object",
            error_message="scenario_review must be an object",
        )
    discipline_check = output.get("discipline_check")
    if not isinstance(discipline_check, Mapping):
        return SchemaValidationResult(
            is_valid=False,
            error_code="schema_invalid_nested_object",
            error_message="discipline_check must be an object",
        )

    nested_missing = _missing_nested_fields(scenario_review=scenario_review, discipline_check=discipline_check)
    if nested_missing:
        return SchemaValidationResult(
            is_valid=False,
            error_code="schema_missing_required_field",
            error_message=f"missing fields: {', '.join(nested_missing)}",
            missing_fields=tuple(nested_missing),
        )

    normalized_enum_output, enum_normalizations = _normalize_controlled_enum_aliases(output)
    enum_error = _validate_enum_values(normalized_enum_output, discipline_check=discipline_check)
    if enum_error is not None:
        return enum_error

    quality_flags = _list_field(output.get("quality_flags"))
    boundary_flags = _list_field(output.get("boundary_flags"))
    forbidden_path = _find_forbidden_path(output)
    if forbidden_path:
        boundary_flags.append(
            {
                "code": "boundary_violation",
                "reason": "forbidden_trading_field_present",
                "path": forbidden_path,
            }
        )
    if not _text_field(output.get("strongest_counterargument")).strip():
        quality_flags.append("missing_strongest_counterargument")
    if not _list_field(output.get("evidence_refs")):
        quality_flags.append("missing_evidence_refs")
    if not _text_field(output.get("time_freshness_assessment")).strip():
        quality_flags.append("missing_time_freshness_assessment")
    external_refs = _detect_external_information_reference(output)
    if external_refs:
        boundary_flags.append(
            {
                "code": "boundary_violation",
                "reason": "external_information_reference",
                "matches": external_refs,
            }
        )
    if _looks_like_23f_restatement(output):
        quality_flags.append("possible_23f_restatement")

    normalized_semantic_output, semantic_normalizations = _normalize_human_review_semantics(normalized_enum_output)
    legacy_review_decision = _legacy_review_decision(str(normalized_semantic_output["review_decision"]))
    evidence_quality = _legacy_evidence_quality(
        normalized_semantic_output,
        quality_flags=quality_flags,
        boundary_flags=boundary_flags,
    )
    risk_acceptability = _legacy_risk_acceptability(
        normalized_semantic_output,
        quality_flags=quality_flags,
        boundary_flags=boundary_flags,
    )
    logic_consistency = _legacy_logic_consistency(normalized_semantic_output, boundary_flags=boundary_flags)
    strategy_conflict_level = _legacy_strategy_conflict_level(normalized_semantic_output)
    human_review_required = _human_review_required(
        normalized_semantic_output,
        legacy_review_decision=legacy_review_decision,
        quality_flags=quality_flags,
        boundary_flags=boundary_flags,
    )

    normalized: dict[str, Any] = {
        "review_decision": legacy_review_decision,
        "model_review_decision_24c": str(normalized_semantic_output["review_decision"]),
        "agreement_with_23f": str(normalized_semantic_output["agreement_with_23f"]),
        "recommendation_to_advice_layer": str(normalized_semantic_output["recommendation_to_advice_layer"]),
        "evidence_quality": evidence_quality,
        "logic_consistency": logic_consistency,
        "risk_acceptability": risk_acceptability,
        "strategy_conflict_level": strategy_conflict_level,
        "human_review_required": human_review_required,
        "missing_evidence": _list_field(output.get("missing_evidence")),
        "rejection_reasons": _rejection_reasons(output),
        "risk_warnings": _risk_warnings(output, boundary_flags=boundary_flags),
        "conditions_to_reconsider": _conditions_to_reconsider(output),
        "validation_focus": _validation_focus(output, quality_flags=quality_flags, boundary_flags=boundary_flags),
        "human_review_questions": _list_field(output.get("human_review_questions")),
        "summary_text": _text_field(output.get("summary") or output.get("summary_text")),
        "not_trading_advice": True,
        "not_trading_advice_text": _text_field(
            output.get("not_trading_advice_text")
            or "这是大模型审查结果，不是最终交易建议，也不是可执行交易信号。"
        ),
        "main_objection": _text_field(output.get("main_objection")),
        "strongest_counterargument": _text_field(output.get("strongest_counterargument")),
        "disputed_strategy_points": _list_field(output.get("disputed_strategy_points")),
        "overestimated_evidence": _list_field(output.get("overestimated_evidence")),
        "underestimated_evidence": _list_field(output.get("underestimated_evidence")),
        "scenario_review": dict(scenario_review),
        "discipline_check": dict(discipline_check),
        "evidence_refs": _list_field(output.get("evidence_refs")),
        "time_freshness_assessment": _text_field(output.get("time_freshness_assessment")),
        "weak_model_assessment": _text_field(output.get("weak_model_assessment")),
        "weak_model_supports_strategy": _text_field(output.get("weak_model_supports_strategy") or "unknown"),
        "weak_model_conflicts_with_strategy": _text_field(output.get("weak_model_conflicts_with_strategy") or "unknown"),
        "weak_model_quality_concerns": _list_field(output.get("weak_model_quality_concerns")),
        "duplicate_evidence_risk": _text_field(output.get("duplicate_evidence_risk") or "unknown"),
        "model_reviewer_note": _text_field(output.get("model_reviewer_note")),
        "boundary_flags": boundary_flags,
        "quality_flags": quality_flags,
        "confidence": str(normalized_semantic_output["confidence"]),
    }
    normalized["review_payload_24c"] = {
        "agreement_with_23f": normalized["agreement_with_23f"],
        "review_decision": normalized["model_review_decision_24c"],
        "main_objection": normalized["main_objection"],
        "strongest_counterargument": normalized["strongest_counterargument"],
        "missing_evidence": normalized["missing_evidence"],
        "disputed_strategy_points": normalized["disputed_strategy_points"],
        "overestimated_evidence": normalized["overestimated_evidence"],
        "underestimated_evidence": normalized["underestimated_evidence"],
        "scenario_review": normalized["scenario_review"],
        "discipline_check": normalized["discipline_check"],
        "recommendation_to_advice_layer": normalized["recommendation_to_advice_layer"],
        "evidence_refs": normalized["evidence_refs"],
        "time_freshness_assessment": normalized["time_freshness_assessment"],
        "weak_model_assessment": normalized["weak_model_assessment"],
        "weak_model_supports_strategy": normalized["weak_model_supports_strategy"],
        "weak_model_conflicts_with_strategy": normalized["weak_model_conflicts_with_strategy"],
        "weak_model_quality_concerns": normalized["weak_model_quality_concerns"],
        "duplicate_evidence_risk": normalized["duplicate_evidence_risk"],
        "model_reviewer_note": normalized["model_reviewer_note"],
        "boundary_flags": normalized["boundary_flags"],
        "quality_flags": normalized["quality_flags"],
        "confidence": normalized["confidence"],
        "summary": normalized["summary_text"],
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
        normalized.get("review_decision") in {ReviewDecision.REQUIRE_MORE_EVIDENCE.value, "need_more_evidence"}
        and normalized.get("human_review_required") is False
    ):
        normalized["human_review_required"] = True
        warnings.append(
            {
                "field": "human_review_required",
                "original_value": "false",
                "normalized_value": "true",
                "review_decision": str(normalized.get("review_decision")),
                "reason": REVIEW_DECISION_HUMAN_REVIEW_NORMALIZATION_REASON,
            }
        )
    return normalized, warnings


def _missing_nested_fields(*, scenario_review: Mapping[str, Any], discipline_check: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    for field_name in ("main_scenario", "opposite_scenario", "risk_scenario", "no_trade_scenario"):
        if not _text_field(scenario_review.get(field_name)).strip():
            missing.append(f"scenario_review.{field_name}")
    for field_name in ("chasing_risk", "risk_reward_quality", "stop_condition_clarity", "overtrading_risk"):
        if field_name not in discipline_check:
            missing.append(f"discipline_check.{field_name}")
    return missing


def _validate_enum_values(
    output: Mapping[str, Any],
    *,
    discipline_check: Mapping[str, Any],
) -> SchemaValidationResult | None:
    checks = (
        ("agreement_with_23f", AGREEMENT_WITH_23F_VALUES),
        ("review_decision", MODEL_REVIEW_DECISION_VALUES),
        ("recommendation_to_advice_layer", RECOMMENDATION_TO_ADVICE_LAYER_VALUES),
        ("confidence", CONFIDENCE_VALUES),
    )
    for field_name, allowed_values in checks:
        value = str(output.get(field_name, ""))
        if value not in allowed_values:
            return SchemaValidationResult(
                is_valid=False,
                error_code="schema_invalid_enum_value",
                error_message=f"{field_name} is invalid: {value}",
            )
    compatibility_checks = (
        ("evidence_quality", EVIDENCE_QUALITY_VALUES),
        ("logic_consistency", LOGIC_CONSISTENCY_VALUES),
        ("risk_acceptability", RISK_ACCEPTABILITY_VALUES),
        ("strategy_conflict_level", CONFLICT_LEVEL_VALUES),
    )
    for field_name, allowed_values in compatibility_checks:
        if field_name not in output:
            continue
        value = str(output.get(field_name, ""))
        if value not in allowed_values:
            return SchemaValidationResult(
                is_valid=False,
                error_code="schema_invalid_enum_value",
                error_message=f"{field_name} is invalid: {value}",
            )
    for field_name in ("chasing_risk", "risk_reward_quality", "stop_condition_clarity", "overtrading_risk"):
        value = str(discipline_check.get(field_name, ""))
        if value not in DISCIPLINE_CHECK_VALUES:
            return SchemaValidationResult(
                is_valid=False,
                error_code="schema_invalid_enum_value",
                error_message=f"discipline_check.{field_name} is invalid: {value}",
            )
    return None


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


def _legacy_review_decision(review_decision: str) -> str:
    if review_decision == "accept":
        return ReviewDecision.ACCEPT_FOR_FURTHER_REVIEW.value
    if review_decision == "downgrade":
        return ReviewDecision.HUMAN_REVIEW_REQUIRED.value
    if review_decision == "risk_reject":
        return ReviewDecision.REJECT_CANDIDATE.value
    if review_decision == "need_more_evidence":
        return ReviewDecision.REQUIRE_MORE_EVIDENCE.value
    return review_decision


def _legacy_evidence_quality(
    output: Mapping[str, Any],
    *,
    quality_flags: list[Any],
    boundary_flags: list[Any],
) -> str:
    raw = str(output.get("evidence_quality", "") or "").strip()
    if raw in EVIDENCE_QUALITY_VALUES:
        return "weak" if quality_flags or boundary_flags else raw
    agreement = str(output.get("agreement_with_23f", ""))
    confidence = str(output.get("confidence", ""))
    decision = str(output.get("review_decision", ""))
    if quality_flags or boundary_flags:
        return "weak"
    if decision in {"need_more_evidence", ReviewDecision.REQUIRE_MORE_EVIDENCE.value}:
        return "insufficient"
    if agreement == "agree" and confidence == "high":
        return "strong"
    if agreement in {"agree", "partial"} and confidence in {"medium", "high"}:
        return "moderate"
    if agreement == "insufficient_evidence":
        return "insufficient"
    return "weak"


def _legacy_risk_acceptability(
    output: Mapping[str, Any],
    *,
    quality_flags: list[Any],
    boundary_flags: list[Any],
) -> str:
    raw = str(output.get("risk_acceptability", "") or "").strip()
    if raw in RISK_ACCEPTABILITY_VALUES:
        return "unacceptable" if boundary_flags else raw
    decision = str(output.get("review_decision", ""))
    recommendation = str(output.get("recommendation_to_advice_layer", ""))
    if boundary_flags:
        return "unacceptable"
    if decision in {"risk_reject", ReviewDecision.REJECT_CANDIDATE.value} or recommendation == "risk_reject":
        return "unacceptable"
    if quality_flags or decision in {"downgrade", "need_more_evidence", ReviewDecision.REQUIRE_MORE_EVIDENCE.value}:
        return "caution"
    return "caution"


def _legacy_logic_consistency(output: Mapping[str, Any], *, boundary_flags: list[Any]) -> str:
    raw = str(output.get("logic_consistency", "") or "").strip()
    if raw in LOGIC_CONSISTENCY_VALUES:
        return "conflicting" if boundary_flags else raw
    disputed = _list_field(output.get("disputed_strategy_points"))
    if boundary_flags:
        return "conflicting"
    if len(disputed) >= 3:
        return "conflicting"
    if disputed:
        return "minor_conflict"
    return "consistent"


def _legacy_strategy_conflict_level(output: Mapping[str, Any]) -> str:
    raw = str(output.get("strategy_conflict_level", "") or "").strip()
    if raw in CONFLICT_LEVEL_VALUES:
        return raw
    disputed = _list_field(output.get("disputed_strategy_points"))
    if len(disputed) >= 3:
        return "high"
    if disputed:
        return "medium"
    agreement = str(output.get("agreement_with_23f", ""))
    if agreement == "disagree":
        return "high"
    if agreement == "partial":
        return "medium"
    if agreement == "agree":
        return "low"
    return "unknown"


def _human_review_required(
    output: Mapping[str, Any],
    *,
    legacy_review_decision: str,
    quality_flags: list[Any],
    boundary_flags: list[Any],
) -> bool:
    if quality_flags or boundary_flags:
        return True
    if legacy_review_decision in {
        ReviewDecision.REQUIRE_MORE_EVIDENCE.value,
        ReviewDecision.HUMAN_REVIEW_REQUIRED.value,
        ReviewDecision.REJECT_CANDIDATE.value,
    }:
        return True
    return bool(output.get("human_review_required"))


def _rejection_reasons(output: Mapping[str, Any]) -> list[Any]:
    reasons = _list_field(output.get("rejection_reasons"))
    for field_name in ("main_objection", "strongest_counterargument"):
        value = _text_field(output.get(field_name)).strip()
        if value:
            reasons.append({field_name: value})
    disputed = _list_field(output.get("disputed_strategy_points"))
    if disputed:
        reasons.append({"disputed_strategy_points": disputed})
    return reasons


def _risk_warnings(output: Mapping[str, Any], *, boundary_flags: list[Any]) -> list[Any]:
    warnings = _list_field(output.get("risk_warnings"))
    discipline = output.get("discipline_check")
    if isinstance(discipline, Mapping):
        warnings.append({"discipline_check": dict(discipline)})
    if boundary_flags:
        warnings.append({"boundary_flags": boundary_flags})
    return warnings


def _conditions_to_reconsider(output: Mapping[str, Any]) -> list[Any]:
    conditions = _list_field(output.get("conditions_to_reconsider"))
    scenario_review = output.get("scenario_review")
    if isinstance(scenario_review, Mapping):
        conditions.append({"scenario_review": dict(scenario_review)})
    recommendation = _text_field(output.get("recommendation_to_advice_layer")).strip()
    if recommendation:
        conditions.append({"recommendation_to_advice_layer": recommendation})
    return conditions


def _validation_focus(
    output: Mapping[str, Any],
    *,
    quality_flags: list[Any],
    boundary_flags: list[Any],
) -> list[Any]:
    focus = _list_field(output.get("validation_focus"))
    refs = _list_field(output.get("evidence_refs"))
    if refs:
        focus.append({"evidence_refs": refs})
    if quality_flags:
        focus.append({"quality_flags": quality_flags})
    if boundary_flags:
        focus.append({"boundary_flags": boundary_flags})
    freshness = _text_field(output.get("time_freshness_assessment")).strip()
    if freshness:
        focus.append({"time_freshness_assessment": freshness})
    return focus


def _detect_external_information_reference(output: Mapping[str, Any]) -> list[str]:
    text = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str).lower()
    keywords = (
        "external news",
        "outside material",
        "macro data",
        "on-chain",
        "account balance",
        "user position",
        "unprovided price",
        "外部新闻",
        "链上",
        "账户",
        "用户仓位",
        "未提供价格",
    )
    matches: list[str] = []
    for keyword in keywords:
        if keyword.lower() in text:
            matches.append(keyword)
    return matches


def _looks_like_23f_restatement(output: Mapping[str, Any]) -> bool:
    summary = _text_field(output.get("summary")).strip()
    counterargument = _text_field(output.get("strongest_counterargument")).strip()
    objection = _text_field(output.get("main_objection")).strip()
    if not summary or not counterargument:
        return False
    return summary == counterargument or (counterargument == objection and len(counterargument) < 16)


def _list_field(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Mapping):
        return [dict(value)]
    if value in (None, ""):
        return []
    return [str(value)]


def _text_field(value: Any) -> str:
    return "" if value is None else str(value)


__all__ = [
    "AGREEMENT_WITH_23F_VALUES",
    "CONFIDENCE_VALUES",
    "CONTROLLED_ENUM_ALIASES",
    "DISCIPLINE_CHECK_VALUES",
    "ENUM_ALLOWED_VALUES",
    "FORBIDDEN_TRADING_FIELDS",
    "MODEL_REVIEW_DECISION_VALUES",
    "RECOMMENDATION_TO_ADVICE_LAYER_VALUES",
    "REQUIRED_FIELDS",
    "SCHEMA_NORMALIZATION_POLICY_HASH",
    "SCHEMA_NORMALIZATION_POLICY_VERSION",
    "REVIEW_DECISION_HUMAN_REVIEW_NORMALIZATION_REASON",
    "build_schema_normalization_policy_hash",
    "validate_model_review_output",
]
