"""Breakout/pullback trigger filter strategy for stage-23D.

This file belongs to `app/strategy/strategies`. It evaluates price behavior
around public key levels emitted earlier in the same run by support/resistance
strategies.
It is called by `app/strategy/runner.py::StrategyRunner.run_strategies`.
It does not query databases, request Binance, read or write Redis, send Hermes,
call DeepSeek or any large language model, read account or position state,
generate final advice, build trade setups, modify Kline tables, or trade.
It only reads `EvidenceContext` public `common_result.key_levels` and never
reads 23C private `strategy_payload_json` or 23C internal helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from app.strategy.base import BaseStrategy
from app.strategy.common.result_contract import (
    StrategyCommonResult,
    StrategyEvidenceItem,
    StrategyResult,
    StrategyRole,
)
from app.strategy.evidence_context import EvidenceContext
from app.strategy.types import StrategyEvaluationInput, StrategySignalStatus

TRIGGER_STATES = frozenset(
    {
        "breakout_attempt",
        "breakout_confirmed",
        "breakout_failed",
        "breakdown_attempt",
        "breakdown_confirmed",
        "breakdown_failed",
        "pullback_testing",
        "pullback_confirmed",
        "pullback_failed",
        "false_breakout",
        "false_breakdown",
        "no_clear_trigger",
        "insufficient_key_levels",
        "insufficient_data",
        "unknown",
    }
)
FILTER_DECISIONS = frozenset({"passed", "blocked", "uncertain", "not_applicable"})
VOLUME_STATES = frozenset({"expanding", "contracting", "normal", "spike", "insufficient", "unknown"})
VOLUME_CONFIRMATIONS = frozenset({"confirming", "weakening", "rejection_signal", "neutral", "insufficient", "unknown"})


@dataclass(frozen=True)
class TestedKeyLevel:
    """Public key-level summary normalized for 23D calculations."""

    level_id: str
    level_type: str
    level_group: str
    zone_low: Decimal
    zone_high: Decimal
    zone_mid: Decimal
    confidence_score: Decimal
    current_relevance_score: Decimal
    distance_from_current_price_pct: Decimal
    role_flip_status: str
    zone_quality: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class TriggerEvaluation:
    """One level behavior evaluation before StrategyResult packaging."""

    level: TestedKeyLevel | None
    trigger_state: str
    filter_decision: str
    volume_state: str
    volume_confirmation: str
    confidence_score: Decimal
    reason_codes: tuple[str, ...]
    reason_text: str
    private_details: Mapping[str, Any]


class BreakoutPullbackTriggerStrategy(BaseStrategy):
    """Confirm key-level behavior as a filter result, not an advice."""

    strategy_name = "breakout_pullback_trigger_strategy"
    strategy_version = "23D-1"
    strategy_role = StrategyRole.FILTER.value

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        active_config = dict(config or {})
        self.strategy_version = str(active_config.get("strategy_version", self.strategy_version))
        self.strategy_role = str(active_config.get("strategy_role", self.strategy_role))
        self.provides = tuple(
            str(item)
            for item in active_config.get(
                "provides",
                (
                    "breakout_confirmation",
                    "breakdown_confirmation",
                    "pullback_confirmation",
                    "false_breakout_filter",
                    "trigger_state",
                    "volume_confirmation",
                ),
            )
        )
        self.requires = tuple(active_config.get("requires", ({"role": "support_resistance", "provides": "key_levels"},)))
        self.consumes = tuple(str(item) for item in active_config.get("consumes", ("common_result.key_levels",)))
        lookback_bars = _mapping(active_config.get("lookback_bars"))
        minimum_required = _mapping(active_config.get("minimum_required_bars"))
        thresholds = _mapping(active_config.get("thresholds"))
        volume = _mapping(active_config.get("volume"))
        output_limits = _mapping(active_config.get("output_limits"))
        self.base_lookback_bars = int(lookback_bars.get("base", 80))
        self.higher_lookback_bars = int(lookback_bars.get("higher", 120))
        self.minimum_required_base_bars = int(minimum_required.get("base", 40))
        self.minimum_required_higher_bars = int(minimum_required.get("higher", 60))
        self.min_breakout_pct = Decimal(str(thresholds.get("min_breakout_pct", "0.003")))
        self.min_breakdown_pct = Decimal(str(thresholds.get("min_breakdown_pct", "0.003")))
        self.zone_touch_tolerance_pct = Decimal(str(thresholds.get("zone_touch_tolerance_pct", "0.004")))
        self.wick_rejection_ratio = Decimal(str(thresholds.get("wick_rejection_ratio", "0.45")))
        self.pullback_max_depth_pct = Decimal(str(thresholds.get("pullback_max_depth_pct", "0.015")))
        self.confirmation_bars = int(thresholds.get("confirmation_bars", 2))
        self.recent_trigger_lookback_bars = int(thresholds.get("recent_trigger_lookback_bars", 6))
        self.volume_enabled = bool(volume.get("enabled", True))
        self.volume_ma_period = int(volume.get("volume_ma_period", 20))
        self.volume_expand_ratio = Decimal(str(volume.get("volume_expand_ratio", "1.30")))
        self.volume_spike_ratio = Decimal(str(volume.get("volume_spike_ratio", "2.00")))
        self.volume_contract_ratio = Decimal(str(volume.get("volume_contract_ratio", "0.80")))
        self.output_tested_levels = int(output_limits.get("tested_levels", 5))

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        """Return not-applicable when called without same-run key-level evidence."""

        return self.evaluate_with_evidence(input_data, EvidenceContext.empty())

    def evaluate_with_evidence(
        self,
        input_data: StrategyEvaluationInput,
        evidence_context: EvidenceContext,
    ) -> StrategyResult:
        """Evaluate public 23C key levels with snapshot-derived Kline rows.

        Parameters: `input_data` is the restored snapshot window; `evidence_context`
        contains public `common_result` fields from earlier same-run strategies.
        Return value: one filter-role StrategyResult.
        Failure scenarios: insufficient Klines or missing key levels produce
        non-throwing not-applicable/invalid results.
        External service and storage impact: none.
        """

        base_rows = tuple(input_data.base_klines)
        higher_rows = tuple(input_data.higher_klines)
        if len(base_rows) < self.minimum_required_base_bars or len(higher_rows) < self.minimum_required_higher_bars:
            return self._insufficient_data_result(input_data, len(base_rows), len(higher_rows))

        public_key_levels = evidence_context.key_levels_for_role(StrategyRole.SUPPORT_RESISTANCE.value)
        tested_levels = _select_tested_levels(
            public_key_levels,
            latest_close=_decimal_attr(base_rows[-1], "close_price"),
            limit=self.output_tested_levels,
        )
        if not tested_levels:
            return self._missing_key_levels_result(input_data)

        window = base_rows[-self.base_lookback_bars :]
        evaluations = tuple(_evaluate_level(level, window, self) for level in tested_levels)
        selected = _select_primary_evaluation(evaluations)
        return self._result_from_evaluation(input_data, selected, evaluations)

    def _result_from_evaluation(
        self,
        input_data: StrategyEvaluationInput,
        selected: TriggerEvaluation,
        evaluations: tuple[TriggerEvaluation, ...],
    ) -> StrategyResult:
        filter_status = _filter_status(selected.filter_decision)
        common_result = StrategyCommonResult(
            market_bias="not_applicable",
            risk_level="not_applicable",
            signal_strength=_decimal_text(selected.confidence_score),
            confidence_score=_decimal_text(selected.confidence_score),
            reason_codes=selected.reason_codes,
            reason_text=selected.reason_text,
            evidence_items=(
                StrategyEvidenceItem(
                    evidence_type="breakout_pullback_trigger_filter",
                    direction="not_applicable",
                    strength=_decimal_text(selected.confidence_score),
                    description=selected.reason_text,
                    source=self.strategy_name,
                ),
            ),
            observation_window={
                "base_interval_value": input_data.base_interval_value,
                "higher_interval_value": input_data.higher_interval_value,
                "base_start_open_time_ms": input_data.base_start_open_time_ms,
                "base_end_open_time_ms": input_data.base_end_open_time_ms,
            },
            filter_status=filter_status,
            trigger_state=selected.trigger_state,
            filter_decision=selected.filter_decision,
            tested_level_summary=_tested_level_summary(selected.level),
            volume_state=selected.volume_state,
            volume_confirmation=selected.volume_confirmation,
            not_trading_advice=True,
        )
        return StrategyResult(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_role=self.strategy_role,
            strategy_status=StrategySignalStatus.SUCCESS.value,
            common_result=common_result,
            strategy_model_material_json={
                "summary": selected.reason_text,
                "provides": self.provides,
                "consumes": self.consumes,
                "not_trading_advice": True,
            },
            strategy_payload_json={
                "tested_level_id": selected.level.level_id if selected.level else None,
                "tested_level_type": selected.level.level_type if selected.level else None,
                "tested_level_group": selected.level.level_group if selected.level else None,
                "trigger_state": selected.trigger_state,
                "filter_decision": selected.filter_decision,
                "breakout_distance_pct": selected.private_details.get("breakout_distance_pct"),
                "breakdown_distance_pct": selected.private_details.get("breakdown_distance_pct"),
                "close_relation_to_zone": selected.private_details.get("close_relation_to_zone"),
                "wick_rejection_ratio": selected.private_details.get("wick_rejection_ratio"),
                "confirmation_bars": self.confirmation_bars,
                "pullback_depth_pct": selected.private_details.get("pullback_depth_pct"),
                "volume_ratio": selected.private_details.get("volume_ratio"),
                "volume_ma_period": self.volume_ma_period,
                "breakout_bar_volume": selected.private_details.get("current_volume"),
                "average_volume": selected.private_details.get("average_volume"),
                "volume_confirmation_result": selected.volume_confirmation,
                "false_breakout_details": selected.private_details.get("false_breakout_details"),
                "false_breakdown_details": selected.private_details.get("false_breakdown_details"),
                "pullback_detection_details": selected.private_details.get("pullback_detection_details"),
                "calculation_params": _calculation_params(self),
                "selected_key_level_candidates": [_private_candidate_json(item) for item in evaluations],
            },
            trace_id=input_data.trace_id,
        )

    def _missing_key_levels_result(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        reason_text = "缺少同轮支撑压力公开 key_levels，突破回踩过滤器不适用。"
        common_result = StrategyCommonResult(
            market_bias="not_applicable",
            risk_level="not_applicable",
            signal_strength="0",
            confidence_score="0",
            reason_codes=("missing_support_resistance_key_levels",),
            reason_text=reason_text,
            filter_status="unknown",
            trigger_state="insufficient_key_levels",
            filter_decision="not_applicable",
            tested_level_summary={},
            volume_state="unknown",
            volume_confirmation="unknown",
            not_trading_advice=True,
        )
        return StrategyResult(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_role=self.strategy_role,
            strategy_status=StrategySignalStatus.NO_SIGNAL.value,
            common_result=common_result,
            strategy_model_material_json={"summary": reason_text, "consumes": self.consumes},
            strategy_payload_json={
                "tested_level_id": None,
                "calculation_params": _calculation_params(self),
                "selected_key_level_candidates": [],
                "missing_key_levels": True,
            },
            trace_id=input_data.trace_id,
        )

    def _insufficient_data_result(self, input_data: StrategyEvaluationInput, actual_base_count: int, actual_higher_count: int) -> StrategyResult:
        reason_text = (
            "突破回踩过滤器所需 K线数量不足，暂不判断关键位行为。"
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
            filter_status="unknown",
            trigger_state="insufficient_data",
            filter_decision="not_applicable",
            volume_state="insufficient",
            volume_confirmation="insufficient",
            not_trading_advice=True,
        )
        return StrategyResult(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_role=self.strategy_role,
            strategy_status=StrategySignalStatus.INVALID.value,
            common_result=common_result,
            strategy_model_material_json={"summary": reason_text, "consumes": self.consumes},
            strategy_payload_json={
                "insufficient_data": {
                    "actual_base_count": actual_base_count,
                    "actual_higher_count": actual_higher_count,
                },
                "calculation_params": _calculation_params(self),
                "selected_key_level_candidates": [],
            },
            trace_id=input_data.trace_id,
        )


def _select_tested_levels(
    raw_levels: tuple[Mapping[str, Any], ...],
    *,
    latest_close: Decimal,
    limit: int,
) -> tuple[TestedKeyLevel, ...]:
    levels = tuple(_level_from_public_payload(item) for item in raw_levels)
    valid_levels = tuple(level for level in levels if level is not None and level.zone_quality != "outlier")
    return tuple(
        sorted(
            valid_levels,
            key=lambda item: (
                _level_group_priority(item.level_group),
                _distance_to_price(item.zone_mid, latest_close),
                -item.current_relevance_score,
                -item.confidence_score,
            ),
        )[:limit]
    )


def _evaluate_level(level: TestedKeyLevel, rows: tuple[Any, ...], strategy: BreakoutPullbackTriggerStrategy) -> TriggerEvaluation:
    latest = rows[-1]
    previous = rows[-2] if len(rows) >= 2 else rows[-1]
    close = _decimal_attr(latest, "close_price")
    high = _decimal_attr(latest, "high_price")
    low = _decimal_attr(latest, "low_price")
    previous_close = _decimal_attr(previous, "close_price")
    volume_state, volume_confirmation_seed, volume_details = _volume_evidence(rows, strategy)
    upper_wick_ratio = _upper_wick_ratio(latest)
    lower_wick_ratio = _lower_wick_ratio(latest)
    pullback_state, pullback_details = _pullback_state(level, rows, strategy)
    if pullback_state != "none":
        details = _base_details(level, close, high, low, volume_details, upper_wick_ratio, lower_wick_ratio)
        details.update(pullback_details)
        return _build_evaluation(level, pullback_state, volume_state, volume_confirmation_seed, details, strategy)
    if _is_resistance_level(level):
        if high > level.zone_high and close <= level.zone_high and upper_wick_ratio >= strategy.wick_rejection_ratio:
            details = _base_details(level, close, high, low, volume_details, upper_wick_ratio, lower_wick_ratio)
            details["false_breakout_details"] = {"high_above_zone": True, "close_recovered_below_zone_high": True}
            return _build_evaluation(level, "false_breakout", volume_state, volume_confirmation_seed, details, strategy)
        breakout_distance = _positive_ratio(close - level.zone_high, level.zone_high)
        if close > level.zone_high and breakout_distance >= strategy.min_breakout_pct and upper_wick_ratio < strategy.wick_rejection_ratio:
            details = _base_details(level, close, high, low, volume_details, upper_wick_ratio, lower_wick_ratio)
            details["breakout_distance_pct"] = _pct_text(breakout_distance)
            return _build_evaluation(level, "breakout_confirmed", volume_state, volume_confirmation_seed, details, strategy)
        if _touches_zone(high, low, close, level, strategy) or close > level.zone_low:
            details = _base_details(level, close, high, low, volume_details, upper_wick_ratio, lower_wick_ratio)
            details["breakout_distance_pct"] = _pct_text(max(Decimal("0"), breakout_distance))
            return _build_evaluation(level, "breakout_attempt", volume_state, volume_confirmation_seed, details, strategy)
    if _is_support_level(level):
        if low < level.zone_low and close >= level.zone_low and lower_wick_ratio >= strategy.wick_rejection_ratio:
            details = _base_details(level, close, high, low, volume_details, upper_wick_ratio, lower_wick_ratio)
            details["false_breakdown_details"] = {"low_below_zone": True, "close_recovered_above_zone_low": True}
            return _build_evaluation(level, "false_breakdown", volume_state, volume_confirmation_seed, details, strategy)
        breakdown_distance = _positive_ratio(level.zone_low - close, level.zone_low)
        if close < level.zone_low and breakdown_distance >= strategy.min_breakdown_pct and lower_wick_ratio < strategy.wick_rejection_ratio:
            details = _base_details(level, close, high, low, volume_details, upper_wick_ratio, lower_wick_ratio)
            details["breakdown_distance_pct"] = _pct_text(breakdown_distance)
            return _build_evaluation(level, "breakdown_confirmed", volume_state, volume_confirmation_seed, details, strategy)
        if _touches_zone(high, low, close, level, strategy) or close < level.zone_high:
            details = _base_details(level, close, high, low, volume_details, upper_wick_ratio, lower_wick_ratio)
            details["breakdown_distance_pct"] = _pct_text(max(Decimal("0"), breakdown_distance))
            return _build_evaluation(level, "breakdown_attempt", volume_state, volume_confirmation_seed, details, strategy)
    details = _base_details(level, close, high, low, volume_details, upper_wick_ratio, lower_wick_ratio)
    details["previous_close"] = _price_text(previous_close)
    return _build_evaluation(level, "no_clear_trigger", volume_state, volume_confirmation_seed, details, strategy)


def _pullback_state(level: TestedKeyLevel, rows: tuple[Any, ...], strategy: BreakoutPullbackTriggerStrategy) -> tuple[str, Mapping[str, Any]]:
    latest = rows[-1]
    close = _decimal_attr(latest, "close_price")
    low = _decimal_attr(latest, "low_price")
    high = _decimal_attr(latest, "high_price")
    previous_rows = rows[-strategy.recent_trigger_lookback_bars - 1 : -1]
    had_up_breakout = any(
        _positive_ratio(_decimal_attr(row, "close_price") - level.zone_high, level.zone_high) >= strategy.min_breakout_pct
        for row in previous_rows
    )
    had_down_breakdown = any(
        _positive_ratio(level.zone_low - _decimal_attr(row, "close_price"), level.zone_low) >= strategy.min_breakdown_pct
        for row in previous_rows
    )
    if _uses_up_breakout_pullback(level) and had_up_breakout and low <= level.zone_high * (Decimal("1") + strategy.zone_touch_tolerance_pct):
        depth = _positive_ratio(level.zone_high - low, level.zone_high)
        details = {"pullback_direction": "after_breakout", "pullback_depth_pct": _pct_text(depth)}
        if close < level.zone_low:
            return "pullback_failed", details
        if close >= level.zone_high and depth <= strategy.pullback_max_depth_pct:
            return "pullback_confirmed", details
        return "pullback_testing", details
    if _uses_down_breakdown_pullback(level) and had_down_breakdown and high >= level.zone_low * (Decimal("1") - strategy.zone_touch_tolerance_pct):
        depth = _positive_ratio(high - level.zone_low, level.zone_low)
        details = {"pullback_direction": "after_breakdown", "pullback_depth_pct": _pct_text(depth)}
        if close > level.zone_high:
            return "pullback_failed", details
        if close <= level.zone_low and depth <= strategy.pullback_max_depth_pct:
            return "pullback_confirmed", details
        return "pullback_testing", details
    return "none", {}


def _build_evaluation(
    level: TestedKeyLevel,
    trigger_state: str,
    volume_state: str,
    volume_confirmation_seed: str,
    details: Mapping[str, Any],
    strategy: BreakoutPullbackTriggerStrategy,
) -> TriggerEvaluation:
    volume_confirmation = _volume_confirmation(trigger_state, volume_confirmation_seed)
    decision = _filter_decision(trigger_state, volume_confirmation)
    confidence = _confidence(trigger_state, level, volume_confirmation)
    reason_codes = (trigger_state, f"filter_{decision}", f"volume_{volume_confirmation}")
    reason_text = _reason_text(trigger_state, decision, volume_confirmation)
    private_details = dict(details)
    private_details.setdefault("breakout_distance_pct", None)
    private_details.setdefault("breakdown_distance_pct", None)
    private_details.setdefault("pullback_detection_details", details if trigger_state.startswith("pullback_") else {})
    private_details.setdefault("wick_rejection_ratio", _decimal_text(max(_upper_wick_ratio_from_details(details), _lower_wick_ratio_from_details(details))))
    private_details.setdefault("calculation_params", _calculation_params(strategy))
    return TriggerEvaluation(
        level=level,
        trigger_state=trigger_state,
        filter_decision=decision,
        volume_state=volume_state,
        volume_confirmation=volume_confirmation,
        confidence_score=confidence,
        reason_codes=reason_codes,
        reason_text=reason_text,
        private_details=private_details,
    )


def _select_primary_evaluation(evaluations: tuple[TriggerEvaluation, ...]) -> TriggerEvaluation:
    return max(
        evaluations,
        key=lambda item: (
            _trigger_priority(item.trigger_state),
            item.confidence_score,
            item.level.current_relevance_score if item.level else Decimal("0"),
        ),
    )


def _volume_evidence(rows: tuple[Any, ...], strategy: BreakoutPullbackTriggerStrategy) -> tuple[str, str, Mapping[str, Any]]:
    if not strategy.volume_enabled:
        return "unknown", "unknown", {"volume_enabled": False}
    volumes = [_optional_decimal_attr(row, "volume") for row in rows]
    if volumes[-1] is None:
        return "insufficient", "insufficient", {"current_volume": None, "average_volume": None, "volume_ratio": None}
    lookback = [item for item in volumes[-strategy.volume_ma_period - 1 : -1] if item is not None]
    if not lookback:
        return "insufficient", "insufficient", {"current_volume": _decimal_text(volumes[-1]), "average_volume": None, "volume_ratio": None}
    average_volume = sum(lookback, Decimal("0")) / Decimal(len(lookback))
    if average_volume <= 0:
        return "insufficient", "insufficient", {"current_volume": _decimal_text(volumes[-1]), "average_volume": "0", "volume_ratio": None}
    ratio = volumes[-1] / average_volume
    if ratio >= strategy.volume_spike_ratio:
        state = "spike"
    elif ratio >= strategy.volume_expand_ratio:
        state = "expanding"
    elif ratio <= strategy.volume_contract_ratio:
        state = "contracting"
    else:
        state = "normal"
    return state, state, {"current_volume": _decimal_text(volumes[-1]), "average_volume": _decimal_text(average_volume), "volume_ratio": _decimal_text(ratio)}


def _volume_confirmation(trigger_state: str, volume_state: str) -> str:
    if volume_state in {"insufficient", "unknown"}:
        return volume_state
    if trigger_state in {"false_breakout", "false_breakdown"} and volume_state in {"expanding", "spike"}:
        return "rejection_signal"
    if trigger_state in {"breakout_confirmed", "breakdown_confirmed", "pullback_confirmed"}:
        if volume_state in {"expanding", "spike"}:
            return "confirming"
        if volume_state == "contracting":
            return "weakening"
    return "neutral"


def _filter_decision(trigger_state: str, volume_confirmation: str) -> str:
    if trigger_state in {"insufficient_key_levels", "insufficient_data"}:
        return "not_applicable"
    if trigger_state in {"false_breakout", "false_breakdown", "breakout_failed", "breakdown_failed", "pullback_failed"}:
        return "blocked"
    if trigger_state in {"breakout_confirmed", "breakdown_confirmed", "pullback_confirmed"}:
        return "uncertain" if volume_confirmation == "weakening" else "passed"
    return "uncertain"


def _confidence(trigger_state: str, level: TestedKeyLevel, volume_confirmation: str) -> Decimal:
    base = {
        "breakout_attempt": Decimal("0.44"),
        "breakout_confirmed": Decimal("0.68"),
        "breakout_failed": Decimal("0.66"),
        "breakdown_attempt": Decimal("0.44"),
        "breakdown_confirmed": Decimal("0.68"),
        "breakdown_failed": Decimal("0.66"),
        "pullback_testing": Decimal("0.50"),
        "pullback_confirmed": Decimal("0.66"),
        "pullback_failed": Decimal("0.66"),
        "false_breakout": Decimal("0.72"),
        "false_breakdown": Decimal("0.72"),
        "no_clear_trigger": Decimal("0.22"),
    }.get(trigger_state, Decimal("0.10"))
    confidence = base + level.confidence_score * Decimal("0.12") + level.current_relevance_score * Decimal("0.08")
    if volume_confirmation in {"confirming", "rejection_signal"}:
        confidence += Decimal("0.08")
    elif volume_confirmation == "weakening":
        confidence -= Decimal("0.16")
    return _clamp_unit(confidence)


def _base_details(
    level: TestedKeyLevel,
    close: Decimal,
    high: Decimal,
    low: Decimal,
    volume_details: Mapping[str, Any],
    upper_wick_ratio: Decimal,
    lower_wick_ratio: Decimal,
) -> dict[str, Any]:
    details = dict(volume_details)
    details.update(
        {
            "tested_level_id": level.level_id,
            "latest_close": _price_text(close),
            "latest_high": _price_text(high),
            "latest_low": _price_text(low),
            "close_relation_to_zone": _close_relation(close, level),
            "upper_wick_ratio": _decimal_text(upper_wick_ratio),
            "lower_wick_ratio": _decimal_text(lower_wick_ratio),
        }
    )
    return details


def _level_from_public_payload(item: Mapping[str, Any]) -> TestedKeyLevel | None:
    try:
        zone_low = Decimal(str(item.get("zone_low")))
        zone_high = Decimal(str(item.get("zone_high")))
    except Exception:  # noqa: BLE001 - malformed public evidence is ignored.
        return None
    if zone_high < zone_low:
        zone_low, zone_high = zone_high, zone_low
    zone_mid = Decimal(str(item.get("zone_mid") or ((zone_low + zone_high) / Decimal("2"))))
    return TestedKeyLevel(
        level_id=str(item.get("level_id", "")),
        level_type=str(item.get("level_type", "")),
        level_group=str(item.get("level_group", "")),
        zone_low=zone_low,
        zone_high=zone_high,
        zone_mid=zone_mid,
        confidence_score=_unit_decimal(item.get("confidence_score")),
        current_relevance_score=_unit_decimal(item.get("current_relevance_score")),
        distance_from_current_price_pct=Decimal(str(item.get("distance_from_current_price_pct", "0"))),
        role_flip_status=str(item.get("role_flip_status", "none")),
        zone_quality=str(item.get("zone_quality", "unknown")),
        raw=dict(item),
    )


def _is_resistance_level(level: TestedKeyLevel) -> bool:
    role_side = _level_role_side(level)
    return role_side == "resistance"


def _is_support_level(level: TestedKeyLevel) -> bool:
    role_side = _level_role_side(level)
    return role_side == "support"


def _uses_up_breakout_pullback(level: TestedKeyLevel) -> bool:
    if level.level_group == "role_flip_candidate":
        return level.role_flip_status == "resistance_to_support"
    return _is_resistance_level(level)


def _uses_down_breakdown_pullback(level: TestedKeyLevel) -> bool:
    if level.level_group == "role_flip_candidate":
        return level.role_flip_status == "support_to_resistance"
    return _is_support_level(level)


def _level_role_side(level: TestedKeyLevel) -> str:
    if level.level_group == "role_flip_candidate":
        if level.role_flip_status == "resistance_to_support":
            return "support"
        if level.role_flip_status == "support_to_resistance":
            return "resistance"
        return "unknown"
    if level.level_group in {"nearest_resistance", "major_resistance", "range_upper_boundary"} or level.level_type in {
        "resistance",
        "target_observation",
    }:
        return "resistance"
    if level.level_group in {"nearest_support", "major_support", "range_lower_boundary"} or level.level_type in {
        "support",
        "invalidation_reference",
    }:
        return "support"
    return "unknown"


def _touches_zone(high: Decimal, low: Decimal, close: Decimal, level: TestedKeyLevel, strategy: BreakoutPullbackTriggerStrategy) -> bool:
    tolerance = Decimal("1") + strategy.zone_touch_tolerance_pct
    lower_tolerance = Decimal("1") - strategy.zone_touch_tolerance_pct
    return high >= level.zone_low * lower_tolerance and low <= level.zone_high * tolerance or level.zone_low <= close <= level.zone_high


def _upper_wick_ratio(row: Any) -> Decimal:
    high = _decimal_attr(row, "high_price")
    close = _decimal_attr(row, "close_price")
    open_price = _optional_decimal_attr(row, "open_price") or close
    total_range = high - _decimal_attr(row, "low_price")
    if total_range <= 0:
        return Decimal("0")
    return _clamp_unit((high - max(open_price, close)) / total_range)


def _lower_wick_ratio(row: Any) -> Decimal:
    low = _decimal_attr(row, "low_price")
    close = _decimal_attr(row, "close_price")
    open_price = _optional_decimal_attr(row, "open_price") or close
    total_range = _decimal_attr(row, "high_price") - low
    if total_range <= 0:
        return Decimal("0")
    return _clamp_unit((min(open_price, close) - low) / total_range)


def _tested_level_summary(level: TestedKeyLevel | None) -> Mapping[str, Any]:
    if level is None:
        return {}
    return {
        "level_id": level.level_id,
        "level_type": level.level_type,
        "level_group": level.level_group,
        "zone_low": _price_text(level.zone_low),
        "zone_high": _price_text(level.zone_high),
        "zone_mid": _price_text(level.zone_mid),
        "distance_from_current_price_pct": _decimal_text(level.distance_from_current_price_pct),
        "confidence_score": _decimal_text(level.confidence_score),
        "current_relevance_score": _decimal_text(level.current_relevance_score),
        "role_flip_status": level.role_flip_status,
        "zone_quality": level.zone_quality,
    }


def _private_candidate_json(item: TriggerEvaluation) -> Mapping[str, Any]:
    return {
        "level": _tested_level_summary(item.level),
        "trigger_state": item.trigger_state,
        "filter_decision": item.filter_decision,
        "confidence_score": _decimal_text(item.confidence_score),
        "volume_state": item.volume_state,
        "volume_confirmation": item.volume_confirmation,
    }


def _calculation_params(strategy: BreakoutPullbackTriggerStrategy) -> Mapping[str, Any]:
    return {
        "min_breakout_pct": _decimal_text(strategy.min_breakout_pct),
        "min_breakdown_pct": _decimal_text(strategy.min_breakdown_pct),
        "zone_touch_tolerance_pct": _decimal_text(strategy.zone_touch_tolerance_pct),
        "wick_rejection_ratio": _decimal_text(strategy.wick_rejection_ratio),
        "pullback_max_depth_pct": _decimal_text(strategy.pullback_max_depth_pct),
        "confirmation_bars": strategy.confirmation_bars,
        "recent_trigger_lookback_bars": strategy.recent_trigger_lookback_bars,
        "volume_ma_period": strategy.volume_ma_period,
    }


def _filter_status(decision: str) -> str:
    if decision == "passed":
        return "pass"
    if decision == "blocked":
        return "reject"
    return "unknown"


def _trigger_priority(trigger_state: str) -> int:
    return {
        "false_breakout": 90,
        "false_breakdown": 90,
        "breakout_confirmed": 80,
        "breakdown_confirmed": 80,
        "pullback_confirmed": 78,
        "breakout_failed": 70,
        "breakdown_failed": 70,
        "pullback_failed": 70,
        "pullback_testing": 55,
        "breakout_attempt": 50,
        "breakdown_attempt": 50,
        "no_clear_trigger": 10,
    }.get(trigger_state, 0)


def _level_group_priority(level_group: str) -> int:
    return {
        "nearest_resistance": 0,
        "nearest_support": 1,
        "range_upper_boundary": 2,
        "range_lower_boundary": 3,
        "role_flip_candidate": 4,
        "major_resistance": 5,
        "major_support": 6,
    }.get(level_group, 99)


def _reason_text(trigger_state: str, decision: str, volume_confirmation: str) -> str:
    return (
        f"关键位行为识别为 {trigger_state}，过滤结果为 {decision}，"
        f"成交量确认状态为 {volume_confirmation}。该结果只是触发过滤证据，不是交易建议。"
    )


def _close_relation(close: Decimal, level: TestedKeyLevel) -> str:
    if close > level.zone_high:
        return "above_zone"
    if close < level.zone_low:
        return "below_zone"
    return "inside_zone"


def _distance_to_price(value: Decimal, latest_close: Decimal) -> Decimal:
    if latest_close <= 0:
        return Decimal("1")
    return abs(value - latest_close) / latest_close


def _positive_ratio(value: Decimal, base: Decimal) -> Decimal:
    if base <= 0 or value <= 0:
        return Decimal("0")
    return value / base


def _upper_wick_ratio_from_details(details: Mapping[str, Any]) -> Decimal:
    return Decimal(str(details.get("upper_wick_ratio", "0")))


def _lower_wick_ratio_from_details(details: Mapping[str, Any]) -> Decimal:
    return Decimal(str(details.get("lower_wick_ratio", "0")))


def _decimal_attr(row: Any, field_name: str) -> Decimal:
    return Decimal(str(getattr(row, field_name)))


def _optional_decimal_attr(row: Any, field_name: str) -> Decimal | None:
    value = getattr(row, field_name, None)
    if value is None:
        return None
    return Decimal(str(value))


def _unit_decimal(value: Any) -> Decimal:
    try:
        return _clamp_unit(Decimal(str(value)))
    except Exception:  # noqa: BLE001
        return Decimal("0")


def _clamp_unit(value: Decimal) -> Decimal:
    return min(Decimal("1"), max(Decimal("0"), value))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _price_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _pct_text(value: Decimal) -> str:
    return _decimal_text(value * Decimal("100"))


__all__ = ["BreakoutPullbackTriggerStrategy"]
