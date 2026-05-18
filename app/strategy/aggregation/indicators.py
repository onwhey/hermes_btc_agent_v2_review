"""Deterministic indicator helpers for stage-18 material packs.

This file belongs to `app/strategy/aggregation`. It calculates swing points,
HH/HL/LH/LL structure, ATR, recent range expansion, and support/resistance
candidates from already restored snapshot Kline rows.

Called by: `app/strategy/aggregation/material_builder.py`.

External services: none. MySQL: read/write none. Redis: none. Hermes: none.
DeepSeek/large models: none. Trading execution: none. Formal Kline impact:
none; callers pass immutable read-only Kline rows and this module never writes
them or requests fresh market data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping

from app.core.time_utils import timestamp_ms_to_utc_datetime


@dataclass(frozen=True)
class SwingPoint:
    """One local swing high/low found inside a historical Kline window."""

    kind: str
    open_time_ms: int
    open_time_utc: str
    price: Decimal
    source_interval: str

    def as_dict(self, *, latest_close: Decimal | None = None) -> dict[str, object]:
        """Return a JSON-ready representation without trading instructions."""

        payload: dict[str, object] = {
            "kind": self.kind,
            "open_time_ms": self.open_time_ms,
            "open_time_utc": self.open_time_utc,
            "price": _decimal_to_float(self.price),
            "source_interval": self.source_interval,
        }
        if latest_close is not None and latest_close > 0:
            distance = ((self.price - latest_close) / latest_close) * Decimal("100")
            payload["distance_to_latest_close_percent"] = _decimal_to_float(_quantize(distance))
        return payload


@dataclass(frozen=True)
class StructureResult:
    """Swing-based structure labels and coarse market state."""

    recent_swing_highs: tuple[SwingPoint, ...]
    recent_swing_lows: tuple[SwingPoint, ...]
    structure_labels: tuple[str, ...]
    structure_state: str


@dataclass(frozen=True)
class VolatilityResult:
    """ATR and range-expansion metrics calculated from base Klines."""

    atr_14: Decimal | None
    atr_percent: Decimal | None
    avg_range_percent_3: Decimal | None
    avg_range_percent_6: Decimal | None
    avg_range_percent_20: Decimal | None
    range_expansion_state: str
    volatility_state: str


def build_swing_structure(
    rows: Iterable[Any],
    *,
    interval_value: str,
    left_bars: int = 2,
    right_bars: int = 2,
    recent_limit: int = 5,
) -> StructureResult:
    """Calculate local swing highs/lows and a coarse HH/HL/LH/LL structure.

    Parameters: historical Kline rows ordered by `open_time_ms`, interval text,
    and local-window sizes.
    Return value: `StructureResult` with recent swing points and labels.
    Failure scenarios: malformed Kline rows raise `ValueError`.
    External effects: none; this function never reads future rows outside the
    caller-provided snapshot window.
    """

    ordered_rows = tuple(rows)
    swing_highs: list[SwingPoint] = []
    swing_lows: list[SwingPoint] = []
    if len(ordered_rows) < left_bars + right_bars + 1:
        return StructureResult((), (), ("insufficient_data",), "insufficient_data")

    highs = [_row_decimal(row, "high_price") for row in ordered_rows]
    lows = [_row_decimal(row, "low_price") for row in ordered_rows]
    for index in range(left_bars, len(ordered_rows) - right_bars):
        current_high = highs[index]
        current_low = lows[index]
        left_highs = highs[index - left_bars : index]
        right_highs = highs[index + 1 : index + 1 + right_bars]
        left_lows = lows[index - left_bars : index]
        right_lows = lows[index + 1 : index + 1 + right_bars]
        row = ordered_rows[index]
        if current_high > max(left_highs) and current_high > max(right_highs):
            swing_highs.append(_build_swing_point("swing_high", row, current_high, interval_value))
        if current_low < min(left_lows) and current_low < min(right_lows):
            swing_lows.append(_build_swing_point("swing_low", row, current_low, interval_value))

    recent_highs = tuple(swing_highs[-recent_limit:])
    recent_lows = tuple(swing_lows[-recent_limit:])
    labels, state = _structure_labels_and_state(recent_highs, recent_lows)
    return StructureResult(recent_highs, recent_lows, labels, state)


def calculate_volatility_metrics(rows: Iterable[Any]) -> VolatilityResult:
    """Calculate ATR_14 and recent range averages from base Kline rows.

    Parameters: historical base-interval Kline rows ordered by `open_time_ms`.
    Return value: volatility metrics; nullable fields mean the caller did not
    provide enough closed Klines.
    Failure scenarios: malformed OHLC rows raise `ValueError`.
    External effects: none.
    """

    ordered_rows = tuple(rows)
    latest_close = _latest_close_or_none(ordered_rows)
    atr_14 = _calculate_atr(ordered_rows, period=14)
    atr_percent = None
    if atr_14 is not None and latest_close is not None and latest_close > 0:
        atr_percent = _quantize((atr_14 / latest_close) * Decimal("100"))

    avg3 = _average_range_percent(ordered_rows, 3)
    avg6 = _average_range_percent(ordered_rows, 6)
    avg20 = _average_range_percent(ordered_rows, 20)
    range_state = _range_expansion_state(avg3, avg20)
    volatility_state = _volatility_state(atr_percent, range_state)
    return VolatilityResult(
        atr_14=_quantize(atr_14) if atr_14 is not None else None,
        atr_percent=atr_percent,
        avg_range_percent_3=avg3,
        avg_range_percent_6=avg6,
        avg_range_percent_20=avg20,
        range_expansion_state=range_state,
        volatility_state=volatility_state,
    )


def build_support_resistance_candidates(
    *,
    swing_structure: StructureResult,
    latest_close: Decimal,
    support_limit: int = 5,
    resistance_limit: int = 5,
) -> Mapping[str, object]:
    """Build support/resistance candidates from recent swing lows/highs.

    Parameters: swing structure and latest close. Return value: JSON-ready
    mapping containing candidates only; no candidate is an entry, exit, profit,
    or loss instruction.
    Failure scenarios: none after inputs are already validated.
    External effects: none.
    """

    support_points = tuple(
        sorted(
            swing_structure.recent_swing_lows,
            key=lambda point: (abs(point.price - latest_close), point.open_time_ms),
        )
    )[:support_limit]
    resistance_points = tuple(
        sorted(
            swing_structure.recent_swing_highs,
            key=lambda point: (abs(point.price - latest_close), point.open_time_ms),
        )
    )[:resistance_limit]
    return {
        "support_candidates": [
            {**point.as_dict(latest_close=latest_close), "source": "swing_low"} for point in support_points
        ],
        "resistance_candidates": [
            {**point.as_dict(latest_close=latest_close), "source": "swing_high"} for point in resistance_points
        ],
    }


def kline_summary(row: Any) -> dict[str, object]:
    """Return a compact JSON-safe Kline summary for material packs."""

    open_time_ms = _row_int(row, "open_time_ms")
    high = _row_decimal(row, "high_price")
    low = _row_decimal(row, "low_price")
    close = _row_decimal(row, "close_price")
    return {
        "open_time_ms": open_time_ms,
        "open_time_utc": _format_open_time_utc(row),
        "open": _decimal_to_float(_row_decimal(row, "open_price", fallback=close)),
        "high": _decimal_to_float(high),
        "low": _decimal_to_float(low),
        "close": _decimal_to_float(close),
        "volume": _decimal_to_float(_row_decimal(row, "volume", fallback=Decimal("0"))),
        "range_percent": _decimal_to_float(_range_percent(high=high, low=low, close=close)),
    }


def latest_close_price(rows: Iterable[Any]) -> Decimal:
    """Return latest close from an ordered Kline row sequence."""

    ordered_rows = tuple(rows)
    if not ordered_rows:
        raise ValueError("at least one Kline row is required")
    return _row_decimal(ordered_rows[-1], "close_price")


def max_open_time_ms(rows: Iterable[Any]) -> int | None:
    """Return the maximum open_time_ms inside the provided rows."""

    values = [_row_int(row, "open_time_ms") for row in rows]
    if not values:
        return None
    return max(values)


def open_time_utc_text(open_time_ms: int | None) -> str | None:
    """Format a Binance millisecond open time as UTC ISO text."""

    if open_time_ms is None:
        return None
    return timestamp_ms_to_utc_datetime(int(open_time_ms)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _calculate_atr(rows: tuple[Any, ...], *, period: int) -> Decimal | None:
    if len(rows) < period + 1:
        return None
    true_ranges: list[Decimal] = []
    for index in range(1, len(rows)):
        high = _row_decimal(rows[index], "high_price")
        low = _row_decimal(rows[index], "low_price")
        prev_close = _row_decimal(rows[index - 1], "close_price")
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(true_ranges) < period:
        return None
    recent = true_ranges[-period:]
    return sum(recent, Decimal("0")) / Decimal(period)


def _average_range_percent(rows: tuple[Any, ...], count: int) -> Decimal | None:
    if len(rows) < count:
        return None
    values = [
        _range_percent(
            high=_row_decimal(row, "high_price"),
            low=_row_decimal(row, "low_price"),
            close=_row_decimal(row, "close_price"),
        )
        for row in rows[-count:]
    ]
    return _quantize(sum(values, Decimal("0")) / Decimal(count))


def _range_percent(*, high: Decimal, low: Decimal, close: Decimal) -> Decimal:
    if close <= 0:
        return Decimal("0")
    return _quantize(((high - low) / close) * Decimal("100"))


def _range_expansion_state(avg3: Decimal | None, avg20: Decimal | None) -> str:
    if avg3 is None or avg20 is None or avg20 <= 0:
        return "insufficient_data"
    ratio = avg3 / avg20
    if ratio >= Decimal("1.60"):
        return "extreme"
    if ratio >= Decimal("1.20"):
        return "expanding"
    if ratio <= Decimal("0.80"):
        return "contracting"
    return "normal"


def _volatility_state(atr_percent: Decimal | None, range_state: str) -> str:
    if atr_percent is None:
        return "insufficient_data"
    if atr_percent >= Decimal("6") or range_state == "extreme":
        return "extreme"
    if atr_percent >= Decimal("3") or range_state == "expanding":
        return "expanded"
    if atr_percent <= Decimal("1"):
        return "low"
    return "normal"


def _structure_labels_and_state(
    highs: tuple[SwingPoint, ...],
    lows: tuple[SwingPoint, ...],
) -> tuple[tuple[str, ...], str]:
    labels: list[str] = []
    if len(highs) >= 2:
        labels.append("HH" if highs[-1].price > highs[-2].price else "LH")
    if len(lows) >= 2:
        labels.append("HL" if lows[-1].price > lows[-2].price else "LL")
    if not labels:
        return ("insufficient_data",), "insufficient_data"
    if "HH" in labels and "HL" in labels:
        return tuple(labels), "uptrend"
    if "LH" in labels and "LL" in labels:
        return tuple(labels), "downtrend"
    if len(labels) == 1:
        return tuple(labels), "mixed"
    return tuple(labels), "range"


def _build_swing_point(kind: str, row: Any, price: Decimal, interval_value: str) -> SwingPoint:
    return SwingPoint(
        kind=kind,
        open_time_ms=_row_int(row, "open_time_ms"),
        open_time_utc=_format_open_time_utc(row),
        price=_quantize(price),
        source_interval=interval_value,
    )


def _format_open_time_utc(row: Any) -> str:
    value = getattr(row, "open_time_utc", None)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return open_time_utc_text(_row_int(row, "open_time_ms")) or ""


def _latest_close_or_none(rows: tuple[Any, ...]) -> Decimal | None:
    if not rows:
        return None
    return _row_decimal(rows[-1], "close_price")


def _row_int(row: Any, field_name: str) -> int:
    value = getattr(row, field_name, None)
    if value is None:
        raise ValueError(f"Kline row missing {field_name}")
    return int(value)


def _row_decimal(row: Any, field_name: str, *, fallback: Decimal | None = None) -> Decimal:
    value = getattr(row, field_name, None)
    if value is None:
        if fallback is not None:
            return fallback
        raise ValueError(f"Kline row missing {field_name}")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Kline row field {field_name} must be numeric") from exc


def _quantize(value: Decimal | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(_quantize(value))


__all__ = [
    "StructureResult",
    "SwingPoint",
    "VolatilityResult",
    "build_support_resistance_candidates",
    "build_swing_structure",
    "calculate_volatility_metrics",
    "kline_summary",
    "latest_close_price",
    "max_open_time_ms",
    "open_time_utc_text",
]
