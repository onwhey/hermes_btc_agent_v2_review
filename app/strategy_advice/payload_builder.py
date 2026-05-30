"""Persistence payload builders for stage-21A strategy advice.

This file belongs to `app/strategy_advice`. It converts one lifecycle plan
context into bounded repository payloads for advice, lifecycle reviews, and
events.

Called by `app/strategy_advice/service.py`. It does not access external
services, does not read/write MySQL by itself, does not touch Redis, does not
send Hermes, does not call model providers, and does not perform trading.
"""

from __future__ import annotations

from typing import Any

from app.core.time_utils import now_utc
from app.strategy_advice.id_utils import build_advice_code, build_strategy_advice_event_id
from app.strategy_advice.lifecycle import AdviceCandidate
from app.strategy_advice.schema import (
    AdviceEventType,
    AdviceStatus,
    LifecycleAction,
    StrategyAdviceEventPersistencePayload,
    StrategyAdviceLifecycleReviewPersistencePayload,
    StrategyAdvicePersistencePayload,
    StrategyAdviceRequest,
)


def build_strategy_advice_payload(
    *,
    request: StrategyAdviceRequest,
    aggregation_row: Any,
    candidate: AdviceCandidate,
    advice_id: str,
    parent_advice_id: str | None,
    root_advice_id: str,
    previous_advice_id: str | None,
    advice_path: str,
    version_no: int,
    advice_status: AdviceStatus,
) -> StrategyAdvicePersistencePayload:
    """Build a bounded `strategy_advice` payload without writing MySQL."""

    created_at = now_utc()
    return StrategyAdvicePersistencePayload(
        advice_id=advice_id,
        advice_code=build_advice_code(
            symbol=text_attr(aggregation_row, "symbol"),
            created_at_utc=created_at,
            version_no=version_no,
        ),
        symbol=text_attr(aggregation_row, "symbol"),
        base_interval=text_attr(aggregation_row, "base_interval"),
        higher_interval=text_attr(aggregation_row, "higher_interval"),
        parent_advice_id=parent_advice_id,
        root_advice_id=root_advice_id,
        previous_advice_id=previous_advice_id,
        advice_path=advice_path,
        version_no=version_no,
        advice_status=advice_status,
        advice_action=candidate.advice_action,
        directional_bias=candidate.directional_bias,
        trade_permission=candidate.trade_permission,
        source_review_aggregation_run_id=text_attr(aggregation_row, "review_aggregation_run_id"),
        source_material_pack_id=text_attr(aggregation_row, "material_pack_id"),
        source_strategy_signal_run_id=optional_text_attr(aggregation_row, "strategy_signal_run_id"),
        source_snapshot_id=optional_text_attr(aggregation_row, "snapshot_id"),
        source_model_chain_id=optional_text_attr(aggregation_row, "source_model_chain_id")
        or optional_text_attr(aggregation_row, "chain_id"),
        model_review_invoked=bool_attr(aggregation_row, "model_review_invoked"),
        model_review_invocation_mode=text_attr(aggregation_row, "model_review_invocation_mode") or "none",
        model_review_reused=bool_attr(aggregation_row, "model_review_reused"),
        reused_model_analysis_run_id=optional_text_attr(aggregation_row, "reused_model_analysis_run_id"),
        model_review_basis=text_attr(aggregation_row, "model_review_basis") or "none",
        model_review_expired=bool_attr(aggregation_row, "model_review_expired"),
        model_review_chain_status=text_attr(aggregation_row, "model_review_chain_status") or "not_started",
        latest_model_review_at_utc=getattr(aggregation_row, "latest_model_review_at_utc", None),
        model_review_status_summary_json=candidate.model_review_status_summary_json,
        summary_text=candidate.summary_text,
        risk_summary_json=candidate.risk_summary_json,
        strategy_summary_json=candidate.strategy_summary_json,
        model_summary_json=candidate.model_summary_json,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        closed_at_utc=None,
    )


