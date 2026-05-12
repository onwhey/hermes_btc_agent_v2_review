"""Decimal price-change detector for 10s WebSocket price monitoring.

This file belongs to `app/market_data/price_monitor`.
It calculates price movement with `Decimal` only. It does not read/write Redis,
read/write MySQL, send Hermes, request Binance, call DeepSeek, generate advice,
or perform trading.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.market_data.price_monitor.exceptions import PriceMonitorValidationError
from app.market_data.price_monitor.types import PriceChangeResult, PriceEvent, PriceState


def detect_price_change(
    current_event: PriceEvent,
    previous_state: PriceState | None,
    *,
    threshold: Decimal | str = Decimal("0.01"),
) -> PriceChangeResult:
    """Detect whether current price movement reaches the configured threshold.

    Parameters: `current_event` is the latest valid WebSocket price;
    `previous_state` is the prior Redis state; `threshold` is a Decimal ratio.
    Return value: `PriceChangeResult` with direction, prices, ratio, percent.
    Failure scenarios: non-positive current price or invalid threshold raises
    `PriceMonitorValidationError`.
    External service access: none.
    Data impact: no Redis/MySQL writes, Hermes sends, DeepSeek, or trading.
    """

    parsed_threshold = parse_decimal_threshold(threshold)
    if current_event.price <= 0:
        raise PriceMonitorValidationError("current price must be greater than 0")
    if previous_state is None:
        return PriceChangeResult(
            has_previous=False,
            exceeded=False,
            direction="none",
            previous_price=None,
            current_price=current_event.price,
            change_ratio=Decimal("0"),
            change_percent=Decimal("0"),
            threshold=parsed_threshold,
            reason="previous_price_missing",
        )
    if previous_state.price <= 0:
        return PriceChangeResult(
            has_previous=True,
            exceeded=False,
            direction="none",
            previous_price=previous_state.price,
            current_price=current_event.price,
            change_ratio=Decimal("0"),
            change_percent=Decimal("0"),
            threshold=parsed_threshold,
            reason="previous_price_not_positive",
        )

    diff = current_event.price - previous_state.price
    direction = "up" if diff >= 0 else "down"
    change_ratio = abs(diff) / previous_state.price
    return PriceChangeResult(
        has_previous=True,
        exceeded=change_ratio >= parsed_threshold,
        direction=direction,
        previous_price=previous_state.price,
        current_price=current_event.price,
        change_ratio=change_ratio,
        change_percent=change_ratio * Decimal("100"),
        threshold=parsed_threshold,
    )


def parse_decimal_threshold(value: Decimal | str) -> Decimal:
    """Parse and validate the configured price-change threshold."""

    if isinstance(value, float):
        raise PriceMonitorValidationError("price monitor threshold must not be float")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PriceMonitorValidationError("price monitor threshold must be Decimal-compatible") from exc
    if parsed < 0:
        raise PriceMonitorValidationError("price monitor threshold must be greater than or equal to 0")
    return parsed
