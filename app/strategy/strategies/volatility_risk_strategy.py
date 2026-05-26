"""Volatility-risk strategy for stage-16 independent signals.

This file belongs to `app/strategy/strategies`. It identifies whether recent
base-period ranges show elevated volatility risk.
It does not generate final advice, output stop-trading instructions, query
Klines, request Binance, write databases, send Hermes, call large language
models, read account/position state, or trade.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from app.strategy.base import BaseStrategy
from app.strategy.common.result_contract import (
    StrategyCommonResult,
    StrategyEvidenceItem,
    StrategyResult,
    StrategyRiskFlag,
    StrategyRole,
)
from app.strategy.types import (
    DirectionBias,
    RiskLevel,
    StrategyEvaluationInput,
    StrategySignal,
    StrategySignalStatus,
)


class VolatilityRiskStrategy(BaseStrategy):
    """Emit a volatility risk signal from recent base Kline ranges."""

    strategy_name = "volatility_risk"
    strategy_version = "v1"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        active_config = dict(config or {})
        self.lookback_period = int(active_config.get("lookback_period", 30))
        self.min_required_base_klines = int(active_config.get("min_required_base_klines", self.lookback_period))
        self.high_volatility_percentile = Decimal(str(active_config.get("high_volatility_percentile", "0.80")))
        self.extreme_volatility_percentile = Decimal(str(active_config.get("extreme_volatility_percentile", "0.95")))

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        """Evaluate recent volatility risk without producing an action instruction."""

        rows = tuple(input_data.base_klines)
        debug_info = _build_debug_info(self, input_data)
        if len(rows) < self.min_required_base_klines:
            return _build_result_from_signal(StrategySignal(
                strategy_name=self.strategy_name,
                strategy_version=self.strategy_version,
                strategy_status=StrategySignalStatus.INVALID,
                direction_bias=DirectionBias.NOT_APPLICABLE,
                risk_level=RiskLevel.UNKNOWN,
                signal_strength=0.0,
                reason_codes=("insufficient_base_klines",),
                reason_text=(
                    "基础周期 K线数量不足，波动风险策略不输出风险分级。"
                    f"要求 {self.min_required_base_klines} 根，实际 {len(rows)} 根。"
                ),
                metrics={"actual_base_count": len(rows), "required_base_count": self.min_required_base_klines},
                debug_info=debug_info,
                trace_id=input_data.trace_id,
            ), input_data=input_data)

        recent_rows = rows[-self.lookback_period :]
        ranges = tuple(_range_ratio(row) for row in recent_rows)
        latest_range = ranges[-1]
        percentile_rank = _percentile_rank(ranges, latest_range)
        average_range = sum(ranges, Decimal("0")) / Decimal(len(ranges))

        reason_codes = ["volatility_measured"]
        risk_level = RiskLevel.LOW
        strength = 0.25
        reason_text = "近期基础周期波动处于较低或正常区间，暂未识别到明显波动放大。"

        if percentile_rank >= self.extreme_volatility_percentile:
            risk_level = RiskLevel.EXTREME
            strength = 0.9
            reason_codes.extend(["range_expansion", "recent_volatility_extreme"])
            reason_text = "近期 K线波动区间显著扩大，波动风险处于极高水平。"
        elif percentile_rank >= self.high_volatility_percentile:
            risk_level = RiskLevel.HIGH
            strength = 0.7
            reason_codes.extend(["range_expansion", "recent_volatility_elevated"])
            reason_text = "近期 K线波动区间明显扩大，波动风险升高。"
        elif latest_range > average_range:
            risk_level = RiskLevel.MEDIUM
            strength = 0.45
            reason_codes.append("latest_range_above_average")
            reason_text = "最新基础周期波动高于近期平均水平，风险需要关注。"

        return _build_result_from_signal(StrategySignal(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_status=StrategySignalStatus.SUCCESS,
            direction_bias=DirectionBias.NOT_APPLICABLE,
            risk_level=risk_level,
            signal_strength=float(strength),
            reason_codes=tuple(reason_codes),
            reason_text=reason_text,
            metrics={
                "latest_range_ratio": str(latest_range),
                "average_range_ratio": str(average_range),
                "percentile_rank": str(percentile_rank),
                "lookback_period": self.lookback_period,
            },
            debug_info=debug_info,
            trace_id=input_data.trace_id,
        ), input_data=input_data)


def _decimal_attr(row: Any, field_name: str) -> Decimal:
    return Decimal(str(getattr(row, field_name)))


def _range_ratio(row: Any) -> Decimal:
    close_price = _decimal_attr(row, "close_price")
    if close_price <= 0:
        return Decimal("0")
    return (_decimal_attr(row, "high_price") - _decimal_attr(row, "low_price")) / close_price


def _percentile_rank(values: tuple[Decimal, ...], value: Decimal) -> Decimal:
    if not values:
        return Decimal("0")
    less_or_equal = sum(1 for item in values if item <= value)
    return Decimal(less_or_equal) / Decimal(len(values))


def _build_debug_info(strategy: VolatilityRiskStrategy, input_data: StrategyEvaluationInput) -> dict[str, Any]:
    """Return replay metadata for the actual volatility strategy configuration."""

    return {
        "snapshot_id": input_data.snapshot_id,
        "base_interval_value": input_data.base_interval_value,
        "higher_interval_value": input_data.higher_interval_value,
        "strategy_boundary": "risk_signal_only",
        "strategy_config": {
            "lookback_period": strategy.lookback_period,
            "min_required_base_klines": strategy.min_required_base_klines,
            "high_volatility_percentile": str(strategy.high_volatility_percentile),
            "extreme_volatility_percentile": str(strategy.extreme_volatility_percentile),
        },
    }


def _build_result_from_signal(signal: StrategySignal, *, input_data: StrategyEvaluationInput) -> StrategyResult:
    """Wrap the simple volatility signal in the stage-23A result contract."""

    common_result = StrategyCommonResult(
        market_bias=signal.direction_bias.value,
        risk_level=signal.risk_level.value,
        signal_strength=str(signal.signal_strength),
        confidence_score=str(signal.signal_strength),
        reason_codes=tuple(signal.reason_codes),
        reason_text=signal.reason_text,
        risk_flags=_risk_flags_from_signal(signal),
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
        strategy_role=StrategyRole.RISK_CONTROL.value,
        strategy_status=signal.strategy_status.value,
        common_result=common_result,
        strategy_model_material_json={"metric_keys": tuple(signal.metrics.keys())},
        strategy_payload_json={"metrics": dict(signal.metrics), "debug": dict(signal.debug_info)},
        trace_id=signal.trace_id,
    )


def _risk_flags_from_signal(signal: StrategySignal) -> tuple[StrategyRiskFlag, ...]:
    if signal.strategy_status != StrategySignalStatus.SUCCESS:
        return ()
    return (
        StrategyRiskFlag(
            risk_type="volatility_observation",
            risk_level=signal.risk_level.value,
            triggered=signal.risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.EXTREME},
            reason=signal.reason_text,
            source=signal.strategy_name,
        ),
    )


def _evidence_items_from_signal(signal: StrategySignal) -> tuple[StrategyEvidenceItem, ...]:
    if not signal.reason_text:
        return ()
    return (
        StrategyEvidenceItem(
            evidence_type="volatility_risk_observation",
            direction=signal.direction_bias.value,
            strength=str(signal.signal_strength),
            description=signal.reason_text,
            source=signal.strategy_name,
        ),
    )


__all__ = ["VolatilityRiskStrategy"]
