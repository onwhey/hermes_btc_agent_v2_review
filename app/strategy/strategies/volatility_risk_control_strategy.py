"""Volatility and risk-gate strategy for stage-23E.

This file belongs to `app/strategy/strategies`. It evaluates global market
risk and current candidate risk from local Kline windows plus same-run public
strategy evidence.
It is called by `app/strategy/runner.py::StrategyRunner.run_strategies`.
It does not query databases, request Binance, read or write Redis, send Hermes,
call DeepSeek or any large language model, read account state, generate final
advice, build trade setups, modify Kline tables, or trade.
It only reads public `EvidenceContext` common results and never reads previous
strategy `strategy_payload_json` values or private helper functions.
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
    StrategyRiskFlag,
    StrategyRole,
)
from app.strategy.evidence_context import EvidenceContext
from app.strategy.types import StrategyEvaluationInput, StrategySignalStatus

_RISK_ORDER = {"low": 1, "medium": 2, "high": 3, "extreme": 4, "unknown": 5}


@dataclass(frozen=True)
class MarketContext:
    """Public market-state evidence extracted from context role outputs."""

    found: bool
    primary_regime: str
    regime_phase: str
    trend_strength: str
    decision_implication: str
    market_bias: str
    market_environment_context: str
    context_summary: str


@dataclass(frozen=True)
class TriggerContext:
    """Public trigger evidence extracted from filter role outputs."""

    found: bool
    trigger_state: str
    filter_decision: str
    tested_level_summary: Mapping[str, Any]
    volume_state: str
    volume_confirmation: str


@dataclass(frozen=True)
class VolatilityMetrics:
    """Private volatility metrics computed from StrategyEvaluationInput."""

    volatility_state: str
    atr_value: Decimal
    atr_pct: Decimal
    recent_range_pct: Decimal
    average_range_pct: Decimal
    range_expansion_ratio: Decimal
    latest_bar_range_pct: Decimal
    wick_risk_score: Decimal


@dataclass(frozen=True)
class SpaceMetrics:
    """Private directional room/risk metrics derived from public key levels."""

    long_feasibility: str
    short_feasibility: str
    long_room_to_resistance_pct: Decimal | None
    long_risk_to_support_pct: Decimal | None
    short_room_to_support_pct: Decimal | None
    short_risk_to_resistance_pct: Decimal | None
    rough_long_reward_risk_ratio: Decimal | None
    rough_short_reward_risk_ratio: Decimal | None
    nearest_support: Mapping[str, Any] | None
    nearest_resistance: Mapping[str, Any] | None


@dataclass(frozen=True)
class GateDecision:
    """Public gate decision and private scoring summary."""

    risk_gate_decision: str
    risk_scope: str
    global_market_risk: str
    candidate_risk: str
    chase_risk: str
    reason_codes: tuple[str, ...]
    confidence_score: Decimal
    scoring_details: Mapping[str, Any]


class VolatilityRiskControlStrategy(BaseStrategy):
    """Risk-control gate that can downgrade a public trigger without advice."""

    strategy_name = "volatility_risk_control_strategy"
    strategy_version = "23E-1"
    strategy_role = StrategyRole.RISK_CONTROL.value

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Load thresholds and risk profiles from config without side effects."""

        active_config = dict(config or {})
        self.strategy_version = str(active_config.get("strategy_version", self.strategy_version))
        self.strategy_role = str(active_config.get("strategy_role", self.strategy_role))
        self.provides = tuple(
            str(item)
            for item in active_config.get(
                "provides",
                (
                    "volatility_risk",
                    "trade_permission_filter",
                    "risk_gate_decision",
                    "reward_risk_feasibility",
                    "chase_risk",
                    "stop_distance_reference",
                    "market_state_aware_risk_policy",
                ),
            )
        )
        self.requires = tuple(active_config.get("requires", ()))
        self.consumes = tuple(str(item) for item in active_config.get("consumes", ()))
        lookback_bars = _mapping(active_config.get("lookback_bars"))
        minimum_required = _mapping(active_config.get("minimum_required_bars"))
        atr_config = _mapping(active_config.get("atr"))
        thresholds = _mapping(active_config.get("thresholds"))
        self.base_lookback_bars = int(lookback_bars.get("base", 80))
        self.higher_lookback_bars = int(lookback_bars.get("higher", 120))
        self.minimum_required_base_bars = int(minimum_required.get("base", 40))
        self.minimum_required_higher_bars = int(minimum_required.get("higher", 60))
        self.atr_period = int(atr_config.get("period", 14))
        self.high_atr_pct = _decimal_config(thresholds, "high_atr_pct", "0.035")
        self.extreme_atr_pct = _decimal_config(thresholds, "extreme_atr_pct", "0.060")
        self.high_range_expansion_ratio = _decimal_config(thresholds, "high_range_expansion_ratio", "1.60")
        self.extreme_range_expansion_ratio = _decimal_config(thresholds, "extreme_range_expansion_ratio", "2.30")
        self.high_chase_distance_pct = _decimal_config(thresholds, "high_chase_distance_pct", "0.020")
        self.extreme_chase_distance_pct = _decimal_config(thresholds, "extreme_chase_distance_pct", "0.035")
        self.min_rough_reward_risk_ratio = _decimal_config(thresholds, "min_rough_reward_risk_ratio", "1.50")
        self.min_net_room_pct = _decimal_config(thresholds, "min_net_room_pct", "0.008")
        self.fee_buffer_pct = _decimal_config(thresholds, "fee_buffer_pct", "0.0004")
        self.slippage_buffer_pct = _decimal_config(thresholds, "slippage_buffer_pct", "0.0010")
        self.risk_policy_mapping = _mapping(active_config.get("risk_policy_mapping"))
        self.risk_policy_profiles = _mapping(active_config.get("risk_policy_profiles"))

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        """Evaluate without public context; this conservatively blocks allow."""

        return self.evaluate_with_evidence(input_data, EvidenceContext.empty())

    def evaluate_with_evidence(
        self,
        input_data: StrategyEvaluationInput,
        evidence_context: EvidenceContext,
    ) -> StrategyResult:
        """Evaluate public evidence from 23B/23C/23D and local Kline windows."""

        base_rows = tuple(input_data.base_klines)[-self.base_lookback_bars :]
        higher_rows = tuple(input_data.higher_klines)[-self.higher_lookback_bars :]
        if len(base_rows) < self.minimum_required_base_bars or len(higher_rows) < self.minimum_required_higher_bars:
            return self._insufficient_data_result(input_data, len(base_rows), len(higher_rows))

        market_context = _extract_market_context(evidence_context)
        key_levels = evidence_context.key_levels_for_role("support_resistance")
        trigger_context = _extract_trigger_context(evidence_context)
        volatility = _volatility_metrics(base_rows, self)
        latest_close = _decimal_attr(base_rows[-1], "close_price")
        space = _space_metrics(key_levels, latest_close, self)
        policy_name, policy_source = _select_policy_profile(market_context, self)
        missing_context = _missing_context_reasons(market_context, key_levels, trigger_context)
        if missing_context:
            gate = _insufficient_context_gate(volatility, policy_name, missing_context)
        else:
            gate = _decide_gate(
                market_context=market_context,
                trigger_context=trigger_context,
                volatility=volatility,
                space=space,
                policy_name=policy_name,
                strategy=self,
                latest_close=latest_close,
            )
        return self._build_result(
            input_data=input_data,
            market_context=market_context,
            trigger_context=trigger_context,
            key_levels=key_levels,
            volatility=volatility,
            space=space,
            gate=gate,
            policy_name=policy_name,
            policy_source=policy_source,
            status=StrategySignalStatus.SUCCESS.value,
        )

    def _insufficient_data_result(
        self,
        input_data: StrategyEvaluationInput,
        actual_base_count: int,
        actual_higher_count: int,
    ) -> StrategyResult:
        """Return an invalid risk-control result when Kline windows are short."""

        volatility = VolatilityMetrics(
            volatility_state="insufficient_data",
            atr_value=Decimal("0"),
            atr_pct=Decimal("0"),
            recent_range_pct=Decimal("0"),
            average_range_pct=Decimal("0"),
            range_expansion_ratio=Decimal("0"),
            latest_bar_range_pct=Decimal("0"),
            wick_risk_score=Decimal("0"),
        )
        gate = GateDecision(
            risk_gate_decision="insufficient_context",
            risk_scope="unknown",
            global_market_risk="insufficient_data",
            candidate_risk="unknown",
            chase_risk="unknown",
            reason_codes=("insufficient_data",),
            confidence_score=Decimal("0"),
            scoring_details={"actual_base_count": actual_base_count, "actual_higher_count": actual_higher_count},
        )
        return self._build_result(
            input_data=input_data,
            market_context=MarketContext(
                found=False,
                primary_regime="unknown",
                regime_phase="unknown",
                trend_strength="0",
                decision_implication="",
                market_bias="unknown",
                market_environment_context="",
                context_summary="",
            ),
            trigger_context=TriggerContext(False, "unknown", "unknown", {}, "unknown", "unknown"),
            key_levels=(),
            volatility=volatility,
            space=_unknown_space(),
            gate=gate,
            policy_name="default_conservative",
            policy_source="insufficient_data",
            status=StrategySignalStatus.INVALID.value,
        )

    def _build_result(
        self,
        *,
        input_data: StrategyEvaluationInput,
        market_context: MarketContext,
        trigger_context: TriggerContext,
        key_levels: tuple[Mapping[str, Any], ...],
        volatility: VolatilityMetrics,
        space: SpaceMetrics,
        gate: GateDecision,
        policy_name: str,
        policy_source: str,
        status: str,
    ) -> StrategyResult:
        """Assemble the three-part StrategyResult without external writes."""

        risk_level = _common_risk_level(gate.global_market_risk, gate.candidate_risk)
        reason_text = _risk_reason_text(gate, market_context)
        common_result = StrategyCommonResult(
            market_bias="not_applicable",
            risk_level=risk_level,
            signal_strength=_decimal_text(gate.confidence_score),
            confidence_score=_decimal_text(gate.confidence_score),
            reason_codes=gate.reason_codes,
            reason_text=reason_text,
            risk_flags=_risk_flags(gate, reason_text, self.strategy_name),
            evidence_items=(
                StrategyEvidenceItem(
                    evidence_type="volatility_risk_control_gate",
                    direction="not_applicable",
                    strength=_decimal_text(gate.confidence_score),
                    description=reason_text,
                    source=self.strategy_name,
                ),
            ),
            observation_window={
                "base_interval_value": input_data.base_interval_value,
                "higher_interval_value": input_data.higher_interval_value,
                "base_start_open_time_ms": input_data.base_start_open_time_ms,
                "base_end_open_time_ms": input_data.base_end_open_time_ms,
            },
            risk_gate_decision=gate.risk_gate_decision,
            risk_scope=gate.risk_scope,
            global_market_risk=gate.global_market_risk,
            candidate_risk=gate.candidate_risk,
            volatility_state=volatility.volatility_state,
            chase_risk=gate.chase_risk,
            long_feasibility=space.long_feasibility,
            short_feasibility=space.short_feasibility,
            selected_risk_policy_profile=policy_name,
            not_trading_advice=True,
        )
        return StrategyResult(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_role=self.strategy_role,
            strategy_status=status,
            common_result=common_result,
            strategy_model_material_json={
                "summary": reason_text,
                "selected_risk_policy_profile": policy_name,
                "risk_review_focus": (
                    "Check volatility state, candidate room, chase risk, and whether 23D trigger should be downgraded."
                ),
            },
            strategy_payload_json=_private_payload(
                market_context=market_context,
                trigger_context=trigger_context,
                key_levels=key_levels,
                volatility=volatility,
                space=space,
                gate=gate,
                policy_name=policy_name,
                policy_source=policy_source,
                strategy=self,
            ),
            trace_id=input_data.trace_id,
        )