def build_lifecycle_review_payload(
    *,
    review_id: str,
    aggregation_row: Any,
    lifecycle_action: LifecycleAction,
    lifecycle_reason: str,
    reviewed_advice_id: str | None,
    result_advice_id: str | None,
    previous_advice_id: str | None,
    notification_level: str,
    notification_reason: str,
    notification_payload: Any,
) -> StrategyAdviceLifecycleReviewPersistencePayload:
    """Build a bounded lifecycle-review payload without writing MySQL."""

    return StrategyAdviceLifecycleReviewPersistencePayload(
        review_id=review_id,
        symbol=text_attr(aggregation_row, "symbol"),
        base_interval=text_attr(aggregation_row, "base_interval"),
        higher_interval=text_attr(aggregation_row, "higher_interval"),
        reviewed_advice_id=reviewed_advice_id,
        result_advice_id=result_advice_id,
        previous_advice_id=previous_advice_id,
        lifecycle_action=lifecycle_action,
        lifecycle_reason=lifecycle_reason,
        source_review_aggregation_run_id=text_attr(aggregation_row, "review_aggregation_run_id"),
        source_material_pack_id=text_attr(aggregation_row, "material_pack_id"),
        source_strategy_signal_run_id=optional_text_attr(aggregation_row, "strategy_signal_run_id"),
        source_snapshot_id=optional_text_attr(aggregation_row, "snapshot_id"),
        model_review_invoked=bool_attr(aggregation_row, "model_review_invoked"),
        model_review_invocation_mode=text_attr(aggregation_row, "model_review_invocation_mode") or "none",
        model_review_reused=bool_attr(aggregation_row, "model_review_reused"),
        reused_model_analysis_run_id=optional_text_attr(aggregation_row, "reused_model_analysis_run_id"),
        model_review_basis=text_attr(aggregation_row, "model_review_basis") or "none",
        model_review_expired=bool_attr(aggregation_row, "model_review_expired"),
        model_review_chain_status=text_attr(aggregation_row, "model_review_chain_status") or "not_started",
        notification_required=True,
        notification_level=notification_level,
        notification_reason=notification_reason,
        notification_payload_json=notification_payload,
    )


def build_event_payloads(
    *,
    review_id: str,
    event_types: tuple[AdviceEventType, ...],
    event_advice_ids: tuple[str | None, ...],
    event_reason: str,
    event_payload: Any,
) -> tuple[StrategyAdviceEventPersistencePayload, ...]:
    """Build bounded lifecycle event payloads without writing MySQL."""

    payloads: list[StrategyAdviceEventPersistencePayload] = []
    for sequence_no, event_type in enumerate(event_types, start=1):
        advice_id = event_advice_ids[sequence_no - 1] if sequence_no - 1 < len(event_advice_ids) else None
        payloads.append(
            StrategyAdviceEventPersistencePayload(
                event_id=build_strategy_advice_event_id(
                    review_id=review_id,
                    event_type=event_type.value,
                    sequence_no=sequence_no,
                ),
                advice_id=advice_id,
                related_review_id=review_id,
                event_type=event_type,
                event_reason=event_reason,
                event_payload_json={
                    "event_type": event_type.value,
                    "advice_id": advice_id,
                    "notification_payload": event_payload
                    if event_type == AdviceEventType.NOTIFICATION_PAYLOAD_CREATED
                    else {},
                    "not_trading_advice": True,
                    "is_final_trading_advice": False,
                    "is_trading_signal": False,
                    "is_executable": False,
                    "auto_trading_allowed": False,
                },
            )
        )
    return tuple(payloads)


def event_type_for_terminal_status(status: AdviceStatus) -> AdviceEventType:
    """Return the event key that matches a terminal advice status."""

    if status == AdviceStatus.COMPLETED:
        return AdviceEventType.COMPLETED
    if status == AdviceStatus.INVALIDATED:
        return AdviceEventType.INVALIDATED
    if status == AdviceStatus.EXPIRED:
        return AdviceEventType.EXPIRED
    return AdviceEventType.CLOSED


def text_attr(row: Any, field_name: str) -> str:
    """Return one row attribute as stable text."""

    value = getattr(row, field_name, "")
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def optional_text_attr(row: Any, field_name: str) -> str | None:
    """Return a stripped text attribute or `None` for empty values."""

    text = text_attr(row, field_name).strip()
    return text or None


def bool_attr(row: Any, field_name: str) -> bool:
    """Return one row attribute as a boolean flag."""

    return bool(getattr(row, field_name, False))


__all__ = [
    "bool_attr",
    "build_event_payloads",
    "build_lifecycle_review_payload",
    "build_strategy_advice_payload",
    "event_type_for_terminal_status",
    "optional_text_attr",
    "text_attr",
]
