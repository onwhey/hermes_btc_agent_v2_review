"""Trend-structure strategy for stage-16 independent signals.

This file belongs to `app/strategy/strategies`. It calculates a basic trend
structure signal from `StrategyEvaluationInput.base_klines`.
It does not query Klines, request Binance, write MySQL, write Redis, send
Hermes, call DeepSeek or any large language model, read account/position state,
generate final trading advice, or perform trading.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from app.strategy.base import BaseStrategy
from app.strategy.types import (
    DirectionBias,
    RiskLevel,
    StrategyEvaluationInput,
    StrategySignal,
    StrategySignalStatus,
)


class TrendStructureStrategy(BaseStrategy):
    """Emit a simple trend-structure signal from the base Kline window."""

    strategy_name = "trend_structure"
    strategy_version = "v1"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        active_config = dict(config or {})
        self.ma_short_period = int(active_config.get("ma_short_period", 20))
        self.ma_mid_period = int(active_config.get("ma_mid_period", 60))
        self.min_required_base_klines = int(active_config.get("min_required_base_klines", 120))

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategySignal:
        """Evaluate trend structure without producing a trade instruction."""

        rows = tuple(input_data.base_klines)
        if len(rows) < self.min_required_base_klines:
            return StrategySignal(
                strategy_name=self.strategy_name,
                strategy_version=self.strategy_version,
                strategy_status=StrategySignalStatus.INVALID,
                direction_bias=DirectionBias.UNKNOWN,
                risk_level=RiskLevel.UNKNOWN,
                signal_strength=0.0,
                reason_codes=("insufficient_base_klines",),
                reason_text=(
                    "基础周期 K线数量不足，趋势结构策略不输出倾向。"
                    f"要求 {self.min_required_base_klines} 根，实际 {len(rows)} 根。"
                ),
                metrics={"actual_base_count": len(rows), "required_base_count": self.min_required_base_klines},
                trace_id=input_data.trace_id,
            )

        closes = [_decimal_attr(row, "close_price") for row in rows]
        lows = [_decimal_attr(row, "low_price") for row in rows]
        highs = [_decimal_attr(row, "high_price") for row in rows]
        latest_close = closes[-1]
        short_ma = _average(closes[-self.ma_short_period :])
        mid_ma = _average(closes[-self.ma_mid_period :])
        recent_position = _range_position(
            latest_close,
            min(lows[-self.ma_mid_period :]),
            max(highs[-self.ma_mid_period :]),
        )
        higher_low = len(lows) >= 6 and min(lows[-3:]) > min(lows[-6:-3])
        lower_high = len(highs) >= 6 and max(highs[-3:]) < max(highs[-6:-3])

        reason_codes: list[str] = []
        direction = DirectionBias.NEUTRAL
        strength = 0.45
        risk_level = RiskLevel.MEDIUM

        if latest_close > mid_ma and short_ma >= mid_ma:
            reason_codes.append("close_above_mid_ma")
            strength += 0.18
            direction = DirectionBias.BULLISH_BIAS
        elif latest_close < mid_ma and short_ma <= mid_ma:
            reason_codes.append("close_below_mid_ma")
            strength += 0.18
            direction = DirectionBias.BEARISH_BIAS
        else:
            reason_codes.append("close_near_mid_ma")

        if higher_low:
            reason_codes.append("higher_low_structure")
            if direction == DirectionBias.BEARISH_BIAS:
                direction = DirectionBias.MIXED
            elif direction == DirectionBias.NEUTRAL:
                direction = DirectionBias.BULLISH_BIAS
            strength += 0.12
        if lower_high:
            reason_codes.append("lower_high_structure")
            if direction == DirectionBias.BULLISH_BIAS:
                direction = DirectionBias.MIXED
            elif direction == DirectionBias.NEUTRAL:
                direction = DirectionBias.BEARISH_BIAS
            strength += 0.12

        if recent_position >= Decimal("0.8"):
            reason_codes.append("near_recent_range_high")
            risk_level = RiskLevel.MEDIUM
        elif recent_position <= Decimal("0.2"):
            reason_codes.append("near_recent_range_low")
            risk_level = RiskLevel.MEDIUM

        if direction == DirectionBias.NEUTRAL:
            status = StrategySignalStatus.NO_SIGNAL
            reason_text = "基础周期收盘价接近中期均线，近期高低点结构没有形成清晰倾向。"
            strength = 0.2
        elif direction == DirectionBias.MIXED:
            status = StrategySignalStatus.SUCCESS
            reason_text = "基础周期均线位置与近期高低点结构存在分歧，趋势结构呈混合状态。"
            strength = min(strength, 0.68)
        elif direction == DirectionBias.BULLISH_BIAS:
            status = StrategySignalStatus.SUCCESS
            reason_text = "最近收盘价位于中期均线上方，且低点结构未继续下移，趋势结构偏多。"
        else:
            status = StrategySignalStatus.SUCCESS
            reason_text = "最近收盘价位于中期均线下方，且高点结构未继续上移，趋势结构偏空。"

        return StrategySignal(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_status=status,
            direction_bias=direction,
            risk_level=risk_level,
            signal_strength=float(min(max(strength, 0.0), 1.0)),
            reason_codes=tuple(reason_codes),
            reason_text=reason_text,
            metrics={
                "latest_close": str(latest_close),
                "ma_short": str(short_ma),
                "ma_mid": str(mid_ma),
                "recent_range_position": str(recent_position),
                "base_kline_count": len(rows),
            },
            debug_info={
                "snapshot_id": input_data.snapshot_id,
                "base_interval_value": input_data.base_interval_value,
                "strategy_boundary": "independent_signal_only",
            },
            trace_id=input_data.trace_id,
        )


def _decimal_attr(row: Any, field_name: str) -> Decimal:
    return Decimal(str(getattr(row, field_name)))


def _average(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _range_position(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    if high <= low:
        return Decimal("0.5")
    return (value - low) / (high - low)


__all__ = ["TrendStructureStrategy"]