def _extract_market_context(evidence_context: EvidenceContext) -> MarketContext:
    for output in evidence_context.public_role_outputs.get("context", ()):
        payload = output.common_result
        reason_codes = tuple(str(item) for item in payload.get("reason_codes", ()) if isinstance(item, str))
        has_public_primary_regime = "primary_regime" in payload
        has_public_regime_phase = "regime_phase" in payload
        primary_regime = str(
            payload.get("primary_regime") or "unknown"
            if has_public_primary_regime
            else _code_suffix(reason_codes, "primary_regime_") or "unknown"
        )
        regime_phase = str(
            payload.get("regime_phase") or "unknown"
            if has_public_regime_phase
            else _code_suffix(reason_codes, "regime_phase_") or "unknown"
        )
        if has_public_primary_regime or primary_regime != "unknown" or "market_regime_classified" in reason_codes:
            return MarketContext(
                found=True,
                primary_regime=primary_regime,
                regime_phase=regime_phase,
                trend_strength=str(payload.get("trend_strength", "0")),
                decision_implication=str(payload.get("decision_implication", "")),
                market_bias=str(payload.get("market_bias", "unknown")),
                market_environment_context=str(payload.get("market_environment_context", "")),
                context_summary=str(payload.get("context_summary", "")),
            )
    return MarketContext(False, "unknown", "unknown", "0", "", "unknown", "", "")


