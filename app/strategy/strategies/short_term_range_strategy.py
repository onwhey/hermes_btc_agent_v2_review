"""Short-term range context strategy for stage-23B.

This file belongs to `app/strategy/strategies`. It identifies the recent
short-term operating range from snapshot-restored base Kline rows.
It is called by `app/strategy/runner.py::StrategyRunner.run_strategies` through
the strategy registry.
It does not implement formal support/resistance, request Binance, read or
write MySQL, read or write Redis, send Hermes, call DeepSeek or any large
language model, read account or position state, generate final advice, build
trade setups, modify Kline tables, or perform trading.
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

RANGE_POSITIONS = frozenset(
    {
        "above_range",
        "upper_edge",
        "upper_half",
        "middle",
        "lower_half",
        "lower_edge",
        "below_range",
        "unknown",
    }
)

RANGE_QUALITIES = frozenset(
    {
        "clear",
        "weak",
        "wide",
        "narrow",
        "noisy",
        "insufficient_data",
        "unknown",
    }
)


class ShortTermRangeStrategy(BaseStrategy):
    """Identify the recent base-period range as context evidence only."""

    strategy_name = "short_term_range"
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
                ("short_term_range", "range_position", "range_quality"),
            )
        )
        lookback_bars = _mapping(active_config.get("lookback_bars"))
        minimum_required = _mapping(active_config.get("minimum_required_bars"))
        thresholds = _mapping(active_config.get("thresholds"))
        self.base_lookback_bars = int(lookback_bars.get("base", active_config.get("lookback_period", 48)))
        self.minimum_required_base_bars = int(
            minimum_required.get("base", active_config.get("min_required_base_klines", 36))
        )
        self.edge_zone_ratio = Decimal(str(thresholds.get("edge_zone_ratio", "0.15")))
        self.middle_band_ratio = Decimal(str(thresholds.get("middle_band_ratio", "0.10")))
        self.wide_range_pct = Decimal(str(thresholds.get("wide_range_pct", "8.0")))
        self.narrow_range_pct = Decimal(str(thresholds.get("narrow_range_pct", "1.2")))
        self.noisy_crossing_min = int(thresholds.get("noisy_crossing_min", 8))

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        """Return short-term range evidence without formal key-level claims."""

        rows = tuple(input_data.base_klines)
        if len(rows) < self.minimum_required_base_bars:
            return self._insufficient_data_result(input_data, len(rows))

        window = rows[-self.base_lookback_bars :]
        latest_close = _decimal_attr(window[-1], "close_price")
        recent_range_high = max(_decimal_attr(row, "high_price") for row in window)
        recent_range_low = min(_decimal_attr(row, "low_price") for row in window)
        range_mid = (recent_range_high + recent_range_low) / Decimal("2")
        width_pct = _range_width_pct(recent_range_high, recent_range_low, range_mid)
        position_ratio = _range_position_ratio(latest_close, recent_range_low, recent_range_high)
        range_position = self._range_position(latest_close, recent_range_low, recent_range_high, position_ratio)
        range_quality = self._range_quality(window, width_pct, range_mid)
        confidence = self._confidence(range_quality)
        risk_level = "medium" if range_quality in {"wide", "noisy", "weak"} else "low"
        context_summary = (
            f"短期运行区间约为 { _price_text(recent_range_low) } 到 { _price_text(recent_range_high) }，"
            f"当前位置为 {range_position}，区间质量为 {range_quality}。"
        )
        reason_text = f"{context_summary}该结果只是短期区间证据，不是正式支撑压力或交易建议。"

        common_result = StrategyCommonResult(
            market_bias="neutral",
            risk_level=risk_level,
            signal_strength=_decimal_text(confidence),
            confidence_score=_decimal_text(confidence),
            reason_codes=("short_term_range_identified", f"range_position_{range_position}", f"range_quality_{range_quality}"),
            reason_text=reason_text,
            evidence_items=(
                StrategyEvidenceItem(
                    evidence_type="short_term_range_context",
                    direction="neutral",
                    strength=_decimal_text(confidence),
                    description=context_summary,
                    source=self.strategy_name,
                ),
            ),
            observation_window={
                "base_interval_value": input_data.base_interval_value,
                "base_start_open_time_ms": input_data.base_start_open_time_ms,
                "base_end_open_time_ms": input_data.base_end_open_time_ms,
                "lookback_bars": len(window),
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
                "range_warning": _range_warning(range_quality),
            },
            strategy_payload_json={
                "recent_range_high": _price_text(recent_range_high),
                "recent_range_low": _price_text(recent_range_low),
                "range_mid": _price_text(range_mid),
                "range_width_pct": _decimal_text(width_pct),
                "range_position": range_position,
                "range_quality": range_quality,
                "range_basis": "recent_4h_swing_window",
                "latest_close": _price_text(latest_close),
                "position_ratio": _decimal_text(position_ratio),
                "provides": self.provides,
            },
            trace_id=input_data.trace_id,
        )

    def _insufficient_data_result(self, input_data: StrategyEvaluationInput, actual_base_count: int) -> StrategyResult:
        """Return an invalid but contract-safe result when the range window is short."""

        reason_text = (
            "短期区间识别所需基础周期 K线数量不足，暂不输出区间高低点。"
            f"要求 {self.minimum_required_base_bars} 根，实际 {actual_base_count} 根。"
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
                "actual_base_count": actual_base_count,
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
                "recent_range_high": None,
                "recent_range_low": None,
                "range_mid": None,
                "range_width_pct": None,
                "range_position": "unknown",
                "range_quality": "insufficient_data",
                "range_basis": "recent_4h_swing_window",
                "actual_base_count": actual_base_count,
                "provides": self.provides,
            },
            trace_id=input_data.trace_id,
        )

    def _range_position(self, latest_close: Decimal, low: Decimal, high: Decimal, ratio: Decimal) -> str:
        if latest_close > high:
            return "above_range"
        if latest_close < low:
            return "below_range"
        if ratio <= self.edge_zone_ratio:
            return "lower_edge"
        if ratio >= Decimal("1") - self.edge_zone_ratio:
            return "upper_edge"
        middle_low = Decimal("0.5") - self.middle_band_ratio
        middle_high = Decimal("0.5") + self.middle_band_ratio
        if middle_low <= ratio <= middle_high:
            return "middle"
        if ratio > Decimal("0.5"):
            return "upper_half"
        return "lower_half"

    def _range_quality(self, window: tuple[Any, ...], width_pct: Decimal, range_mid: Decimal) -> str:
        if width_pct >= self.wide_range_pct:
            return "wide"
        if width_pct <= self.narrow_range_pct:
            return "narrow"
        crossings = _mid_crossings(window, range_mid)
        if crossings >= self.noisy_crossing_min:
            return "noisy"
        touches_high, touches_low = _edge_touch_counts(window)
        if touches_high >= 2 and touches_low >= 2:
            return "clear"
        return "weak"

    @staticmethod
    def _confidence(range_quality: str) -> Decimal:
        if range_quality == "clear":
            return Decimal("0.72")
        if range_quality == "weak":
            return Decimal("0.46")
        if range_quality in {"wide", "narrow", "noisy"}:
            return Decimal("0.52")
        return Decimal("0")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _decimal_attr(row: Any, field_name: str) -> Decimal:
    return Decimal(str(getattr(row, field_name)))


def _range_width_pct(high: Decimal, low: Decimal, mid: Decimal) -> Decimal:
    if mid <= 0:
        return Decimal("0")
    return ((high - low) / mid) * Decimal("100")


def _range_position_ratio(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    if high <= low:
        return Decimal("0.5")
    ratio = (value - low) / (high - low)
    if ratio < Decimal("0"):
        return Decimal("0")
    if ratio > Decimal("1"):
        return Decimal("1")
    return ratio


def _mid_crossings(window: tuple[Any, ...], range_mid: Decimal) -> int:
    closes = [_decimal_attr(row, "close_price") for row in window]
    if len(closes) < 2:
        return 0
    crossings = 0
    previous_side = closes[0] >= range_mid
    for close in closes[1:]:
        current_side = close >= range_mid
        if current_side != previous_side:
            crossings += 1
        previous_side = current_side
    return crossings


def _edge_touch_counts(window: tuple[Any, ...]) -> tuple[int, int]:
    high = max(_decimal_attr(row, "high_price") for row in window)
    low = min(_decimal_attr(row, "low_price") for row in window)
    width = high - low
    if width <= 0:
        return 0, 0
    edge_band = width * Decimal("0.12")
    high_touches = sum(1 for row in window if high - _decimal_attr(row, "high_price") <= edge_band)
    low_touches = sum(1 for row in window if _decimal_attr(row, "low_price") - low <= edge_band)
    return high_touches, low_touches


def _range_warning(range_quality: str) -> str:
    if range_quality == "wide":
        return "区间偏宽，后续仍需要更细的支撑压力策略确认关键位。"
    if range_quality == "narrow":
        return "区间偏窄，容易被噪声穿越。"
    if range_quality == "noisy":
        return "区间内来回穿越较多，区间参考价值下降。"
    if range_quality == "weak":
        return "区间触碰证据偏少，暂按弱区间处理。"
    return "区间相对清晰，但仍不是正式支撑压力判断。"


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _price_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


__all__ = ["ShortTermRangeStrategy", "RANGE_POSITIONS", "RANGE_QUALITIES"]
