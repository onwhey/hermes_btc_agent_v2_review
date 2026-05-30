"""Notification payload builder for stage-21A strategy advice.

This file belongs to `app/strategy_advice`. It creates bounded structured
notification fields for future 21B Hermes delivery. It does not send Hermes,
read/write databases, call model providers, touch Redis, modify Kline data, or
execute trading.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.strategy_advice.lifecycle import AdviceCandidate
from app.strategy_advice.schema import (
    STRATEGY_ADVICE_PAYLOAD_SCHEMA_VERSION,
    AdviceAction,
    LifecycleAction,
    load_json_text,
)


def build_notification_payload(
    *,
    lifecycle_action: LifecycleAction,
    lifecycle_reason: str,
    aggregation_row: Any,
    candidate: AdviceCandidate,
    reviewed_advice_id: str | None,
    result_advice_id: str | None,
    advice_code: str | None,
    advice_path: str | None,
    notification_level: str,
    trade_setup_count: int,
    evidence_chain_summary: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Build a bounded notification payload for future Hermes delivery.

    Parameters: lifecycle context, source aggregation row, candidate, and
    advice ids.
    Return value: JSON-serializable mapping.
    Failure scenarios: malformed upstream JSON fields are represented as empty
    structures.
    External effects: none. This function does not send anything.
    """

    model_review = _model_review_payload(aggregation_row)
    if not model_review["model_review_invoked"]:
        model_review["no_model_invocation_reason"] = (
            model_review.get("model_review_skip_reason")
            or model_review.get("model_review_block_reason")
            or "stage21a_does_not_call_models"
        )
    if model_review["model_review_reused"]:
        model_review["reused_notice"] = {
            "reused_model_analysis_run_id": model_review.get("reused_model_analysis_run_id"),
        }
    if model_review["model_review_expired"]:
        model_review["expired_notice"] = "model review is expired and must not be treated as fresh"
    if model_review["model_review_chain_status"] == "partial_success":
        model_review["partial_success_notice"] = "partial_success cannot be reported as a complete model relay review"

    evidence_summary = dict(evidence_chain_summary or {})
    strategy_evidence_chain = dict(evidence_summary.get("strategy_evidence_chain") or {})
    model_review_summary = dict(evidence_summary.get("model_review_summary") or {})
    return {
        "schema_version": STRATEGY_ADVICE_PAYLOAD_SCHEMA_VERSION,
        "lifecycle": {
            "action": lifecycle_action.value,
            "reason": _bounded_text(lifecycle_reason),
            "notification_level": notification_level,
            "reviewed_advice_id": reviewed_advice_id,
            "result_advice_id": result_advice_id,
        },
        "advice": {
            "advice_id": result_advice_id,
            "advice_code": advice_code,
            "advice_path": advice_path,
            "advice_action": candidate.advice_action.value,
            "directional_bias": candidate.directional_bias.value,
            "trade_permission": candidate.trade_permission.value,
            "risk_blocked": candidate.risk_blocked,
        },
        "source": {
            "review_aggregation_run_id": _text_attr(aggregation_row, "review_aggregation_run_id"),
            "material_pack_id": _text_attr(aggregation_row, "material_pack_id"),
            "strategy_signal_run_id": _text_attr(aggregation_row, "strategy_signal_run_id"),
            "snapshot_id": _text_attr(aggregation_row, "snapshot_id"),
        },
        "model_review": model_review,
        "evidence_chain_summary": evidence_summary,
        "strategy_evidence_chain": strategy_evidence_chain,
        "model_review_summary": model_review_summary,
        "risk": dict(candidate.risk_summary_json),
        "strategy": dict(candidate.strategy_summary_json),
        "trade_setup": {
            "created_setup_count": trade_setup_count,
            "is_executable": False,
            "auto_trading_allowed": False,
        },
        "boundaries": {
            "stage21a_calls_model": False,
            "stage21a_sends_hermes": False,
            "not_trading_advice": True,
            "is_final_trading_advice": False,
            "is_trading_signal": False,
            "is_executable": False,
            "auto_trading_allowed": False,
        },
    }


def notification_level_for_lifecycle(action: LifecycleAction, candidate: AdviceCandidate) -> str:
    """Return `brief` for continuation and `full` for material lifecycle changes."""

    if action == LifecycleAction.CONTINUE_ACTIVE_ADVICE and not candidate.risk_blocked:
        return "brief"
    if action == LifecycleAction.WAIT_WITHOUT_ACTIVE_ADVICE and candidate.advice_action == AdviceAction.WAIT:
        return "brief"
    return "full"


def notification_reason_for_lifecycle(action: LifecycleAction, candidate: AdviceCandidate) -> str:
    """Return a compact reason string for notification metadata."""

    if action == LifecycleAction.CONTINUE_ACTIVE_ADVICE:
        return "active advice continued without substantial change"
    if action == LifecycleAction.CREATE_NEW_ADVICE:
        return "new active advice created"
    if action == LifecycleAction.UPDATE_ACTIVE_ADVICE:
        return "active advice updated with a new version"
    if action in {
        LifecycleAction.CLOSE_ACTIVE_ADVICE,
        LifecycleAction.COMPLETE_ACTIVE_ADVICE,
        LifecycleAction.INVALIDATE_ACTIVE_ADVICE,
        LifecycleAction.EXPIRE_ACTIVE_ADVICE,
    }:
        return f"active advice terminal action: {action.value}"
    if action == LifecycleAction.STOP_TRADING:
        return "no active advice; risk posture is stop_trading"
    if candidate.risk_blocked:
        return "risk boundary blocked active conditional setup"
    return "no active advice; wait payload created"


def _model_review_payload(aggregation_row: Any) -> dict[str, Any]:
    return {
        "model_review_invoked": _bool_attr(aggregation_row, "model_review_invoked"),
        "model_review_invocation_mode": _bounded_text(_text_attr(aggregation_row, "model_review_invocation_mode")),
        "model_review_reused": _bool_attr(aggregation_row, "model_review_reused"),
        "reused_model_analysis_run_id": _bounded_text(_text_attr(aggregation_row, "reused_model_analysis_run_id")),
        "model_review_skip_reason": _bounded_text(_text_attr(aggregation_row, "model_review_skip_reason")),
        "model_review_block_reason": _bounded_text(_text_attr(aggregation_row, "model_review_block_reason")),
        "invoked_model_keys_json": _bounded_sequence(
            load_json_text(getattr(aggregation_row, "invoked_model_keys_json", "[]"), [])
        ),
        "invoked_model_roles_json": _bounded_sequence(
            load_json_text(getattr(aggregation_row, "invoked_model_roles_json", "[]"), [])
        ),
        "model_review_chain_status": _bounded_text(_text_attr(aggregation_row, "model_review_chain_status")),
        "latest_model_review_at_utc": _text_attr(aggregation_row, "latest_model_review_at_utc"),
        "model_review_basis": _bounded_text(_text_attr(aggregation_row, "model_review_basis")),
        "model_review_expired": _bool_attr(aggregation_row, "model_review_expired"),
    }


def _bounded_sequence(value: Any, *, limit: int = 8) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_bounded_text(str(item), max_length=120) for item in value[:limit]]


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
    "build_notification_payload",
    "notification_level_for_lifecycle",
    "notification_reason_for_lifecycle",
]