def _extract_trigger_context(evidence_context: EvidenceContext) -> TriggerContext:
    for output in reversed(evidence_context.public_role_outputs.get("filter", ())):
        payload = output.common_result
        trigger_state = payload.get("trigger_state")
        if trigger_state:
            return TriggerContext(
                found=True,
                trigger_state=str(trigger_state),
                filter_decision=str(payload.get("filter_decision", "unknown")),
                tested_level_summary=_mapping(payload.get("tested_level_summary")),
                volume_state=str(payload.get("volume_state", "unknown")),
                volume_confirmation=str(payload.get("volume_confirmation", "unknown")),
            )
    return TriggerContext(False, "unknown", "unknown", {}, "unknown", "unknown")


def _missing_context_reasons(
    market_context: MarketContext,
    key_levels: tuple[Mapping[str, Any], ...],
    trigger_context: TriggerContext,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not market_context.found or market_context.primary_regime in {"unknown", "insufficient_data"}:
        reasons.append("missing_or_unknown_market_context")
    if not key_levels:
        reasons.append("missing_support_resistance_key_levels")
    if not trigger_context.found or trigger_context.trigger_state in {"unknown", "insufficient_key_levels"}:
        reasons.append("missing_trigger_context")
    return tuple(reasons)


def _select_policy_profile(market_context: MarketContext, strategy: VolatilityRiskControlStrategy) -> tuple[str, str]:
    mapping = strategy.risk_policy_mapping
    candidates = (
        f"regime_phase.{market_context.regime_phase}",
        f"primary_regime.{market_context.primary_regime}",
        f"market_bias.{market_context.market_bias}",
        "default",
    )
    for key in candidates:
        value = mapping.get(key)
        if value:
            return str(value), key
    return "default_conservative", "fallback"


def _volatility_metrics(rows: tuple[Any, ...], strategy: VolatilityRiskControlStrategy) -> VolatilityMetrics:
    latest_close = _decimal_attr(rows[-1], "close_price")
    true_ranges = _true_ranges(rows[-(strategy.atr_period + 1) :])
    atr_value = sum(true_ranges, Decimal("0")) / Decimal(len(true_ranges)) if true_ranges else Decimal("0")
    atr_pct = _safe_ratio(atr_value, latest_close)
    ranges = tuple(_range_pct(row) for row in rows[-strategy.atr_period :])
    latest_bar_range_pct = ranges[-1] if ranges else Decimal("0")
    average_range_pct = sum(ranges, Decimal("0")) / Decimal(len(ranges)) if ranges else Decimal("0")
    range_expansion_ratio = _safe_ratio(latest_bar_range_pct, average_range_pct)
    recent_range_pct = _window_range_pct(rows[-strategy.atr_period :])
    wick_risk_score = max(_upper_wick_ratio(rows[-1]), _lower_wick_ratio(rows[-1]))
    if atr_pct >= strategy.extreme_atr_pct or range_expansion_ratio >= strategy.extreme_range_expansion_ratio:
        state = "extreme_volatility"
    elif atr_pct >= strategy.high_atr_pct or range_expansion_ratio >= strategy.high_range_expansion_ratio:
        state = "high_volatility"
    elif atr_pct <= strategy.high_atr_pct / Decimal("3"):
        state = "low_volatility"
    else:
        state = "normal_volatility"
    return VolatilityMetrics(state, atr_value, atr_pct, recent_range_pct, average_range_pct, range_expansion_ratio, latest_bar_range_pct, wick_risk_score)


def _space_metrics(
    key_levels: tuple[Mapping[str, Any], ...],
    latest_close: Decimal,
    strategy: VolatilityRiskControlStrategy,
) -> SpaceMetrics:
    if not key_levels:
        return _unknown_space()
    supports = tuple(level for level in key_levels if _level_side(level) == "support")
    resistances = tuple(level for level in key_levels if _level_side(level) == "resistance")
    nearest_support = _nearest_below(supports, latest_close)
    nearest_resistance = _nearest_above(resistances, latest_close)
    long_room = _room_to_resistance(nearest_resistance, latest_close)
    long_risk = _risk_to_support(nearest_support, latest_close)
    short_room = _room_to_support(nearest_support, latest_close)
    short_risk = _risk_to_resistance(nearest_resistance, latest_close)
    long_ratio = _reward_risk(long_room, long_risk)
    short_ratio = _reward_risk(short_room, short_risk)
    return SpaceMetrics(
        long_feasibility=_feasibility(long_room, long_risk, long_ratio, strategy),
        short_feasibility=_feasibility(short_room, short_risk, short_ratio, strategy),
        long_room_to_resistance_pct=long_room,
        long_risk_to_support_pct=long_risk,
        short_room_to_support_pct=short_room,
        short_risk_to_resistance_pct=short_risk,
        rough_long_reward_risk_ratio=long_ratio,
        rough_short_reward_risk_ratio=short_ratio,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
    )


def _decide_gate(
    *,
    market_context: MarketContext,
    trigger_context: TriggerContext,
    volatility: VolatilityMetrics,
    space: SpaceMetrics,
    policy_name: str,
    strategy: VolatilityRiskControlStrategy,
    latest_close: Decimal,
) -> GateDecision:
    chase_risk = _chase_risk(trigger_context, latest_close, strategy)
    global_market_risk = _global_market_risk(volatility, market_context)
    candidate_direction = _candidate_direction(trigger_context)
    candidate_risk = _candidate_risk(trigger_context, volatility, chase_risk, space, candidate_direction)
    is_countertrend = _is_countertrend_candidate(market_context, candidate_direction)
    phase_requires_caution = _phase_requires_caution(market_context.regime_phase)
    reason_codes = [
        f"profile_{policy_name}",
        f"volatility_{volatility.volatility_state}",
        f"chase_{chase_risk}",
        f"candidate_direction_{candidate_direction}",
    ]
    if phase_requires_caution:
        reason_codes.append("regime_phase_requires_caution")
    if volatility.volatility_state == "extreme_volatility":
        decision = _profile_value(strategy, policy_name, "extreme_action", "block_all_candidates")
        scope = "all_candidates" if decision == "block_all_candidates" else "current_candidate"
        reason_codes.append("extreme_volatility_gate")
    elif trigger_context.trigger_state in {"false_breakout", "false_breakdown"} or trigger_context.filter_decision == "blocked":
        decision = _profile_value(strategy, policy_name, "false_trigger_action", "block_current_candidate")
        scope = "current_candidate"
        reason_codes.append("trigger_rejected_by_23d")
    elif is_countertrend:
        configured_action = _profile_value(strategy, policy_name, "countertrend_action", "")
        decision = configured_action or "wait"
        if decision == "block_all_candidates":
            decision = "block_current_candidate"
            reason_codes.append("countertrend_block_all_downgraded_to_current")
        scope = _scope_for_decision(decision, candidate_direction)
        if decision.startswith("block"):
            reason_codes.append("countertrend_candidate_blocked")
        else:
            reason_codes.append("countertrend_candidate_wait")
    elif _risk_exceeds(chase_risk, _profile_value(strategy, policy_name, "max_chase_risk", "medium")):
        decision = _profile_value(strategy, policy_name, "excessive_chase_action", "wait")
        scope = "current_candidate"
        reason_codes.append("chase_risk_exceeds_profile")
    elif candidate_direction == "long" and space.long_feasibility in {"poor", "invalid"}:
        decision = _profile_value(strategy, policy_name, "poor_space_action", "block_long_candidate")
        scope = "long_only"
        reason_codes.append("long_space_insufficient")
    elif candidate_direction == "short" and space.short_feasibility in {"poor", "invalid"}:
        decision = _profile_value(strategy, policy_name, "poor_space_action", "block_short_candidate")
        scope = "short_only"
        reason_codes.append("short_space_insufficient")
    elif _breakout_requires_volume(trigger_context, policy_name, strategy):
        decision = "wait"
        scope = "current_candidate"
        reason_codes.append("volume_confirmation_required")
    else:
        decision = _profile_value(strategy, policy_name, "default_decision", "wait")
        if phase_requires_caution and decision == "allow":
            decision = "allow_with_caution"
            reason_codes.append("regime_phase_caution_applied")
        scope = "current_candidate" if decision not in {"unknown", "insufficient_context"} else "unknown"
        reason_codes.append(f"default_decision_{decision}")
    confidence = _confidence(global_market_risk, candidate_risk, decision)
    return GateDecision(decision, scope, global_market_risk, candidate_risk, chase_risk, tuple(reason_codes), confidence, {
        "candidate_direction": candidate_direction,
        "is_countertrend_candidate": is_countertrend,
        "primary_regime": market_context.primary_regime,
        "regime_phase": market_context.regime_phase,
        "trend_strength": market_context.trend_strength,
        "decision_implication": market_context.decision_implication,
    })


def _insufficient_context_gate(
    volatility: VolatilityMetrics,
    policy_name: str,
    missing_context: tuple[str, ...],
) -> GateDecision:
    global_risk = "insufficient_data" if volatility.volatility_state == "insufficient_data" else "unknown"
    return GateDecision(
        "insufficient_context",
        "unknown",
        global_risk,
        "unknown",
        "unknown",
        ("insufficient_context",) + missing_context + (f"profile_{policy_name}",),
        Decimal("0.18"),
        {"missing_context": list(missing_context)},
    )


def _risk_reason_text(gate: GateDecision, market_context: MarketContext) -> str:
    """Build a Chinese public summary for later aggregation and review."""

    direction = str(gate.scoring_details.get("candidate_direction", "unknown"))
    direction_text = {"long": "多头", "short": "空头"}.get(direction, "未知方向")
    if "countertrend_candidate_blocked" in gate.reason_codes:
        return (
            f"当前候选方向为{direction_text}，与 23B 公开市场背景 "
            f"{market_context.primary_regime}/{market_context.regime_phase} 相反，风控层阻断当前候选。"
        )
    if "countertrend_candidate_wait" in gate.reason_codes:
        return (
            f"当前候选方向为{direction_text}，与 23B 公开市场背景 "
            f"{market_context.primary_regime}/{market_context.regime_phase} 相反；"
            "当前 profile 未配置明确逆势动作，风控层降级为等待。"
        )
    if "insufficient_context" in gate.reason_codes:
        return "23E 风控所需的公开上下文不足，不能默认放行，当前仅输出保守等待证据。"
    if "extreme_volatility_gate" in gate.reason_codes:
        return "当前波动率处于极端状态，风控层阻断或暂停候选推进；这不是最终交易建议。"
    if "trigger_rejected_by_23d" in gate.reason_codes:
        return "23D 公开触发证据已被拒绝或识别为假突破风险，23E 风控阻断当前候选。"
    if "chase_risk_exceeds_profile" in gate.reason_codes:
        return "当前候选存在追单风险，超过所选风控 profile 的允许阈值，风控层建议等待。"
    if "long_space_insufficient" in gate.reason_codes:
        return "当前多头方向净空间不足，23E 风控阻断或降级多头候选。"
    if "short_space_insufficient" in gate.reason_codes:
        return "当前空头方向净空间不足，23E 风控阻断或降级空头候选。"
    if "volume_confirmation_required" in gate.reason_codes:
        return "当前触发需要更强成交量确认，23E 风控建议等待新的公开证据。"
    return (
        f"23E 风控结论为 {gate.risk_gate_decision}，作用范围为 {gate.risk_scope}。"
        "该结果只作为风控证据，不是最终交易建议。"
    )


def _private_payload(
    *,
    market_context: MarketContext,
    trigger_context: TriggerContext,
    key_levels: tuple[Mapping[str, Any], ...],
    volatility: VolatilityMetrics,
    space: SpaceMetrics,
    gate: GateDecision,
    policy_name: str,
    policy_source: str,
    strategy: VolatilityRiskControlStrategy,
) -> Mapping[str, Any]:
    return {
        "atr_value": _price_text(volatility.atr_value),
        "atr_pct": _decimal_text(volatility.atr_pct),
        "recent_range_pct": _decimal_text(volatility.recent_range_pct),
        "average_range_pct": _decimal_text(volatility.average_range_pct),
        "range_expansion_ratio": _decimal_text(volatility.range_expansion_ratio),
        "latest_bar_range_pct": _decimal_text(volatility.latest_bar_range_pct),
        "wick_risk_score": _decimal_text(volatility.wick_risk_score),
        "distance_to_nearest_support_pct": _optional_decimal_text(space.short_room_to_support_pct),
        "distance_to_nearest_resistance_pct": _optional_decimal_text(space.long_room_to_resistance_pct),
        "long_room_to_resistance_pct": _optional_decimal_text(space.long_room_to_resistance_pct),
        "long_risk_to_support_pct": _optional_decimal_text(space.long_risk_to_support_pct),
        "short_room_to_support_pct": _optional_decimal_text(space.short_room_to_support_pct),
        "short_risk_to_resistance_pct": _optional_decimal_text(space.short_risk_to_resistance_pct),
        "rough_long_reward_risk_ratio": _optional_decimal_text(space.rough_long_reward_risk_ratio),
        "rough_short_reward_risk_ratio": _optional_decimal_text(space.rough_short_reward_risk_ratio),
        "fee_buffer_pct": _decimal_text(strategy.fee_buffer_pct),
        "slippage_buffer_pct": _decimal_text(strategy.slippage_buffer_pct),
        "min_net_room_pct": _decimal_text(strategy.min_net_room_pct),
        "risk_policy_mapping_details": {
            "selected_risk_policy_profile": policy_name,
            "policy_source": policy_source,
            "primary_regime": market_context.primary_regime,
            "regime_phase": market_context.regime_phase,
            "trend_strength": market_context.trend_strength,
            "decision_implication": market_context.decision_implication,
        },
        "risk_scoring_details": dict(gate.scoring_details),
        "calculation_params": {
            "atr_period": strategy.atr_period,
            "high_atr_pct": _decimal_text(strategy.high_atr_pct),
            "extreme_atr_pct": _decimal_text(strategy.extreme_atr_pct),
            "high_chase_distance_pct": _decimal_text(strategy.high_chase_distance_pct),
            "extreme_chase_distance_pct": _decimal_text(strategy.extreme_chase_distance_pct),
            "min_rough_reward_risk_ratio": _decimal_text(strategy.min_rough_reward_risk_ratio),
        },
        "public_context_snapshot": {
            "market_context_found": market_context.found,
            "key_level_count": len(key_levels),
            "trigger_context_found": trigger_context.found,
            "trigger_state": trigger_context.trigger_state,
            "filter_decision": trigger_context.filter_decision,
        },
    }


def _risk_flags(gate: GateDecision, reason: str, source: str) -> tuple[StrategyRiskFlag, ...]:
    return (
        StrategyRiskFlag("global_market_risk", _risk_flag_level(gate.global_market_risk), gate.global_market_risk != "normal", reason, source),
        StrategyRiskFlag("candidate_risk", _risk_flag_level(gate.candidate_risk), gate.candidate_risk in {"high", "extreme", "unknown"}, reason, source),
        StrategyRiskFlag("risk_gate_decision", _risk_flag_level(gate.candidate_risk), gate.risk_gate_decision not in {"allow", "allow_with_caution"}, reason, source),
    )


def _true_ranges(rows: tuple[Any, ...]) -> tuple[Decimal, ...]:
    result: list[Decimal] = []
    for index, row in enumerate(rows):
        high = _decimal_attr(row, "high_price")
        low = _decimal_attr(row, "low_price")
        previous_close = _decimal_attr(rows[index - 1], "close_price") if index > 0 else _decimal_attr(row, "close_price")
        result.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return tuple(result)


def _range_pct(row: Any) -> Decimal:
    return _safe_ratio(_decimal_attr(row, "high_price") - _decimal_attr(row, "low_price"), _decimal_attr(row, "close_price"))


def _window_range_pct(rows: tuple[Any, ...]) -> Decimal:
    if not rows:
        return Decimal("0")
    high = max(_decimal_attr(row, "high_price") for row in rows)
    low = min(_decimal_attr(row, "low_price") for row in rows)
    return _safe_ratio(high - low, _decimal_attr(rows[-1], "close_price"))


def _level_side(level: Mapping[str, Any]) -> str:
    group = str(level.get("level_group", ""))
    level_type = str(level.get("level_type", ""))
    role_flip_status = str(level.get("role_flip_status", "none"))
    if group == "role_flip_candidate":
        if role_flip_status == "resistance_to_support":
            return "support"
        if role_flip_status == "support_to_resistance":
            return "resistance"
        return "unknown"
    if group in {"nearest_support", "major_support", "range_lower_boundary"} or level_type in {"support", "invalidation_reference"}:
        return "support"
    if group in {"nearest_resistance", "major_resistance", "range_upper_boundary"} or level_type in {"resistance", "target_observation"}:
        return "resistance"
    return "unknown"


def _nearest_below(levels: tuple[Mapping[str, Any], ...], latest_close: Decimal) -> Mapping[str, Any] | None:
    candidates = [level for level in levels if _zone_high(level) <= latest_close]
    return max(candidates, key=_zone_high) if candidates else None


def _nearest_above(levels: tuple[Mapping[str, Any], ...], latest_close: Decimal) -> Mapping[str, Any] | None:
    candidates = [level for level in levels if _zone_low(level) >= latest_close]
    return min(candidates, key=_zone_low) if candidates else None


def _room_to_resistance(level: Mapping[str, Any] | None, latest_close: Decimal) -> Decimal | None:
    return None if level is None else _safe_ratio(_zone_low(level) - latest_close, latest_close)


def _risk_to_support(level: Mapping[str, Any] | None, latest_close: Decimal) -> Decimal | None:
    return None if level is None else _safe_ratio(latest_close - _zone_high(level), latest_close)


def _room_to_support(level: Mapping[str, Any] | None, latest_close: Decimal) -> Decimal | None:
    return None if level is None else _safe_ratio(latest_close - _zone_high(level), latest_close)


def _risk_to_resistance(level: Mapping[str, Any] | None, latest_close: Decimal) -> Decimal | None:
    return None if level is None else _safe_ratio(_zone_low(level) - latest_close, latest_close)


def _reward_risk(room: Decimal | None, risk: Decimal | None) -> Decimal | None:
    if room is None or risk is None or risk <= 0:
        return None
    return max(Decimal("0"), room) / risk


def _feasibility(
    room: Decimal | None,
    risk: Decimal | None,
    ratio: Decimal | None,
    strategy: VolatilityRiskControlStrategy,
) -> str:
    if room is None or risk is None or ratio is None:
        return "unknown"
    net_room = room - strategy.fee_buffer_pct - strategy.slippage_buffer_pct
    if room <= 0 or net_room <= 0:
        return "invalid"
    if net_room < strategy.min_net_room_pct or ratio < strategy.min_rough_reward_risk_ratio:
        return "poor"
    if ratio >= strategy.min_rough_reward_risk_ratio * Decimal("1.5") and net_room >= strategy.min_net_room_pct * Decimal("2"):
        return "favorable"
    return "acceptable"


def _chase_risk(trigger: TriggerContext, latest_close: Decimal, strategy: VolatilityRiskControlStrategy) -> str:
    if not trigger.found or not trigger.tested_level_summary:
        return "unknown"
    direction = _candidate_direction(trigger)
    tested = trigger.tested_level_summary
    if direction == "long":
        distance = _safe_ratio(latest_close - _zone_high(tested), _zone_high(tested))
    elif direction == "short":
        distance = _safe_ratio(_zone_low(tested) - latest_close, _zone_low(tested))
    else:
        return "unknown"
    if distance >= strategy.extreme_chase_distance_pct:
        return "extreme"
    if distance >= strategy.high_chase_distance_pct:
        return "high"
    if distance >= strategy.high_chase_distance_pct / Decimal("2"):
        return "medium"
    return "low"


def _candidate_direction(trigger: TriggerContext) -> str:
    state = trigger.trigger_state
    tested = trigger.tested_level_summary
    if state.startswith("breakout") or state == "false_breakout":
        return "long"
    if state.startswith("breakdown") or state == "false_breakdown":
        return "short"
    role_flip_status = str(tested.get("role_flip_status", "none"))
    if role_flip_status == "resistance_to_support":
        return "long"
    if role_flip_status == "support_to_resistance":
        return "short"
    level_type = str(tested.get("level_type", ""))
    group = str(tested.get("level_group", ""))
    if state.startswith("pullback"):
        if level_type == "support" or "support" in group or "lower" in group:
            return "long"
        if level_type == "resistance" or "resistance" in group or "upper" in group:
            return "short"
    if level_type == "resistance" or "resistance" in group or "upper" in group:
        return "long"
    if level_type == "support" or "support" in group or "lower" in group:
        return "short"
    return "unknown"


def _is_countertrend_candidate(market_context: MarketContext, candidate_direction: str) -> bool:
    if candidate_direction not in {"long", "short"}:
        return False
    bullish_context = market_context.primary_regime == "uptrend" or market_context.market_bias == "bullish_bias"
    bearish_context = market_context.primary_regime == "downtrend" or market_context.market_bias == "bearish_bias"
    if bullish_context:
        return candidate_direction == "short"
    if bearish_context:
        return candidate_direction == "long"
    return False


def _phase_requires_caution(regime_phase: str) -> bool:
    phase = regime_phase.lower()
    return any(token in phase for token in ("pullback", "rebound", "correction", "countertrend"))


def _scope_for_decision(decision: str, candidate_direction: str) -> str:
    if decision == "block_all_candidates":
        return "all_candidates"
    if decision == "block_long_candidate":
        return "long_only"
    if decision == "block_short_candidate":
        return "short_only"
    if decision in {"allow", "allow_with_caution", "wait", "block_current_candidate"}:
        return "current_candidate"
    if decision in {"insufficient_context", "unknown"}:
        return "unknown"
    if candidate_direction == "long":
        return "long_only"
    if candidate_direction == "short":
        return "short_only"
    return "unknown"


def _global_market_risk(volatility: VolatilityMetrics, market_context: MarketContext) -> str:
    if volatility.volatility_state == "extreme_volatility" or market_context.primary_regime == "volatile":
        return "extreme"
    if volatility.volatility_state == "high_volatility":
        return "high"
    if market_context.primary_regime in {"mixed", "unknown", "insufficient_data"}:
        return "elevated"
    return "normal"


def _candidate_risk(
    trigger: TriggerContext,
    volatility: VolatilityMetrics,
    chase_risk: str,
    space: SpaceMetrics,
    direction: str,
) -> str:
    if trigger.trigger_state in {"false_breakout", "false_breakdown"}:
        return "extreme"
    if trigger.filter_decision == "blocked":
        return "high"
    if chase_risk in {"high", "extreme"}:
        return chase_risk
    feasibility = space.long_feasibility if direction == "long" else space.short_feasibility if direction == "short" else "unknown"
    if feasibility in {"poor", "invalid"}:
        return "high"
    if volatility.volatility_state == "extreme_volatility":
        return "high"
    if volatility.volatility_state == "high_volatility" or trigger.volume_confirmation == "weakening":
        return "medium"
    if trigger.trigger_state in {"unknown", "no_clear_trigger", "insufficient_key_levels"}:
        return "unknown"
    return "low"


def _breakout_requires_volume(trigger: TriggerContext, policy_name: str, strategy: VolatilityRiskControlStrategy) -> bool:
    requires = str(_profile_value(strategy, policy_name, "breakout_requires_volume", "false")).lower() == "true"
    if not requires:
        return False
    return trigger.trigger_state in {"breakout_confirmed", "breakdown_confirmed"} and trigger.volume_confirmation != "confirming"


def _profile_value(strategy: VolatilityRiskControlStrategy, profile_name: str, key: str, default: str) -> str:
    return str(strategy.risk_policy_profiles.get(f"{profile_name}.{key}", default))


def _risk_exceeds(value: str, limit: str) -> bool:
    return _RISK_ORDER.get(value, 5) > _RISK_ORDER.get(limit, 2)


def _confidence(global_risk: str, candidate_risk: str, decision: str) -> Decimal:
    base = Decimal("0.68")
    if global_risk in {"extreme", "high"} or candidate_risk in {"extreme", "high"}:
        base += Decimal("0.12")
    if decision in {"insufficient_context", "unknown"}:
        return Decimal("0.18")
    if decision in {"allow", "allow_with_caution"}:
        base -= Decimal("0.08")
    return _clamp_unit(base)


def _common_risk_level(global_risk: str, candidate_risk: str) -> str:
    if "extreme" in {global_risk, candidate_risk}:
        return "extreme"
    if "high" in {global_risk, candidate_risk}:
        return "high"
    if global_risk == "elevated" or candidate_risk == "medium":
        return "medium"
    if global_risk == "unknown" or candidate_risk == "unknown":
        return "unknown"
    return "low"


def _risk_flag_level(value: str) -> str:
    if value == "normal":
        return "low"
    if value == "elevated":
        return "medium"
    if value in {"high", "extreme", "low", "medium", "unknown"}:
        return value
    return "unknown"


def _unknown_space() -> SpaceMetrics:
    return SpaceMetrics("insufficient_context", "insufficient_context", None, None, None, None, None, None, None, None)


def _code_suffix(reason_codes: tuple[str, ...], prefix: str) -> str | None:
    for item in reason_codes:
        if item.startswith(prefix):
            return item[len(prefix) :]
    return None


def _zone_low(level: Mapping[str, Any]) -> Decimal:
    return Decimal(str(level.get("zone_low") or level.get("price") or "0"))


def _zone_high(level: Mapping[str, Any]) -> Decimal:
    return Decimal(str(level.get("zone_high") or level.get("price") or "0"))


def _upper_wick_ratio(row: Any) -> Decimal:
    high = _decimal_attr(row, "high_price")
    low = _decimal_attr(row, "low_price")
    close = _decimal_attr(row, "close_price")
    open_price = _optional_decimal_attr(row, "open_price") or close
    return _safe_ratio(high - max(open_price, close), high - low)


def _lower_wick_ratio(row: Any) -> Decimal:
    high = _decimal_attr(row, "high_price")
    low = _decimal_attr(row, "low_price")
    close = _decimal_attr(row, "close_price")
    open_price = _optional_decimal_attr(row, "open_price") or close
    return _safe_ratio(min(open_price, close) - low, high - low)


def _decimal_attr(row: Any, field_name: str) -> Decimal:
    return Decimal(str(getattr(row, field_name)))


def _optional_decimal_attr(row: Any, field_name: str) -> Decimal | None:
    value = getattr(row, field_name, None)
    return None if value is None else Decimal(str(value))


def _safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return numerator / denominator


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _decimal_config(config: Mapping[str, Any], key: str, default: str) -> Decimal:
    return Decimal(str(config.get(key, default)))


def _clamp_unit(value: Decimal) -> Decimal:
    return min(Decimal("1"), max(Decimal("0"), value))


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _price_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


__all__ = ["VolatilityRiskControlStrategy"]
