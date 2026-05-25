"""Decimal calculations for stage-22A manual execution feedback.

This `app/manual_execution` file is called by the service to calculate
quantity, fee, average entry, cost basis, realized PnL, margin ratio, and
effective leverage with `Decimal`. It does not access external services, MySQL,
Redis, Hermes, DeepSeek, exchange accounts, or automatic trading.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.exceptions import ValidationError
from app.manual_execution.constants import (
    ACTION_ADD_POSITION,
    ACTION_CLOSE_POSITION,
    ACTION_OPEN_POSITION,
    ACTION_REDUCE_POSITION,
    ACTION_STOP_LOSS,
    ACTION_TAKE_PROFIT,
    CLOSING_ACTIONS,
    MANUAL_DISCIPLINE_EFFECTIVE_LEVERAGE_LIMIT,
    POSITION_STATUS_CLOSED,
    POSITION_STATUS_OPEN,
    REVIEW_STATUS_NOT_REVIEWED,
    SIDE_LONG,
    SIDE_SHORT,
)
from app.manual_execution.decimal_utils import (
    DECIMAL_SCALE,
    ONE,
    ZERO,
    decimal_to_text,
    parse_decimal_value,
    parse_fee_rate,
    parse_optional_decimal_value,
    quantize_decimal,
)

LEVERAGE_WARNING_LIMIT = Decimal(MANUAL_DISCIPLINE_EFFECTIVE_LEVERAGE_LIMIT)


@dataclass(frozen=True)
class ManualPositionState:
    """Calculated manual position state after one user-reported action."""

    manual_position_id: str
    symbol: str
    side: str
    status: str
    opened_at_utc: datetime
    closed_at_utc: datetime | None
    opened_by_advice_id: str
    latest_related_advice_id: str
    closed_by_advice_id: str | None
    initial_entry_price: Decimal
    avg_entry_price: Decimal
    close_price: Decimal | None
    current_quantity_base_asset: Decimal
    current_cost_basis_usdt: Decimal
    margin_basis_usdt: Decimal
    effective_leverage: Decimal
    total_open_notional_usdt: Decimal
    total_close_notional_usdt: Decimal
    total_fee_usdt: Decimal
    gross_realized_pnl_usdt: Decimal
    net_realized_pnl_usdt: Decimal
    net_pnl_ratio_on_margin: Decimal
    open_reason: str | None
    open_decision_context: str | None
    review_status: str
    trigger_source: str
    created_by: str
    trace_id: str
    created_at_utc: datetime
    updated_at_utc: datetime


@dataclass(frozen=True)
class ManualExecutionMath:
    """Calculated row values for one execution record plus resulting state."""

    position_after: ManualPositionState
    price: Decimal
    notional_usdt: Decimal
    quantity_base_asset: Decimal
    margin_usdt: Decimal | None
    fee_rate: Decimal
    fee_usdt: Decimal
    gross_pnl_usdt: Decimal
    net_pnl_usdt: Decimal
    warnings: tuple[str, ...] = ()


def state_from_row(row: Any) -> ManualPositionState:
    """Build a calculation state from a repository row or test double."""

    return ManualPositionState(
        manual_position_id=str(getattr(row, "manual_position_id")),
        symbol=str(getattr(row, "symbol")),
        side=str(getattr(row, "side")),
        status=str(getattr(row, "status")),
        opened_at_utc=getattr(row, "opened_at_utc"),
        closed_at_utc=getattr(row, "closed_at_utc", None),
        opened_by_advice_id=str(getattr(row, "opened_by_advice_id")),
        latest_related_advice_id=str(getattr(row, "latest_related_advice_id")),
        closed_by_advice_id=getattr(row, "closed_by_advice_id", None),
        initial_entry_price=parse_decimal_value(getattr(row, "initial_entry_price"), "initial_entry_price"),
        avg_entry_price=parse_decimal_value(getattr(row, "avg_entry_price"), "avg_entry_price"),
        close_price=parse_optional_decimal_value(getattr(row, "close_price", None), "close_price"),
        current_quantity_base_asset=parse_decimal_value(
            getattr(row, "current_quantity_base_asset"),
            "current_quantity_base_asset",
        ),
        current_cost_basis_usdt=parse_decimal_value(getattr(row, "current_cost_basis_usdt"), "current_cost_basis_usdt"),
        margin_basis_usdt=parse_decimal_value(getattr(row, "margin_basis_usdt"), "margin_basis_usdt"),
        effective_leverage=parse_decimal_value(getattr(row, "effective_leverage"), "effective_leverage"),
        total_open_notional_usdt=parse_decimal_value(getattr(row, "total_open_notional_usdt"), "total_open_notional_usdt"),
        total_close_notional_usdt=parse_decimal_value(
            getattr(row, "total_close_notional_usdt"),
            "total_close_notional_usdt",
        ),
        total_fee_usdt=parse_decimal_value(getattr(row, "total_fee_usdt"), "total_fee_usdt"),
        gross_realized_pnl_usdt=parse_decimal_value(
            getattr(row, "gross_realized_pnl_usdt"),
            "gross_realized_pnl_usdt",
        ),
        net_realized_pnl_usdt=parse_decimal_value(getattr(row, "net_realized_pnl_usdt"), "net_realized_pnl_usdt"),
        net_pnl_ratio_on_margin=parse_decimal_value(
            getattr(row, "net_pnl_ratio_on_margin"),
            "net_pnl_ratio_on_margin",
        ),
        open_reason=getattr(row, "open_reason", None),
        open_decision_context=getattr(row, "open_decision_context", None),
        review_status=str(getattr(row, "review_status", REVIEW_STATUS_NOT_REVIEWED)),
        trigger_source=str(getattr(row, "trigger_source")),
        created_by=str(getattr(row, "created_by")),
        trace_id=str(getattr(row, "trace_id")),
        created_at_utc=getattr(row, "created_at_utc"),
        updated_at_utc=getattr(row, "updated_at_utc"),
    )


def calculate_open_position(
    *,
    manual_position_id: str,
    symbol: str,
    side: str,
    advice_id: str,
    price: Decimal,
    notional_usdt: Decimal,
    margin_usdt: Decimal,
    fee_rate: Decimal,
    reason: str,
    note: str,
    trigger_source: str,
    created_by: str,
    trace_id: str,
    executed_at_utc: datetime,
) -> ManualExecutionMath:
    """Calculate a new manual position from one user-reported open action."""

    _require_positive(price, "price")
    _require_positive(notional_usdt, "notional_usdt")
    if margin_usdt <= ONE:
        raise ValidationError("open_position margin_usdt must be > 1")

    quantity = quantize_decimal(notional_usdt / price)
    fee = quantize_decimal(notional_usdt * fee_rate)
    net_pnl = quantize_decimal(ZERO - fee)
    state = ManualPositionState(
        manual_position_id=manual_position_id,
        symbol=symbol,
        side=side,
        status=POSITION_STATUS_OPEN,
        opened_at_utc=executed_at_utc,
        closed_at_utc=None,
        opened_by_advice_id=advice_id,
        latest_related_advice_id=advice_id,
        closed_by_advice_id=None,
        initial_entry_price=quantize_decimal(price),
        avg_entry_price=quantize_decimal(price),
        close_price=None,
        current_quantity_base_asset=quantity,
        current_cost_basis_usdt=quantize_decimal(notional_usdt),
        margin_basis_usdt=quantize_decimal(margin_usdt),
        effective_leverage=quantize_decimal(notional_usdt / margin_usdt),
        total_open_notional_usdt=quantize_decimal(notional_usdt),
        total_close_notional_usdt=ZERO,
        total_fee_usdt=fee,
        gross_realized_pnl_usdt=ZERO,
        net_realized_pnl_usdt=net_pnl,
        net_pnl_ratio_on_margin=quantize_decimal(net_pnl / margin_usdt),
        open_reason=reason or None,
        open_decision_context=note or None,
        review_status=REVIEW_STATUS_NOT_REVIEWED,
        trigger_source=trigger_source,
        created_by=created_by,
        trace_id=trace_id,
        created_at_utc=executed_at_utc,
        updated_at_utc=executed_at_utc,
    )
    return ManualExecutionMath(
        position_after=state,
        price=quantize_decimal(price),
        notional_usdt=quantize_decimal(notional_usdt),
        quantity_base_asset=quantity,
        margin_usdt=quantize_decimal(margin_usdt),
        fee_rate=fee_rate,
        fee_usdt=fee,
        gross_pnl_usdt=ZERO,
        net_pnl_usdt=net_pnl,
        warnings=_leverage_warnings(state),
    )


def calculate_existing_position_action(
    *,
    action: str,
    state: ManualPositionState,
    advice_id: str,
    price: Decimal,
    notional_usdt: Decimal | None,
    margin_usdt: Decimal | None,
    fee_rate: Decimal,
    executed_at_utc: datetime,
) -> ManualExecutionMath:
    """Calculate add/reduce/close results for an existing open manual position."""

    _require_positive(price, "price")
    if state.status != POSITION_STATUS_OPEN:
        raise ValidationError("manual_position is already closed")
    if state.current_quantity_base_asset <= ZERO:
        raise ValidationError("manual_position current quantity must be > 0")

    if action == ACTION_ADD_POSITION:
        return _calculate_add_position(
            state=state,
            advice_id=advice_id,
            price=price,
            notional_usdt=_require_notional(notional_usdt),
            margin_usdt=_require_margin_for_add(margin_usdt),
            fee_rate=fee_rate,
            executed_at_utc=executed_at_utc,
        )
    if action == ACTION_REDUCE_POSITION:
        if margin_usdt is not None:
            raise ValidationError("reduce_position does not accept margin_usdt")
        return _calculate_reduce_position(
            state=state,
            advice_id=advice_id,
            price=price,
            notional_usdt=_require_notional(notional_usdt),
            fee_rate=fee_rate,
            executed_at_utc=executed_at_utc,
        )
    if action in CLOSING_ACTIONS:
        if margin_usdt is not None:
            raise ValidationError(f"{action} does not accept margin_usdt")
        if notional_usdt is not None:
            raise ValidationError(f"{action} does not accept notional_usdt")
        return _calculate_close_position(
            action=action,
            state=state,
            advice_id=advice_id,
            price=price,
            fee_rate=fee_rate,
            executed_at_utc=executed_at_utc,
        )
    raise ValidationError(f"unsupported execution_action: {action}")


def position_snapshot(state: ManualPositionState) -> dict[str, str]:
    """Build a stable user-facing snapshot from calculated state."""

    return {
        "manual_position_id": state.manual_position_id,
        "symbol": state.symbol,
        "side": state.side,
        "status": state.status,
        "avg_entry_price": decimal_to_text(state.avg_entry_price),
        "current_quantity_base_asset": decimal_to_text(state.current_quantity_base_asset),
        "current_cost_basis_usdt": decimal_to_text(state.current_cost_basis_usdt),
        "margin_basis_usdt": decimal_to_text(state.margin_basis_usdt),
        "effective_leverage": decimal_to_text(state.effective_leverage),
        "total_fee_usdt": decimal_to_text(state.total_fee_usdt),
        "gross_realized_pnl_usdt": decimal_to_text(state.gross_realized_pnl_usdt),
        "net_realized_pnl_usdt": decimal_to_text(state.net_realized_pnl_usdt),
        "net_pnl_ratio_on_margin": decimal_to_text(state.net_pnl_ratio_on_margin),
    }


def execution_snapshot(math: ManualExecutionMath) -> dict[str, str]:
    """Build a stable user-facing snapshot from one calculated execution."""

    return {
        "price": decimal_to_text(math.price),
        "notional_usdt": decimal_to_text(math.notional_usdt),
        "quantity_base_asset": decimal_to_text(math.quantity_base_asset),
        "margin_usdt": decimal_to_text(math.margin_usdt),
        "fee_rate": decimal_to_text(math.fee_rate),
        "fee_usdt": decimal_to_text(math.fee_usdt),
        "gross_pnl_usdt": decimal_to_text(math.gross_pnl_usdt),
        "net_pnl_usdt": decimal_to_text(math.net_pnl_usdt),
    }


def _calculate_add_position(
    *,
    state: ManualPositionState,
    advice_id: str,
    price: Decimal,
    notional_usdt: Decimal,
    margin_usdt: Decimal,
    fee_rate: Decimal,
    executed_at_utc: datetime,
) -> ManualExecutionMath:
    raw_new_quantity = notional_usdt / price
    new_quantity = quantize_decimal(raw_new_quantity)
    raw_total_quantity = state.current_quantity_base_asset + raw_new_quantity
    total_quantity = quantize_decimal(state.current_quantity_base_asset + new_quantity)
    if total_quantity <= ZERO:
        raise ValidationError("add_position total quantity must be > 0")
    new_avg = quantize_decimal(
        ((state.current_quantity_base_asset * state.avg_entry_price) + notional_usdt) / raw_total_quantity
    )
    fee = quantize_decimal(notional_usdt * fee_rate)
    total_fee = quantize_decimal(state.total_fee_usdt + fee)
    gross_total = state.gross_realized_pnl_usdt
    net_total = quantize_decimal(gross_total - total_fee)
    margin_basis = quantize_decimal(state.margin_basis_usdt + margin_usdt)
    if margin_basis <= ZERO:
        raise ValidationError("margin_basis_usdt must be > 0")
    after = replace(
        state,
        latest_related_advice_id=advice_id,
        avg_entry_price=new_avg,
        current_quantity_base_asset=total_quantity,
        current_cost_basis_usdt=quantize_decimal(state.current_cost_basis_usdt + notional_usdt),
        margin_basis_usdt=margin_basis,
        effective_leverage=quantize_decimal((state.current_cost_basis_usdt + notional_usdt) / margin_basis),
        total_open_notional_usdt=quantize_decimal(state.total_open_notional_usdt + notional_usdt),
        total_fee_usdt=total_fee,
        net_realized_pnl_usdt=net_total,
        net_pnl_ratio_on_margin=quantize_decimal(net_total / margin_basis),
        updated_at_utc=executed_at_utc,
    )
    return ManualExecutionMath(
        position_after=after,
        price=quantize_decimal(price),
        notional_usdt=quantize_decimal(notional_usdt),
        quantity_base_asset=new_quantity,
        margin_usdt=quantize_decimal(margin_usdt),
        fee_rate=fee_rate,
        fee_usdt=fee,
        gross_pnl_usdt=ZERO,
        net_pnl_usdt=quantize_decimal(ZERO - fee),
        warnings=_leverage_warnings(after),
    )


def _calculate_reduce_position(
    *,
    state: ManualPositionState,
    advice_id: str,
    price: Decimal,
    notional_usdt: Decimal,
    fee_rate: Decimal,
    executed_at_utc: datetime,
) -> ManualExecutionMath:
    raw_reduce_quantity = notional_usdt / price
    reduce_quantity = quantize_decimal(raw_reduce_quantity)
    if reduce_quantity > state.current_quantity_base_asset:
        raise ValidationError("reduce_position quantity exceeds current manual position quantity")
    cost_basis_reduced = quantize_decimal(state.avg_entry_price * raw_reduce_quantity)
    gross = _realized_pnl(
        side=state.side,
        avg_entry_price=state.avg_entry_price,
        exit_price=price,
        quantity=raw_reduce_quantity,
    )
    fee = quantize_decimal(notional_usdt * fee_rate)
    net = quantize_decimal(gross - fee)
    remaining_quantity = _zero_if_tiny(state.current_quantity_base_asset - reduce_quantity)
    remaining_cost_basis = _zero_if_tiny(state.current_cost_basis_usdt - cost_basis_reduced)
    total_fee = quantize_decimal(state.total_fee_usdt + fee)
    gross_total = quantize_decimal(state.gross_realized_pnl_usdt + gross)
    net_total = quantize_decimal(gross_total - total_fee)
    after = replace(
        state,
        latest_related_advice_id=advice_id,
        current_quantity_base_asset=quantize_decimal(remaining_quantity),
        current_cost_basis_usdt=quantize_decimal(remaining_cost_basis),
        effective_leverage=quantize_decimal(remaining_cost_basis / state.margin_basis_usdt),
        total_close_notional_usdt=quantize_decimal(state.total_close_notional_usdt + notional_usdt),
        total_fee_usdt=total_fee,
        gross_realized_pnl_usdt=gross_total,
        net_realized_pnl_usdt=net_total,
        net_pnl_ratio_on_margin=quantize_decimal(net_total / state.margin_basis_usdt),
        updated_at_utc=executed_at_utc,
    )
    return ManualExecutionMath(
        position_after=after,
        price=quantize_decimal(price),
        notional_usdt=quantize_decimal(notional_usdt),
        quantity_base_asset=reduce_quantity,
        margin_usdt=None,
        fee_rate=fee_rate,
        fee_usdt=fee,
        gross_pnl_usdt=gross,
        net_pnl_usdt=net,
        warnings=_leverage_warnings(after),
    )


def _calculate_close_position(
    *,
    action: str,
    state: ManualPositionState,
    advice_id: str,
    price: Decimal,
    fee_rate: Decimal,
    executed_at_utc: datetime,
) -> ManualExecutionMath:
    del action
    exit_quantity = state.current_quantity_base_asset
    close_notional = quantize_decimal(exit_quantity * price)
    fee = quantize_decimal(close_notional * fee_rate)
    gross = _realized_pnl(side=state.side, avg_entry_price=state.avg_entry_price, exit_price=price, quantity=exit_quantity)
    net = quantize_decimal(gross - fee)
    total_fee = quantize_decimal(state.total_fee_usdt + fee)
    gross_total = quantize_decimal(state.gross_realized_pnl_usdt + gross)
    net_total = quantize_decimal(gross_total - total_fee)
    after = replace(
        state,
        status=POSITION_STATUS_CLOSED,
        closed_at_utc=executed_at_utc,
        latest_related_advice_id=advice_id,
        closed_by_advice_id=advice_id,
        close_price=quantize_decimal(price),
        current_quantity_base_asset=ZERO,
        current_cost_basis_usdt=ZERO,
        effective_leverage=ZERO,
        total_close_notional_usdt=quantize_decimal(state.total_close_notional_usdt + close_notional),
        total_fee_usdt=total_fee,
        gross_realized_pnl_usdt=gross_total,
        net_realized_pnl_usdt=net_total,
        net_pnl_ratio_on_margin=quantize_decimal(net_total / state.margin_basis_usdt),
        updated_at_utc=executed_at_utc,
    )
    return ManualExecutionMath(
        position_after=after,
        price=quantize_decimal(price),
        notional_usdt=close_notional,
        quantity_base_asset=exit_quantity,
        margin_usdt=None,
        fee_rate=fee_rate,
        fee_usdt=fee,
        gross_pnl_usdt=gross,
        net_pnl_usdt=net,
        warnings=_leverage_warnings(after),
    )


def _realized_pnl(*, side: str, avg_entry_price: Decimal, exit_price: Decimal, quantity: Decimal) -> Decimal:
    if side == SIDE_LONG:
        return quantize_decimal((exit_price - avg_entry_price) * quantity)
    if side == SIDE_SHORT:
        return quantize_decimal((avg_entry_price - exit_price) * quantity)
    raise ValidationError(f"unsupported side: {side}")


def _require_notional(value: Decimal | None) -> Decimal:
    if value is None:
        raise ValidationError("notional_usdt is required")
    _require_positive(value, "notional_usdt")
    return value


def _require_margin_for_add(value: Decimal | None) -> Decimal:
    if value is None:
        raise ValidationError("add_position margin_usdt is required")
    if value < ZERO:
        raise ValidationError("add_position margin_usdt must be >= 0")
    return value


def _require_positive(value: Decimal, field_name: str) -> None:
    if value <= ZERO:
        raise ValidationError(f"{field_name} must be > 0")


def _zero_if_tiny(value: Decimal) -> Decimal:
    if abs(value) < DECIMAL_SCALE:
        return ZERO
    return value


def _leverage_warnings(state: ManualPositionState) -> tuple[str, ...]:
    if state.status == POSITION_STATUS_CLOSED:
        return ()
    if state.effective_leverage > LEVERAGE_WARNING_LIMIT:
        return (f"当前有效杠杆 {decimal_to_text(state.effective_leverage)}x，超过 5x 风险纪律边界。",)
    return ()
