"""Market-direction regime context strategy for stage-23B.

This file belongs to `app/strategy/strategies`. It identifies the broad market
regime and the current phase from snapshot-restored 4h/1d Kline windows.
It is called by `app/strategy/runner.py::StrategyRunner.run_strategies` through
the strategy registry.
It does not request Binance, read or write MySQL, read or write Redis, send
Hermes, call DeepSeek or any large language model, read account or position
state, generate final advice, build trade setups, modify Kline tables, or
perform trading.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from app.strategy.base import BaseStrategy
from app.strategy.common.result_contract import (
    StrategyCommonResult,
    StrategyEvidenceItem,
    StrategyResult,
    StrategyRole,
)
from app.strategy.types import StrategyEvaluationInput, StrategySignalStatus

PRIMARY_REGIMES = frozenset(
    {
        "uptrend",
        "downtrend",
        "range",
        "volatile",
        "mixed",
        "insufficient_data",
        "unknown",
    }
)

REGIME_PHASES = frozenset(
    {
        "trend_continuation",
        "pullback_in_uptrend",
        "countertrend_rebound",
        "range_mid_rotation",
        "range_support_rebound",
        "range_resistance_rejection",
        "breakout_attempt",
        "breakdown_attempt",
        "false_breakout",
        "transition",
        "unknown",
    }
)


class MarketDirectionRegimeStrategy(BaseStrategy):
    """Identify broad market direction and phase as context evidence only."""

    strategy_name = "market_direction_regime"
    strategy_version = "23B-1"
    strategy_role = StrategyRole.CONTEXT.value

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        active_config = dict(config or {})
        self.strategy_version = str(active_config.get("strategy_version", self.strategy_version))
        self.strategy_role = str(active_config.get("strategy_role", self.strategy_role))
        self.provides = tuple(
            str(item)
            for item in active_config.get(
                "provides",
                ("primary_regime", "regime_phase", "market_environment_context"),
            )
        )
        lookback_bars = _mapping(active_config.get("lookback_bars"))
        minimum_required = _mapping(active_config.get("minimum_required_bars"))
        thresholds = _mapping(active_config.get("thresholds"))
        self.base_lookback_bars = int(lookback_bars.get("base", active_config.get("lookback_base_bars", 120)))
        self.higher_lookback_bars = int(lookback_bars.get("higher", active_config.get("lookback_higher_bars", 120)))
        self.minimum_required_base_bars = int(
            minimum_required.get("base", active_config.get("min_required_base_klines", 80))
        )
        self.minimum_required_higher_bars = int(
            minimum_required.get("higher", active_config.get("min_required_higher_klines", 80))
        )
        self.trend_change_threshold = Decimal(str(thresholds.get("trend_change_threshold", "0.035")))
        self.range_change_threshold = Decimal(str(thresholds.get("range_change_threshold", "0.018")))
        self.volatile_range_threshold = Decimal(str(thresholds.get("volatile_range_threshold", "0.090")))
        self.phase_change_threshold = Decimal(str(thresholds.get("phase_change_threshold", "0.018")))

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        """Return broad-regime evidence without producing a trading setup."""

        base_rows = tuple(input_data.base_klines)
        higher_rows = tuple(input_data.higher_klines)
        if len(base_rows) < self.minimum_required_base_bars or len(higher_rows) < self.minimum_required_higher_bars:
            return self._insufficient_data_result(input_data, len(base_rows), len(higher_rows))

        base_window = base_rows[-self.base_lookback_bars :]
        higher_window = higher_rows[-self.higher_lookback_bars :]
        latest_close = _decimal_attr(base_window[-1], "close_price")
        higher_change = _change_ratio(_decimal_attr(higher_window[0], "close_price"), _decimal_attr(higher_window[-1], "close_price"))
        base_recent_change = _change_ratio(
            _decimal_attr(base_window[max(0, len(base_window) - 12)], "close_price"),
            latest_close,
        )
        high_change = _change_ratio(
            max(_decimal_attr(row, "high_price") for row in higher_window[: len(higher_window) // 2]),
            max(_decimal_attr(row, "high_price") for row in higher_window[len(higher_window) // 2 :]),
        )
        low_change = _change_ratio(
            min(_decimal_attr(row, "low_price") for row in higher_window[: len(higher_window) // 2]),
            min(_decimal_attr(row, "low_price") for row in higher_window[len(higher_window) // 2 :]),
        )
        base_range_width = _range_width_ratio(base_window)

        primary_regime = self._classify_primary_regime(higher_change, high_change, low_change, base_range_width)
        regime_phase = self._classify_phase(primary_regime, base_window, base_recent_change, latest_close)
        trend_strength = _clamp_unit(abs(higher_change) * Decimal("8") + abs(high_change + low_change) * Decimal("2"))
        confidence = self._confidence(primary_regime, trend_strength, base_range_width)
        reason_codes = _reason_codes(primary_regime, regime_phase)
        market_bias = _market_bias(primary_regime)
        risk_level = "high" if primary_regime == "volatile" else "medium" if primary_regime in {"mixed", "unknown"} else "low"
        context_summary = _context_summary(primary_regime, regime_phase)
        reason_text = (
            f"市场大方向识别为 {primary_regime}，当前阶段为 {regime_phase}。"
            "该结果只作为环境证据，不是交易建议。"
        )

        common_result = StrategyCommonResult(
            market_bias=market_bias,
            risk_level=risk_level,
            signal_strength=_decimal_text(confidence),
            confidence_score=_decimal_text(confidence),
            reason_codes=reason_codes,
            reason_text=reason_text,
            evidence_items=(
                StrategyEvidenceItem(
                    evidence_type="market_environment_context",
                    direction=market_bias,
                    strength=_decimal_text(confidence),
                    description=context_summary,
                    source=self.strategy_name,
                ),
            ),
            observation_window={
                "base_interval_value": input_data.base_interval_value,
                "higher_interval_value": input_data.higher_interval_value,
                "base_start_open_time_ms": input_data.base_start_open_time_ms,
                "base_end_open_time_ms": input_data.base_end_open_time_ms,
                "higher_start_open_time_ms": input_data.higher_start_open_time_ms,
                "higher_end_open_time_ms": input_data.higher_end_open_time_ms,
            },
            context_summary=context_summary,
            not_trading_advice=True,
        )
        return StrategyResult(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_role=self.strategy_role,
            strategy_status=StrategySignalStatus.SUCCESS.value,
            common_result=common_result,
            strategy_model_material_json={
                "summary": context_summary,
                "provides": self.provides,
                "main_uncertainty": _uncertainty(primary_regime, regime_phase),
            },
            strategy_payload_json={
                "primary_regime": primary_regime,
                "regime_phase": regime_phase,
                "trend_strength": _decimal_text(trend_strength),
                "regime_confidence": _decimal_text(confidence),
                "phase_confidence": _decimal_text(_phase_confidence(regime_phase, confidence)),
                "decision_implication": _decision_implication(primary_regime, regime_phase),
                "higher_close_change_ratio": _decimal_text(higher_change),
                "base_recent_change_ratio": _decimal_text(base_recent_change),
                "base_range_width_ratio": _decimal_text(base_range_width),
                "provides": self.provides,
            },
            trace_id=input_data.trace_id,
        )

    def _insufficient_data_result(
        self,
        input_data: StrategyEvaluationInput,
        actual_base_count: int,
        actual_higher_count: int,
    ) -> StrategyResult:
        """Return an invalid but contract-safe result when input windows are short."""

        reason_text = (
            "市场大方向识别所需 K线数量不足，暂不输出主状态判断。"
            f"基础周期要求 {self.minimum_required_base_bars} 根，实际 {actual_base_count} 根；"
            f"高周期要求 {self.minimum_required_higher_bars} 根，实际 {actual_higher_count} 根。"
        )
        common_result = StrategyCommonResult(
            market_bias="unknown",
            risk_level="unknown",
            signal_strength="0",
            confidence_score="0",
            reason_codes=("insufficient_data",),
            reason_text=reason_text,
            context_summary=reason_text,
            observation_window={
                "base_interval_value": input_data.base_interval_value,
                "higher_interval_value": input_data.higher_interval_value,
                "actual_base_count": actual_base_count,
                "actual_higher_count": actual_higher_count,
            },
            not_trading_advice=True,
        )
        return StrategyResult(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_role=self.strategy_role,
            strategy_status=StrategySignalStatus.INVALID.value,
            common_result=common_result,
            strategy_model_material_json={"summary": reason_text, "provides": self.provides},
            strategy_payload_json={
                "primary_regime": "insufficient_data",
                "regime_phase": "unknown",
                "trend_strength": "0",
                "regime_confidence": "0",
                "phase_confidence": "0",
                "decision_implication": "证据不足，只能等待更多已收盘 K线形成后再判断市场环境。",
                "actual_base_count": actual_base_count,
                "actual_higher_count": actual_higher_count,
                "provides": self.provides,
            },
            trace_id=input_data.trace_id,
        )

    def _classify_primary_regime(
        self,
        higher_change: Decimal,
        high_change: Decimal,
        low_change: Decimal,
        base_range_width: Decimal,
    ) -> str:
        if base_range_width >= self.volatile_range_threshold:
            return "volatile"
        if higher_change >= self.trend_change_threshold and high_change >= Decimal("0") and low_change >= Decimal("0"):
            return "uptrend"
        if higher_change <= -self.trend_change_threshold and high_change <= Decimal("0") and low_change <= Decimal("0"):
            return "downtrend"
        if abs(higher_change) <= self.range_change_threshold:
            return "range"
        return "mixed"

    def _classify_phase(
        self,
        primary_regime: str,
        base_window: tuple[Any, ...],
        base_recent_change: Decimal,
        latest_close: Decimal,
    ) -> str:
        recent_low = min(_decimal_attr(row, "low_price") for row in base_window[-24:])
        recent_high = max(_decimal_attr(row, "high_price") for row in base_window[-24:])
        position = _range_position(latest_close, recent_low, recent_high)
        if primary_regime == "uptrend":
            return "pullback_in_uptrend" if base_recent_change <= -self.phase_change_threshold else "trend_continuation"
        if primary_regime == "downtrend":
            return "countertrend_rebound" if base_recent_change >= self.phase_change_threshold else "trend_continuation"
        if primary_regime == "range":
            if position <= Decimal("0.25"):
                return "range_support_rebound"
            if position >= Decimal("0.75"):
                return "range_resistance_rejection"
            return "range_mid_rotation"
        if primary_regime == "mixed":
            if position >= Decimal("0.90"):
                return "breakout_attempt"
            if position <= Decimal("0.10"):
                return "breakdown_attempt"
            return "transition"
        if primary_regime == "volatile":
            return "transition"
        return "unknown"

    def _confidence(self, primary_regime: str, trend_strength: Decimal, range_width: Decimal) -> Decimal:
        if primary_regime in {"uptrend", "downtrend"}:
            return _clamp_unit(Decimal("0.45") + trend_strength)
        if primary_regime == "range":
            return _clamp_unit(Decimal("0.58") - range_width)
        if primary_regime == "volatile":
            return Decimal("0.55")
        if primary_regime == "mixed":
            return Decimal("0.42")
        return Decimal("0")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _decimal_attr(row: Any, field_name: str) -> Decimal:
    return Decimal(str(getattr(row, field_name)))


def _change_ratio(start: Decimal, end: Decimal) -> Decimal:
    if start == 0:
        return Decimal("0")
    return (end - start) / start


def _range_width_ratio(rows: tuple[Any, ...]) -> Decimal:
    recent_high = max(_decimal_attr(row, "high_price") for row in rows[-24:])
    recent_low = min(_decimal_attr(row, "low_price") for row in rows[-24:])
    latest_close = _decimal_attr(rows[-1], "close_price")
    if latest_close <= 0:
        return Decimal("0")
    return (recent_high - recent_low) / latest_close


def _range_position(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    if high <= low:
        return Decimal("0.5")
    return _clamp_unit((value - low) / (high - low))


def _clamp_unit(value: Decimal) -> Decimal:
    if value < Decimal("0"):
        return Decimal("0")
    if value > Decimal("1"):
        return Decimal("1")
    return value


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _market_bias(primary_regime: str) -> str:
    if primary_regime == "uptrend":
        return "bullish_bias"
    if primary_regime == "downtrend":
        return "bearish_bias"
    if primary_regime == "range":
        return "neutral"
    if primary_regime in {"volatile", "mixed"}:
        return "mixed"
    return "unknown"


def _reason_codes(primary_regime: str, regime_phase: str) -> tuple[str, ...]:
    return ("market_regime_classified", f"primary_regime_{primary_regime}", f"regime_phase_{regime_phase}")


def _context_summary(primary_regime: str, regime_phase: str) -> str:
    return f"大级别主状态为 {primary_regime}，当前阶段为 {regime_phase}。该判断只用于市场环境背景。"


def _uncertainty(primary_regime: str, regime_phase: str) -> str:
    if primary_regime in {"mixed", "volatile"}:
        return "方向结构不够单一，后续需要更多角色证据确认。"
    if regime_phase in {"breakout_attempt", "breakdown_attempt", "transition"}:
        return "当前处于状态切换或尝试阶段，需要等待后续收盘验证。"
    return "当前环境证据相对一致，但仍不是交易建议。"


def _phase_confidence(regime_phase: str, base_confidence: Decimal) -> Decimal:
    if regime_phase in {"transition", "breakout_attempt", "breakdown_attempt"}:
        return _clamp_unit(base_confidence - Decimal("0.12"))
    return base_confidence


def _decision_implication(primary_regime: str, regime_phase: str) -> str:
    if primary_regime == "downtrend" and regime_phase == "countertrend_rebound":
        return "大级别偏空但短期处于反弹修复，后续方向策略需要等待反弹失败或结构确认。"
    if primary_regime == "uptrend" and regime_phase == "pullback_in_uptrend":
        return "大级别偏多但短期处于回调，后续方向策略需要等待回调稳定证据。"
    if primary_regime == "range":
        return "大级别以震荡环境处理，后续需要结合关键价位和风控证据。"
    if primary_regime == "volatile":
        return "波动环境较强，后续需要风控策略进一步确认风险是否可接受。"
    return "仅提供市场环境背景，不能直接转化为交易结构。"


__all__ = ["MarketDirectionRegimeStrategy", "PRIMARY_REGIMES", "REGIME_PHASES"]
