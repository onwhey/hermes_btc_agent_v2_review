"""Strategy result contract dataclasses for stage-23A.

This file belongs to `app/strategy/common`. It defines the shared strategy
result protocol that stage-16 strategies return before validation and
adaptation into existing `StrategySignal` rows.
It does not access external services, read or write MySQL, read or write Redis,
send Hermes, call DeepSeek or any large language model, read private trading
state, generate final advice, modify Kline tables, or perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from app.strategy.common.constants import (
    STRATEGY_COMMON_RESULT_SCHEMA_VERSION,
    STRATEGY_RESULT_CONTRACT_VERSION,
)


class StrategyRole(str, Enum):
    """Public role used to validate strategy result shape."""

    DIRECTIONAL = "directional"
    SUPPORT_RESISTANCE = "support_resistance"
    RISK_CONTROL = "risk_control"
    FILTER = "filter"
    CONTEXT = "context"
    PLACEHOLDER = "placeholder"


@dataclass(frozen=True)
class StrategyKeyLevel:
    """Public key-level observation, not an execution order."""

    level_type: str
    price: str | None = None
    zone_low: str | None = None
    zone_high: str | None = None
    strength: str | None = None
    source: str = ""
    timeframe: str = ""
    reason: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return _without_none(self.__dict__)


@dataclass(frozen=True)
class StrategyScenarioCandidate:
    """Public observation scenario used by later analysis layers only."""

    scenario_type: str
    direction_bias: str = "not_applicable"
    activation_condition: str = ""
    invalidation_condition: str = ""
    target_observation_zone: str = ""
    risk_boundary: str = ""
    observation_period_bars: int | None = None
    preliminary_reward_risk_ratio: str | None = None
    supporting_evidence: tuple[str, ...] = ()
    opposing_evidence: tuple[str, ...] = ()

    def to_jsonable(self) -> dict[str, Any]:
        return _without_none(
            {
                "scenario_type": self.scenario_type,
                "direction_bias": self.direction_bias,
                "activation_condition": self.activation_condition,
                "invalidation_condition": self.invalidation_condition,
                "target_observation_zone": self.target_observation_zone,
                "risk_boundary": self.risk_boundary,
                "observation_period_bars": self.observation_period_bars,
                "preliminary_reward_risk_ratio": self.preliminary_reward_risk_ratio,
                "supporting_evidence": list(self.supporting_evidence),
                "opposing_evidence": list(self.opposing_evidence),
            }
        )


@dataclass(frozen=True)
class StrategyRiskFlag:
    """Public risk flag emitted by a risk or context strategy."""

    risk_type: str
    risk_level: str
    triggered: bool
    reason: str
    source: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return _without_none(self.__dict__)


@dataclass(frozen=True)
class StrategyEvidenceItem:
    """Public evidence item preserving one strategy observation."""

    evidence_type: str
    direction: str
    strength: str
    description: str
    source: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return _without_none(self.__dict__)


@dataclass(frozen=True)
class StrategyCommonResult:
    """Common public payload shared by all strategy roles.

    The common layer validates this object. Strategy-specific private material
    must stay in `StrategyResult.strategy_payload_json`.
    """

    market_bias: str = "unknown"
    risk_level: str = "unknown"
    signal_strength: str = "0"
    confidence_score: str = "0"
    reason_codes: tuple[str, ...] = ()
    reason_text: str = ""
    key_levels: tuple[StrategyKeyLevel | Mapping[str, Any], ...] = ()
    scenario_candidates: tuple[StrategyScenarioCandidate | Mapping[str, Any], ...] = ()
    risk_flags: tuple[StrategyRiskFlag | Mapping[str, Any], ...] = ()
    evidence_items: tuple[StrategyEvidenceItem | Mapping[str, Any], ...] = ()
    observation_window: Mapping[str, Any] = field(default_factory=dict)
    not_trading_advice: bool = True
    schema_version: str = STRATEGY_COMMON_RESULT_SCHEMA_VERSION
    primary_regime: str | None = None
    regime_phase: str | None = None
    trend_strength: str | None = None
    decision_implication: str | None = None
    market_environment_context: str | None = None
    filter_status: str | None = None
    context_summary: str | None = None
    trigger_state: str | None = None
    filter_decision: str | None = None
    tested_level_summary: Mapping[str, Any] | None = None
    volume_state: str | None = None
    volume_confirmation: str | None = None
    risk_gate_decision: str | None = None
    risk_scope: str | None = None
    global_market_risk: str | None = None
    candidate_risk: str | None = None
    volatility_state: str | None = None
    chase_risk: str | None = None
    long_feasibility: str | None = None
    short_feasibility: str | None = None
    selected_risk_policy_profile: str | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return _without_none(
            {
                "schema_version": self.schema_version,
                "market_bias": self.market_bias,
                "risk_level": self.risk_level,
                "signal_strength": self.signal_strength,
                "confidence_score": self.confidence_score,
                "reason_codes": list(self.reason_codes),
                "reason_text": self.reason_text,
                "key_levels": [_item_to_jsonable(item) for item in self.key_levels],
                "scenario_candidates": [_item_to_jsonable(item) for item in self.scenario_candidates],
                "risk_flags": [_item_to_jsonable(item) for item in self.risk_flags],
                "evidence_items": [_item_to_jsonable(item) for item in self.evidence_items],
                "observation_window": dict(self.observation_window),
                "not_trading_advice": self.not_trading_advice,
                "primary_regime": self.primary_regime,
                "regime_phase": self.regime_phase,
                "trend_strength": self.trend_strength,
                "decision_implication": self.decision_implication,
                "market_environment_context": self.market_environment_context,
                "filter_status": self.filter_status,
                "context_summary": self.context_summary,
                "trigger_state": self.trigger_state,
                "filter_decision": self.filter_decision,
                "tested_level_summary": dict(self.tested_level_summary) if self.tested_level_summary else None,
                "volume_state": self.volume_state,
                "volume_confirmation": self.volume_confirmation,
                "risk_gate_decision": self.risk_gate_decision,
                "risk_scope": self.risk_scope,
                "global_market_risk": self.global_market_risk,
                "candidate_risk": self.candidate_risk,
                "volatility_state": self.volatility_state,
                "chase_risk": self.chase_risk,
                "long_feasibility": self.long_feasibility,
                "short_feasibility": self.short_feasibility,
                "selected_risk_policy_profile": self.selected_risk_policy_profile,
            }
        )


@dataclass(frozen=True)
class StrategyResult:
    """Three-section result returned by a strategy implementation.

    `common_result` is the only section understood by the shared layer.
    `strategy_model_material_json` and `strategy_payload_json` are bounded
    strategy-owned mappings; the common layer validates JSON size/hash but does
    not interpret their private fields.
    """

    strategy_name: str
    strategy_version: str
    strategy_role: str
    strategy_status: str
    common_result: StrategyCommonResult
    trace_id: str = ""
    contract_version: str = STRATEGY_RESULT_CONTRACT_VERSION
    strategy_model_material_json: Mapping[str, Any] = field(default_factory=dict)
    strategy_payload_json: Mapping[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "strategy_role": self.strategy_role,
            "strategy_status": self.strategy_status,
            "common_result": self.common_result.to_jsonable(),
            "strategy_model_material_json": dict(self.strategy_model_material_json),
            "strategy_payload_json": dict(self.strategy_payload_json),
            "trace_id": self.trace_id,
        }


def _item_to_jsonable(value: Any) -> Any:
    if hasattr(value, "to_jsonable") and callable(value.to_jsonable):
        return value.to_jsonable()
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _without_none(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


__all__ = [
    "StrategyCommonResult",
    "StrategyEvidenceItem",
    "StrategyKeyLevel",
    "StrategyResult",
    "StrategyRiskFlag",
    "StrategyRole",
    "StrategyScenarioCandidate",
]
