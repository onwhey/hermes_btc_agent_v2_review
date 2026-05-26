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
from app.strategy.common.result_contract import (
    StrategyCommonResult,
    StrategyEvidenceItem,
    StrategyResult,
    StrategyRole,
    StrategyScenarioCandidate,
)
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
        self.strategy_version = str(active_config.get("strategy_version", self.strategy_version))
        self.strategy_role = str(active_config.get("strategy_role", StrategyRole.DIRECTIONAL.value))
        self.provides = tuple(str(item) for item in active_config.get("provides", ("trend_structure", "direction_bias")))
        self.ma_short_period = int(active_config.get("ma_short_period", 20))
        self.ma_mid_period = int(active_config.get("ma_mid_period", 60))
        self.min_required_base_klines = int(active_config.get("min_required_base_klines", 120))

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        """Evaluate trend structure without producing a trade instruction."""

        rows = tuple(input_data.base_klines)
        debug_info = _build_debug_info(self, input_data)
        if len(rows) < self.min_required_base_klines:
            return _build_result_from_signal(StrategySignal(
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
                debug_info=debug_info,
                trace_id=input_data.trace_id,
            ), input_data=input_data)

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

        return _build_result_from_signal(StrategySignal(
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
            debug_info=debug_info,
            trace_id=input_data.trace_id,
        ), input_data=input_data)


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


def _build_debug_info(strategy: TrendStructureStrategy, input_data: StrategyEvaluationInput) -> dict[str, Any]:
    """Return replay metadata for the actual trend strategy configuration."""

    return {
        "snapshot_id": input_data.snapshot_id,
        "base_interval_value": input_data.base_interval_value,
        "higher_interval_value": input_data.higher_interval_value,
        "strategy_boundary": "independent_signal_only",
        "strategy_config": {
            "ma_short_period": strategy.ma_short_period,
            "ma_mid_period": strategy.ma_mid_period,
            "min_required_base_klines": strategy.min_required_base_klines,
        },
    }


def _build_result_from_signal(signal: StrategySignal, *, input_data: StrategyEvaluationInput) -> StrategyResult:
    """Wrap the simple trend signal in the stage-23A result contract."""

    common_result = StrategyCommonResult(
        market_bias=signal.direction_bias.value,
        risk_level=signal.risk_level.value,
        signal_strength=str(signal.signal_strength),
        confidence_score=str(signal.signal_strength),
        reason_codes=tuple(signal.reason_codes),
        reason_text=signal.reason_text,
        scenario_candidates=_scenario_candidates_from_signal(signal),
        evidence_items=_evidence_items_from_signal(signal),
        observation_window={
            "base_interval_value": input_data.base_interval_value,
            "base_start_open_time_ms": input_data.base_start_open_time_ms,
            "base_end_open_time_ms": input_data.base_end_open_time_ms,
        },
        not_trading_advice=True,
    )
    return StrategyResult(
        strategy_name=signal.strategy_name,
        strategy_version=signal.strategy_version,
        strategy_role=StrategyRole.DIRECTIONAL.value,
        strategy_status=signal.strategy_status.value,
        common_result=common_result,
        strategy_model_material_json={"metric_keys": tuple(signal.metrics.keys())},
        strategy_payload_json={"metrics": dict(signal.metrics), "debug": dict(signal.debug_info)},
        trace_id=signal.trace_id,
    )


def _scenario_candidates_from_signal(signal: StrategySignal) -> tuple[StrategyScenarioCandidate, ...]:
    if signal.strategy_status != StrategySignalStatus.SUCCESS:
        return ()
    if signal.direction_bias == DirectionBias.NOT_APPLICABLE:
        return ()
    scenario_type = "observation_only"
    if signal.direction_bias == DirectionBias.BULLISH_BIAS:
        scenario_type = "long_candidate"
    elif signal.direction_bias == DirectionBias.BEARISH_BIAS:
        scenario_type = "short_candidate"
    return (
        StrategyScenarioCandidate(
            scenario_type=scenario_type,
            direction_bias=signal.direction_bias.value,
            activation_condition="Observe whether base-period closes keep confirming the mid moving-average structure.",
            invalidation_condition="Observation weakens if base-period closes stop confirming the same structure.",
            target_observation_zone="Recent base-period swing area and moving-average relation.",
            risk_boundary="Use the recent base-period range only as an observation boundary.",
            observation_period_bars=3,
            supporting_evidence=tuple(signal.reason_codes),
            opposing_evidence=(),
        ),
    )


def _evidence_items_from_signal(signal: StrategySignal) -> tuple[StrategyEvidenceItem, ...]:
    if not signal.reason_text:
        return ()
    return (
        StrategyEvidenceItem(
            evidence_type="trend_structure_observation",
            direction=signal.direction_bias.value,
            strength=str(signal.signal_strength),
            description=signal.reason_text,
            source=signal.strategy_name,
        ),
    )


__all__ = ["TrendStructureStrategy"]
