"""Read-only strategy context view for stage-23A.

This file belongs to `app/strategy/common`. It wraps an existing
`StrategyEvaluationInput` so strategy implementations can consume a stable
market-context view without querying storage or external services.
It does not access external services, read or write MySQL, read or write Redis,
send Hermes, call DeepSeek or any large language model, read private trading
state, generate final advice, modify Kline tables, or perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.strategy.types import StrategyEvaluationInput


@dataclass(frozen=True)
class StrategyContextView:
    """Read-only view over snapshot-derived strategy input.

    Parameters: copied from `StrategyEvaluationInput`.
    Return value: immutable context object with small helper methods.
    Failure scenarios: helper methods raise `ValueError` if the requested
    Kline window is empty.
    External service access: none.
    Data impact: none; this class never reads or writes databases or Redis.
    """

    snapshot_id: str
    symbol: str
    base_interval_value: str
    higher_interval_value: str
    base_klines: tuple[Any, ...]
    higher_klines: tuple[Any, ...]
    latest_base_open_time_ms: int
    latest_higher_open_time_ms: int
    latest_base_close_price: Decimal
    latest_higher_close_price: Decimal
    base_start_open_time_ms: int
    base_end_open_time_ms: int
    higher_start_open_time_ms: int
    higher_end_open_time_ms: int
    base_window_count: int
    higher_window_count: int
    trace_id: str
    evaluated_at_utc: datetime

    @classmethod
    def from_evaluation_input(cls, input_data: StrategyEvaluationInput) -> "StrategyContextView":
        """Build a read-only view from the existing stage-16 input DTO."""

        if not input_data.base_klines:
            raise ValueError("base Kline window is empty")
        if not input_data.higher_klines:
            raise ValueError("higher Kline window is empty")
        return cls(
            snapshot_id=input_data.snapshot_id,
            symbol=input_data.symbol,
            base_interval_value=input_data.base_interval_value,
            higher_interval_value=input_data.higher_interval_value,
            base_klines=tuple(input_data.base_klines),
            higher_klines=tuple(input_data.higher_klines),
            latest_base_open_time_ms=input_data.latest_base_open_time_ms,
            latest_higher_open_time_ms=input_data.latest_higher_open_time_ms,
            latest_base_close_price=_decimal_attr(input_data.base_klines[-1], "close_price"),
            latest_higher_close_price=_decimal_attr(input_data.higher_klines[-1], "close_price"),
            base_start_open_time_ms=input_data.base_start_open_time_ms,
            base_end_open_time_ms=input_data.base_end_open_time_ms,
            higher_start_open_time_ms=input_data.higher_start_open_time_ms,
            higher_end_open_time_ms=input_data.higher_end_open_time_ms,
            base_window_count=len(input_data.base_klines),
            higher_window_count=len(input_data.higher_klines),
            trace_id=input_data.trace_id,
            evaluated_at_utc=input_data.evaluated_at_utc,
        )

    def latest_base_close(self) -> Decimal:
        """Return the latest base-period close price."""

        return self.latest_base_close_price

    def recent_base_high(self, window: int) -> Decimal:
        """Return the maximum base-period high over the latest `window` rows."""

        rows = _recent_rows(self.base_klines, window)
        return max(_decimal_attr(row, "high_price") for row in rows)

    def recent_base_low(self, window: int) -> Decimal:
        """Return the minimum base-period low over the latest `window` rows."""

        rows = _recent_rows(self.base_klines, window)
        return min(_decimal_attr(row, "low_price") for row in rows)

    def recent_base_range(self, window: int) -> tuple[Decimal, Decimal]:
        """Return `(low, high)` over the latest base-period rows."""

        return self.recent_base_low(window), self.recent_base_high(window)


def _recent_rows(rows: tuple[Any, ...], window: int) -> tuple[Any, ...]:
    if window <= 0:
        raise ValueError("window must be greater than 0")
    selected = rows[-window:]
    if not selected:
        raise ValueError("requested Kline window is empty")
    return tuple(selected)


def _decimal_attr(row: Any, field_name: str) -> Decimal:
    return Decimal(str(getattr(row, field_name)))


__all__ = ["StrategyContextView"]
