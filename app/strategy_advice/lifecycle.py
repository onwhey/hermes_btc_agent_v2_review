"""Lifecycle decision helpers for stage-21A strategy advice.

This file belongs to `app/strategy_advice`. It converts one compact stage-20A
aggregation row into a conservative advice candidate and compares it with the
current active advice. It does not read/write databases, call model providers,
send Hermes, touch Redis, modify Kline data, or execute trading.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from app.strategy_advice.schema import (
    AdviceAction,
    AdviceStatus,
    DirectionalBias,
    LifecycleAction,
    TradePermission,
    json_text,
    load_json_text,
)


@dataclass(frozen=True)
class AdviceCandidate:
    """Compact candidate generated from one stage-20A aggregation row."""

    advice_action: AdviceAction
    directional_bias: DirectionalBias
    trade_permission: TradePermission
    summary_text: str
    risk_summary_json: Mapping[str, Any]
    strategy_summary_json: Mapping[str, Any]
    model_summary_json: Mapping[str, Any]
    model_review_status_summary_json: Mapping[str, Any]
    risk_blocked: bool
    terminal_lifecycle_action: LifecycleAction | None
    terminal_advice_status: AdviceStatus | None
    lifecycle_reason: str
    semantic_signature: str


def build_advice_candidate_from_aggregation(aggregation_row: Any) -> AdviceCandidate:
    """Build a conservative advice candidate from a stage-20A row.

    Parameters: `aggregation_row` is a persisted model-review aggregation row.
    Return value: bounded `AdviceCandidate`.
    Failure scenarios: malformed JSON fields are treated as empty structures.
    External effects: none.
    """

    combined_text = _combined_text(aggregation_row)
    terminal_action, terminal_status = _detect_terminal_action(combined_text)
    risk_blocked, risk_reasons = _detect_risk_blockers(aggregation_row, combined_text)
    directional_bias = _detect_directional_bias(combined_text)
    advice_action = _detect_advice_action(aggregation_row, combined_text, risk_blocked=risk_blocked)
    trade_permission = _trade_permission_for_action(advice_action, risk_blocked=risk_blocked)
    risk_summary = _risk_summary(aggregation_row, risk_reasons=risk_reasons, risk_blocked=risk_blocked)
    strategy_summary = _strategy_summary(aggregation_row)
    model_summary = _model_summary(aggregation_row)
    model_review_status_summary = _model_review_status_summary(aggregation_row)
    summary_text = _bounded_text(_text_attr(aggregation_row, "summary_text") or _candidate_summary(advice_action))
    lifecycle_reason = _build_lifecycle_reason(
        advice_action=advice_action,
        risk_blocked=risk_blocked,
        risk_reasons=risk_reasons,
        terminal_lifecycle_action=terminal_action,
    )
    semantic_signature = build_candidate_semantic_signature(
        advice_action=advice_action,
        directional_bias=directional_bias,
        trade_permission=trade_permission,
        summary_text=summary_text,
        risk_summary_json=risk_summary,
        strategy_summary_json=strategy_summary,
        model_summary_json=model_summary,
    )
    return AdviceCandidate(
        advice_action=advice_action,
        directional_bias=directional_bias,
        trade_permission=trade_permission,
        summary_text=summary_text,
        risk_summary_json=risk_summary,
        strategy_summary_json=strategy_summary,
        model_summary_json=model_summary,
        model_review_status_summary_json=model_review_status_summary,
        risk_blocked=risk_blocked,
        terminal_lifecycle_action=terminal_action,
        terminal_advice_status=terminal_status,
        lifecycle_reason=lifecycle_reason,
        semantic_signature=semantic_signature,
    )


def should_create_new_advice_without_active(candidate: AdviceCandidate, aggregation_row: Any) -> bool:
    """Return whether a no-active state should create a new advice row."""

    if candidate.risk_blocked:
        return False
    text = _combined_text(aggregation_row)
    if "create_new_advice" in text:
        return True
    return candidate.advice_action in {AdviceAction.CONDITIONAL_TRADE, AdviceAction.MANAGE_POSITION}


def lifecycle_action_without_active(candidate: AdviceCandidate) -> LifecycleAction:
    """Return the review action when no active advice exists."""

    if candidate.advice_action == AdviceAction.STOP_TRADING:
        return LifecycleAction.STOP_TRADING
    return LifecycleAction.WAIT_WITHOUT_ACTIVE_ADVICE


def active_advice_semantic_signature(active_advice: Any) -> str:
    """Return the same bounded semantic signature for an existing advice row."""

    return build_candidate_semantic_signature(
        advice_action=AdviceAction(str(getattr(active_advice, "advice_action", AdviceAction.WAIT.value))),
        directional_bias=DirectionalBias(str(getattr(active_advice, "directional_bias", DirectionalBias.UNKNOWN.value))),
        trade_permission=TradePermission(
            str(getattr(active_advice, "trade_permission", TradePermission.NOT_ALLOWED.value))
        ),
        summary_text=_bounded_text(str(getattr(active_advice, "summary_text", "") or "")),
        risk_summary_json=dict(load_json_text(getattr(active_advice, "risk_summary_json", "{}"), {})),
        strategy_summary_json=dict(load_json_text(getattr(active_advice, "strategy_summary_json", "{}"), {})),
        model_summary_json=dict(load_json_text(getattr(active_advice, "model_summary_json", "{}"), {})),
    )


def build_candidate_semantic_signature(
    *,
    advice_action: AdviceAction,
    directional_bias: DirectionalBias,
    trade_permission: TradePermission,
    summary_text: str,
    risk_summary_json: Mapping[str, Any],
    strategy_summary_json: Mapping[str, Any],
    model_summary_json: Mapping[str, Any],
) -> str:
    """Return a stable hash over fields that define substantial advice change."""

    value = {
        "advice_action": advice_action.value,
        "directional_bias": directional_bias.value,
        "trade_permission": trade_permission.value,
        "summary_text": _bounded_text(summary_text, max_length=240),
        "risk": _signature_subset(risk_summary_json),
        "strategy": _signature_subset(strategy_summary_json),
        "model": _signature_subset(model_summary_json),
    }
    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def _detect_terminal_action(text: str) -> tuple[LifecycleAction | None, AdviceStatus | None]:
    if "complete_active_advice" in text or "completed" in text:
        return LifecycleAction.COMPLETE_ACTIVE_ADVICE, AdviceStatus.COMPLETED
    if "invalidate_active_advice" in text or "invalidated" in text:
        return LifecycleAction.INVALIDATE_ACTIVE_ADVICE, AdviceStatus.INVALIDATED
    if "expire_active_advice" in text or "expired_advice" in text:
        return LifecycleAction.EXPIRE_ACTIVE_ADVICE, AdviceStatus.EXPIRED
    if "close_active_advice" in text or "closed_advice" in text:
        return LifecycleAction.CLOSE_ACTIVE_ADVICE, AdviceStatus.CLOSED
    return None, None


def _detect_advice_action(aggregation_row: Any, text: str, *, risk_blocked: bool) -> AdviceAction:
    if "stop_trading" in text:
        return AdviceAction.STOP_TRADING
    if "avoid_trade" in text:
        return AdviceAction.AVOID_TRADE
    if risk_blocked:
        if _bool_attr(aggregation_row, "model_review_expired") or "partial_success" in text or "high" in text:
            return AdviceAction.STOP_TRADING
        return AdviceAction.AVOID_TRADE
    if "manage_position" in text:
        return AdviceAction.MANAGE_POSITION
    if "conditional_trade" in text or "conditionally_allowed" in text:
        return AdviceAction.CONDITIONAL_TRADE
    if _bool_attr(aggregation_row, "directional_trade_allowed"):
        return AdviceAction.CONDITIONAL_TRADE
    if "wait" in text:
        return AdviceAction.WAIT
    return AdviceAction.WAIT


def _detect_directional_bias(text: str) -> DirectionalBias:
    if "mixed" in text or "conflict" in text:
        return DirectionalBias.MIXED
    if "bullish" in text or "long_bias" in text or " long" in f" {text}":
        return DirectionalBias.BULLISH
    if "bearish" in text or "short_bias" in text or " short" in f" {text}":
        return DirectionalBias.BEARISH
    if "neutral" in text or "range" in text:
        return DirectionalBias.NEUTRAL
    return DirectionalBias.UNKNOWN


def _trade_permission_for_action(action: AdviceAction, *, risk_blocked: bool) -> TradePermission:
    if risk_blocked:
        return TradePermission.NOT_ALLOWED
    if action == AdviceAction.CONDITIONAL_TRADE:
        return TradePermission.CONDITIONALLY_ALLOWED
    if action == AdviceAction.MANAGE_POSITION:
        return TradePermission.POSITION_MANAGEMENT_ONLY
    return TradePermission.NOT_ALLOWED


def _detect_risk_blockers(aggregation_row: Any, text: str) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    risk = _text_attr(aggregation_row, "risk_acceptability_summary").lower()
    conflict = _text_attr(aggregation_row, "strategy_conflict_summary").lower()
    evidence = _text_attr(aggregation_row, "evidence_quality_summary").lower()
    chain_status = _text_attr(aggregation_row, "model_review_chain_status").lower()
    status = _text_attr(aggregation_row, "status").lower()
    if "unacceptable" in risk or "not_acceptable" in risk:
        reasons.append("risk_acceptability_unacceptable")
    if "high" in conflict:
        reasons.append("strategy_conflict_high")
    if _bool_attr(aggregation_row, "model_review_expired") and not _bool_attr(aggregation_row, "model_review_invoked"):
        reasons.append("model_review_expired_without_new_invocation")
    if chain_status == "partial_success":
        reasons.append("model_review_chain_partial_success")
    if chain_status in {"failed", "blocked"}:
        reasons.append(f"model_review_chain_{chain_status}")
    if "missing" in evidence or "insufficient" in evidence:
        reasons.append("evidence_insufficient")
    if status and status not in {"success", "partial_success"}:
        reasons.append(f"upstream_aggregation_{status}")
    if "model_review_expired" in text:
        reasons.append("model_review_expired")
    return bool(reasons), tuple(dict.fromkeys(reasons))


def _risk_summary(aggregation_row: Any, *, risk_reasons: tuple[str, ...], risk_blocked: bool) -> Mapping[str, Any]:
    return {
        "risk_acceptability": _bounded_text(_text_attr(aggregation_row, "risk_acceptability_summary")),
        "risk_blocked": risk_blocked,
        "risk_block_reasons": list(risk_reasons),
        "risk_warnings": _bounded_sequence(load_json_text(getattr(aggregation_row, "risk_warnings_json", "[]"), [])),
        "missing_evidence": _bounded_sequence(
            load_json_text(getattr(aggregation_row, "missing_evidence_json", "[]"), [])
        ),
    }


def _strategy_summary(aggregation_row: Any) -> Mapping[str, Any]:
    return {
        "review_decision": _bounded_text(_text_attr(aggregation_row, "review_decision_summary")),
        "evidence_quality": _bounded_text(_text_attr(aggregation_row, "evidence_quality_summary")),
        "strategy_conflict": _bounded_text(_text_attr(aggregation_row, "strategy_conflict_summary")),
        "allowed_advice_mode": _bounded_text(_text_attr(aggregation_row, "allowed_advice_mode")),
    }


def _model_summary(aggregation_row: Any) -> Mapping[str, Any]:
    return {
        "model_review_invoked": _bool_attr(aggregation_row, "model_review_invoked"),
        "model_review_invocation_mode": _bounded_text(_text_attr(aggregation_row, "model_review_invocation_mode")),
        "model_review_reused": _bool_attr(aggregation_row, "model_review_reused"),
        "reused_model_analysis_run_id": _bounded_text(_text_attr(aggregation_row, "reused_model_analysis_run_id")),
        "model_review_basis": _bounded_text(_text_attr(aggregation_row, "model_review_basis")),
        "model_review_expired": _bool_attr(aggregation_row, "model_review_expired"),
        "model_review_chain_status": _bounded_text(_text_attr(aggregation_row, "model_review_chain_status")),
        "model_review_skip_reason": _bounded_text(_text_attr(aggregation_row, "model_review_skip_reason")),
        "model_review_block_reason": _bounded_text(_text_attr(aggregation_row, "model_review_block_reason")),
        "invoked_model_keys": _bounded_sequence(
            load_json_text(getattr(aggregation_row, "invoked_model_keys_json", "[]"), [])
        ),
        "invoked_model_roles": _bounded_sequence(
            load_json_text(getattr(aggregation_row, "invoked_model_roles_json", "[]"), [])
        ),
    }


def _model_review_status_summary(aggregation_row: Any) -> Mapping[str, Any]:
    return {
        "invoked": _bool_attr(aggregation_row, "model_review_invoked"),
        "invocation_mode": _bounded_text(_text_attr(aggregation_row, "model_review_invocation_mode")),
        "reused": _bool_attr(aggregation_row, "model_review_reused"),
        "reused_model_analysis_run_id": _bounded_text(_text_attr(aggregation_row, "reused_model_analysis_run_id")),
        "skip_reason": _bounded_text(_text_attr(aggregation_row, "model_review_skip_reason")),
        "block_reason": _bounded_text(_text_attr(aggregation_row, "model_review_block_reason")),
        "basis": _bounded_text(_text_attr(aggregation_row, "model_review_basis")),
        "expired": _bool_attr(aggregation_row, "model_review_expired"),
        "chain_status": _bounded_text(_text_attr(aggregation_row, "model_review_chain_status")),
        "invoked_model_keys": _bounded_sequence(
            load_json_text(getattr(aggregation_row, "invoked_model_keys_json", "[]"), [])
        ),
        "invoked_model_roles": _bounded_sequence(
            load_json_text(getattr(aggregation_row, "invoked_model_roles_json", "[]"), [])
        ),
    }


def _combined_text(aggregation_row: Any) -> str:
    fields = (
        "review_decision_summary",
        "evidence_quality_summary",
        "risk_acceptability_summary",
        "strategy_conflict_summary",
        "model_review_basis",
        "model_review_reuse_status",
        "model_review_chain_status",
        "allowed_advice_mode",
        "summary_text",
        "error_code",
        "error_message",
    )
    return " ".join(_text_attr(aggregation_row, field_name).lower() for field_name in fields)


def _build_lifecycle_reason(
    *,
    advice_action: AdviceAction,
    risk_blocked: bool,
    risk_reasons: tuple[str, ...],
    terminal_lifecycle_action: LifecycleAction | None,
) -> str:
    if terminal_lifecycle_action is not None:
        return f"Stage-21A selected terminal lifecycle action {terminal_lifecycle_action.value}."
    if risk_blocked:
        return "Risk boundary prevents active conditional setup: " + ", ".join(risk_reasons)
    if advice_action == AdviceAction.CONDITIONAL_TRADE:
        return "Stage-20 aggregation permits only conditional human review; 21A keeps execution disabled."
    if advice_action == AdviceAction.MANAGE_POSITION:
        return "Stage-20 aggregation indicates position-management-only human review."
    return "No confirmed conditional setup is available; keep waiting without automatic execution."


def _candidate_summary(action: AdviceAction) -> str:
    if action == AdviceAction.CONDITIONAL_TRADE:
        return "Conditional human review setup only; 21A does not create executable instructions."
    if action == AdviceAction.STOP_TRADING:
        return "Stop trading posture selected by risk or model-review status."
    if action == AdviceAction.AVOID_TRADE:
        return "Avoid trade posture selected by risk or evidence status."
    return "Wait posture selected by stage-21A lifecycle rules."


def _signature_subset(value: Mapping[str, Any]) -> Mapping[str, Any]:
    allowed = (
        "risk_acceptability",
        "risk_blocked",
        "risk_block_reasons",
        "review_decision",
        "evidence_quality",
        "strategy_conflict",
        "allowed_advice_mode",
        "model_review_invocation_mode",
        "model_review_reused",
        "model_review_basis",
        "model_review_expired",
        "model_review_chain_status",
    )
    return {key: value.get(key) for key in allowed if key in value}


def _bounded_sequence(value: Any, *, limit: int = 6) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    bounded = []
    for item in value[:limit]:
        if isinstance(item, (str, int, float, bool)) or item is None:
            bounded.append(item)
        elif isinstance(item, Mapping):
            bounded.append({str(key): _bounded_text(str(val)) for key, val in list(item.items())[:8]})
        else:
            bounded.append(_bounded_text(str(item)))
    return bounded


def _bounded_text(value: str, *, max_length: int = 480) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 15]}...[truncated]"


def _text_attr(row: Any, field_name: str) -> str:
    value = getattr(row, field_name, "")
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _bool_attr(row: Any, field_name: str) -> bool:
    return bool(getattr(row, field_name, False))


__all__ = [
    "AdviceCandidate",
    "active_advice_semantic_signature",
    "build_advice_candidate_from_aggregation",
    "build_candidate_semantic_signature",
    "lifecycle_action_without_active",
    "should_create_new_advice_without_active",
]
