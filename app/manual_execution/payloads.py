"""Payload builders for stage-22A manual execution feedback.

This file belongs to `app/manual_execution`. It converts calculated manual
execution state into repository persistence payloads and CLI summaries. It does
not read/write MySQL by itself, read Redis, send Hermes, call DeepSeek, request
Binance, or perform automatic trading.
"""

from __future__ import annotations

from typing import Any

from datetime import datetime

from app.core.time_utils import ensure_utc_aware, format_datetime_with_timezone
from app.manual_execution.calculations import ManualExecutionMath, ManualPositionState, position_snapshot, state_from_row
from app.manual_execution.constants import REVIEW_STATUS_NOT_REVIEWED
from app.manual_execution.schema import (
    AdviceResolution,
    ExecutionRecordPersistencePayload,
    ManualExecutionRequest,
    ManualPositionPersistencePayload,
    ManualPositionSummary,
)


def position_payload_from_state(state: ManualPositionState) -> ManualPositionPersistencePayload:
    """Build a repository payload from calculated manual position state."""

    return ManualPositionPersistencePayload(
        manual_position_id=state.manual_position_id,
        symbol=state.symbol,
        side=state.side,
        status=state.status,
        opened_at_utc=state.opened_at_utc,
        closed_at_utc=state.closed_at_utc,
        opened_by_advice_id=state.opened_by_advice_id,
        latest_related_advice_id=state.latest_related_advice_id,
        closed_by_advice_id=state.closed_by_advice_id,
        initial_entry_price=state.initial_entry_price,
        avg_entry_price=state.avg_entry_price,
        close_price=state.close_price,
        current_quantity_base_asset=state.current_quantity_base_asset,
        current_cost_basis_usdt=state.current_cost_basis_usdt,
        margin_basis_usdt=state.margin_basis_usdt,
        effective_leverage=state.effective_leverage,
        total_open_notional_usdt=state.total_open_notional_usdt,
        total_close_notional_usdt=state.total_close_notional_usdt,
        total_fee_usdt=state.total_fee_usdt,
        gross_realized_pnl_usdt=state.gross_realized_pnl_usdt,
        net_realized_pnl_usdt=state.net_realized_pnl_usdt,
        net_pnl_ratio_on_margin=state.net_pnl_ratio_on_margin,
        open_reason=state.open_reason,
        open_decision_context=state.open_decision_context,
        review_status=REVIEW_STATUS_NOT_REVIEWED,
        trigger_source=state.trigger_source,
        created_by=state.created_by,
        trace_id=state.trace_id,
        created_at_utc=state.created_at_utc,
        updated_at_utc=state.updated_at_utc,
    )


def execution_payload_from_math(
    *,
    request: ManualExecutionRequest,
    math: ManualExecutionMath,
    execution_id: str,
    advice_resolution: AdviceResolution,
    position_resolution: str,
    executed_at_utc: Any,
) -> ExecutionRecordPersistencePayload:
    """Build a repository payload from calculated manual execution math."""

    return ExecutionRecordPersistencePayload(
        execution_id=execution_id,
        manual_position_id=math.position_after.manual_position_id,
        execution_action=request.action,
        symbol=math.position_after.symbol,
        side=math.position_after.side,
        price=math.price,
        notional_usdt=math.notional_usdt,
        quantity_base_asset=math.quantity_base_asset,
        margin_usdt=math.margin_usdt,
        fee_rate=math.fee_rate,
        fee_usdt=math.fee_usdt,
        gross_pnl_usdt=math.gross_pnl_usdt,
        net_pnl_usdt=math.net_pnl_usdt,
        advice_id=advice_resolution.advice_id,
        review_id=advice_resolution.review_id,
        setup_id=advice_resolution.setup_id,
        advice_resolution_method=advice_resolution.advice_resolution_method,
        setup_resolution_method=advice_resolution.setup_resolution_method,
        manual_position_resolution_method=position_resolution,
        reason=request.reason.strip() or None,
        note=request.note.strip() or None,
        executed_at_utc=executed_at_utc,
        trigger_source=request.trigger_source.strip(),
        created_by=request.created_by.strip() or "cli",
        trace_id=request.trace_id,
        created_at_utc=executed_at_utc,
    )


def summary_from_row(row: Any) -> ManualPositionSummary:
    """Build a user-facing open-position summary from a repository row."""

    state = state_from_row(row)
    snapshot = position_snapshot(state)
    return ManualPositionSummary(
        manual_position_id=state.manual_position_id,
        symbol=state.symbol,
        side=state.side,
        status=state.status,
        avg_entry_price=snapshot["avg_entry_price"],
        current_quantity_base_asset=snapshot["current_quantity_base_asset"],
        current_cost_basis_usdt=snapshot["current_cost_basis_usdt"],
        margin_basis_usdt=snapshot["margin_basis_usdt"],
        effective_leverage=snapshot["effective_leverage"],
        opened_at_utc=_format_optional_utc_datetime(state.opened_at_utc),
        opened_by_advice_id=state.opened_by_advice_id,
    )


def _format_optional_utc_datetime(value: datetime | None) -> str:
    """Format a UTC datetime read from MySQL without assuming tzinfo exists."""

    aware_value = ensure_utc_aware(value)
    if aware_value is None:
        return ""
    return format_datetime_with_timezone(aware_value)
