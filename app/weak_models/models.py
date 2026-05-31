"""Initial 27A rule-based weak models.

本文件属于 `app/weak_models` 模块，负责首批四个规则型弱模型：
trend_strength_directional、volatility_risk_gate、support_distance_confirmation、
market_regime_context。
本文件只读取 `WeakModelEvaluationInput` 中已还原的正式 K线窗口，不查询数据库，
不请求 Binance，不发送 Hermes，不调用 DeepSeek/GPT/Claude，不读取账户或仓位，
不生成订单，不自动交易。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.weak_models.base import BaseWeakModel
from app.weak_models.types import WeakModelEvaluationInput, WeakModelOutput, WeakModelResultStatus, WeakModelRole


class TrendStrengthDirectionalModel(BaseWeakModel):
    """Directional weak model based on moving-average relation and slope."""

    def evaluate(self, input_data: WeakModelEvaluationInput) -> WeakModelOutput:
        rows = tuple(input_data.base_klines)
        params = self.profile.params
        fast = int(params.get("ma_fast", 20))
        slow = int(params.get("ma_slow", 60))
        slope_window = int(params.get("slope_window", 10))
        min_count = max(fast, slow, slope_window + slow)
        if len(rows) < min_count:
            return _failed_output(self, "insufficient_base_klines", {"actual_base_count": len(rows), "required": min_count})

        closes = [_decimal_attr(row, "close_price") for row in rows]
        latest_close = closes[-1]
        fast_ma = _average(closes[-fast:])
        slow_ma = _average(closes[-slow:])
        previous_slow_ma = _average(closes[-slow - slope_window : -slope_window])
        slope = _safe_ratio(slow_ma - previous_slow_ma, previous_slow_ma)
        ma_distance = _safe_ratio(fast_ma - slow_ma, slow_ma)
        signal_score = 0.0
        direction = "neutral"
        if latest_close > slow_ma and fast_ma > slow_ma and slope > Decimal("0.002"):
            signal_score = 0.50
            direction = "bullish"
            if ma_distance > Decimal("0.015") and slope > Decimal("0.006"):
                signal_score = 0.75
        elif latest_close < slow_ma and fast_ma < slow_ma and slope < Decimal("-0.002"):
            signal_score = -0.50
            direction = "bearish"
            if ma_distance < Decimal("-0.015") and slope < Decimal("-0.006"):
                signal_score = -0.75
        elif latest_close > slow_ma:
            signal_score = 0.25
            direction = "bullish"
        elif latest_close < slow_ma:
            signal_score = -0.25
            direction = "bearish"
        confidence = _clamp(0.50 + min(abs(float(ma_distance)) * 8, 0.20) + min(abs(float(slope)) * 16, 0.10), 0.30, 0.80)
        effective_score = signal_score * confidence * self.profile.static_weight
        return WeakModelOutput(
            model_key=self.profile.model_key,
            model_role=WeakModelRole.DIRECTIONAL.value,
            status=WeakModelResultStatus.SUCCESS,
            signal_score=signal_score,
            direction_bias=direction,
            confidence=confidence,
            static_weight=self.profile.static_weight,
            effective_score=effective_score,
            input_summary=_input_summary(input_data),
            evidence={
                "latest_close": str(latest_close),
                "fast_ma": str(fast_ma),
                "slow_ma": str(slow_ma),
                "slow_ma_slope_ratio": str(slope),
                "ma_distance_ratio": str(ma_distance),
            },
            raw_output={"signal_score": signal_score, "direction_bias": direction, "not_trading_advice": True},
        )


class VolatilityRiskGateModel(BaseWeakModel):
    """Risk weak model based on ATR percentage and recent range expansion."""

    def evaluate(self, input_data: WeakModelEvaluationInput) -> WeakModelOutput:
        rows = tuple(input_data.base_klines)
        params = self.profile.params
        atr_period = int(params.get("atr_period", 14))
        can_veto = bool(params.get("can_veto", True))
        min_count = atr_period + 1
        if len(rows) < min_count:
            return _failed_output(self, "insufficient_base_klines", {"actual_base_count": len(rows), "required": min_count})

        latest_close = _decimal_attr(rows[-1], "close_price")
        true_ranges = _true_ranges(rows[-min_count:])
        atr_pct = _safe_ratio(_average(true_ranges), latest_close)
        ranges = tuple(_range_pct(row) for row in rows[-atr_period:])
        latest_range_pct = ranges[-1] if ranges else Decimal("0")
        avg_range_pct = _average(list(ranges))
        expansion = _safe_ratio(latest_range_pct, avg_range_pct)
        risk_score = _risk_score(float(atr_pct), float(expansion), float(latest_range_pct))
        risk_level = _risk_level(risk_score)
        veto_triggered = can_veto and risk_score >= 0.80
        permission = "block" if veto_triggered else "caution" if risk_level == "high" else "allow"
        confidence = 0.80 if risk_level in {"high", "extreme"} else 0.60 if risk_level == "medium" else 0.50
        return WeakModelOutput(
            model_key=self.profile.model_key,
            model_role=WeakModelRole.RISK.value,
            status=WeakModelResultStatus.SUCCESS,
            risk_score=risk_score,
            risk_level=risk_level,
            can_veto=can_veto,
            veto_triggered=veto_triggered,
            trade_permission=permission,
            confidence=confidence,
            static_weight=self.profile.static_weight,
            effective_score=0.0,
            input_summary=_input_summary(input_data),
            evidence={
                "atr_pct": str(atr_pct),
                "latest_range_pct": str(latest_range_pct),
                "average_range_pct": str(avg_range_pct),
                "range_expansion_ratio": str(expansion),
            },
            raw_output={
                "risk_score": risk_score,
                "risk_level": risk_level,
                "trade_permission": permission,
                "not_trading_advice": True,
            },
        )


class SupportDistanceConfirmationModel(BaseWeakModel):
    """Confirmation weak model based on distance to recent support/resistance."""

    def evaluate(self, input_data: WeakModelEvaluationInput) -> WeakModelOutput:
        rows = tuple(input_data.base_klines)
        params = self.profile.params
        lookback = int(params.get("lookback", 48))
        near_pct = Decimal(str(params.get("near_level_pct", "0.012")))
        if len(rows) < max(12, lookback // 2):
            return _failed_output(self, "insufficient_base_klines", {"actual_base_count": len(rows), "required": max(12, lookback // 2)})

        window = rows[-lookback:]
        latest_close = _decimal_attr(window[-1], "close_price")
        support = min(_decimal_attr(row, "low_price") for row in window)
        resistance = max(_decimal_attr(row, "high_price") for row in window)
        support_distance = _safe_ratio(latest_close - support, latest_close)
        resistance_distance = _safe_ratio(resistance - latest_close, latest_close)
        supports = "neutral"
        confirmation = 0.35
        if support_distance <= near_pct and resistance_distance > near_pct:
            supports = "long"
            confirmation = 0.70
        elif resistance_distance <= near_pct and support_distance > near_pct:
            supports = "short"
            confirmation = 0.70
        elif support_distance <= near_pct and resistance_distance <= near_pct:
            supports = "none"
            confirmation = 0.25
        confidence = 0.65 if supports in {"long", "short"} else 0.45
        return WeakModelOutput(
            model_key=self.profile.model_key,
            model_role=WeakModelRole.CONFIRMATION.value,
            status=WeakModelResultStatus.SUCCESS,
            supports_direction=supports,
            confirmation_score=confirmation,
            confidence=confidence,
            static_weight=self.profile.static_weight,
            effective_score=0.0,
            input_summary=_input_summary(input_data),
            evidence={
                "nearest_window_support": str(support),
                "nearest_window_resistance": str(resistance),
                "support_distance_pct": str(support_distance),
                "resistance_distance_pct": str(resistance_distance),
            },
            raw_output={
                "supports_direction": supports,
                "confirmation_score": confirmation,
                "not_trading_advice": True,
            },
        )


class MarketRegimeContextModel(BaseWeakModel):
    """Context weak model classifying trend/range/volatility regime."""

    def evaluate(self, input_data: WeakModelEvaluationInput) -> WeakModelOutput:
        base_rows = tuple(input_data.base_klines)
        higher_rows = tuple(input_data.higher_klines)
        params = self.profile.params
        lookback = int(params.get("lookback", 60))
        if len(base_rows) < 24 or len(higher_rows) < max(10, lookback // 2):
            return _failed_output(self, "insufficient_klines", {"actual_base_count": len(base_rows), "actual_higher_count": len(higher_rows)})

        base_window = base_rows[-lookback:]
        higher_window = higher_rows[-lookback:] if len(higher_rows) >= lookback else higher_rows
        latest_close = _decimal_attr(base_window[-1], "close_price")
        base_change = _change_ratio(_decimal_attr(base_window[0], "close_price"), latest_close)
        higher_change = _change_ratio(_decimal_attr(higher_window[0], "close_price"), _decimal_attr(higher_window[-1], "close_price"))
        range_width = _safe_ratio(
            max(_decimal_attr(row, "high_price") for row in base_window) - min(_decimal_attr(row, "low_price") for row in base_window),
            latest_close,
        )
        if range_width >= Decimal("0.12"):
            regime = "high_volatility"
        elif range_width <= Decimal("0.035"):
            regime = "low_volatility"
        elif abs(higher_change) >= Decimal("0.04") and abs(base_change) >= Decimal("0.015"):
            regime = "trend"
        elif abs(higher_change) <= Decimal("0.018"):
            regime = "range"
        else:
            regime = "transition"
        context_score = _clamp(float(abs(higher_change)) * 6 + float(range_width), 0.20, 0.80)
        confidence = 0.70 if regime in {"trend", "range"} else 0.55
        return WeakModelOutput(
            model_key=self.profile.model_key,
            model_role=WeakModelRole.CONTEXT.value,
            status=WeakModelResultStatus.SUCCESS,
            context_regime=regime,
            context_score=context_score,
            confidence=confidence,
            static_weight=self.profile.static_weight,
            effective_score=0.0,
            input_summary=_input_summary(input_data),
            evidence={
                "base_change_ratio": str(base_change),
                "higher_change_ratio": str(higher_change),
                "base_range_width_ratio": str(range_width),
            },
            raw_output={"regime": regime, "context_score": context_score, "not_trading_advice": True},
        )


def _failed_output(model: BaseWeakModel, error_code: str, evidence: dict[str, Any]) -> WeakModelOutput:
    return WeakModelOutput(
        model_key=model.profile.model_key,
        model_role=model.profile.model_role,
        status=WeakModelResultStatus.FAILED,
        error_code=error_code,
        error_message=error_code,
        confidence=0.0,
        static_weight=model.profile.static_weight,
        input_summary={},
        evidence=evidence,
        raw_output={"not_trading_advice": True, "error_code": error_code},
    )


def _input_summary(input_data: WeakModelEvaluationInput) -> dict[str, Any]:
    return {
        "snapshot_id": input_data.snapshot_id,
        "strategy_signal_run_id": input_data.strategy_signal_run_id,
        "kline_slot_utc": input_data.kline_slot_utc.isoformat(),
        "base_kline_count": len(input_data.base_klines),
        "higher_kline_count": len(input_data.higher_klines),
    }


def _true_ranges(rows: tuple[Any, ...]) -> list[Decimal]:
    result: list[Decimal] = []
    for index, row in enumerate(rows):
        high = _decimal_attr(row, "high_price")
        low = _decimal_attr(row, "low_price")
        previous_close = _decimal_attr(rows[index - 1], "close_price") if index > 0 else _decimal_attr(row, "close_price")
        result.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return result


def _risk_score(atr_pct: float, expansion: float, latest_range_pct: float) -> float:
    atr_component = min(atr_pct / 0.08, 1.0) * 0.55
    expansion_component = min(max(expansion - 1.0, 0.0) / 2.0, 1.0) * 0.30
    range_component = min(latest_range_pct / 0.10, 1.0) * 0.15
    return _clamp(atr_component + expansion_component + range_component, 0.0, 1.0)


def _risk_level(risk_score: float) -> str:
    if risk_score < 0.35:
        return "low"
    if risk_score < 0.60:
        return "medium"
    if risk_score < 0.80:
        return "high"
    return "extreme"


def _range_pct(row: Any) -> Decimal:
    return _safe_ratio(_decimal_attr(row, "high_price") - _decimal_attr(row, "low_price"), _decimal_attr(row, "close_price"))


def _decimal_attr(row: Any, field_name: str) -> Decimal:
    return Decimal(str(getattr(row, field_name)))


def _average(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return numerator / denominator


def _change_ratio(start: Decimal, end: Decimal) -> Decimal:
    return Decimal("0") if start == 0 else (end - start) / start


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


__all__ = [
    "MarketRegimeContextModel",
    "SupportDistanceConfirmationModel",
    "TrendStrengthDirectionalModel",
    "VolatilityRiskGateModel",
]
