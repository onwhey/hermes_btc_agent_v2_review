"""Candidate scenario builder for stage-18 strategy aggregation.

This file belongs to `app/strategy/aggregation`. It converts a deterministic
aggregation decision and market-material indicators into candidate scenarios
with activation conditions, invalidation conditions, target observation zones,
preliminary reward/risk estimates, evidence, opposing evidence, and validation
plans.

Called by: `app/strategy/aggregation/service.py` and
`app/strategy/aggregation/material_builder.py`.

External services: none. MySQL: none. Redis: none. Hermes: none.
DeepSeek/large models: none. Trading execution: none. It never outputs an
entry, exit, position size, leverage, order, or final suggestion field.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from app.strategy.aggregation.types import (
    AggregationDecision,
    CandidateDirection,
    ConflictLevel,
    RiskGateStatus,
    StrategyVoteSummary,
)


def build_candidate_scenarios(
    *,
    decision: AggregationDecision,
    vote_summary: StrategyVoteSummary,
    latest_close: Decimal,
    support_resistance: Mapping[str, object],
    structure_state: str,
    volatility_state: str,
) -> Mapping[str, Any]:
    """Build JSON-ready candidate scenarios from deterministic inputs.

    Parameters: aggregation decision, strategy votes, latest close, support /
    resistance candidates, and structural/volatility states.
    Return value: mapping with one or more candidate scenarios and warnings.
    Failure scenarios: malformed candidate prices are skipped, leaving a
    nullable reward/risk estimate rather than failing the aggregation.
    External effects: none.
    """

    direction = decision.candidate_direction
    supporting = _supporting_strategy_names(direction, vote_summary)
    opposing = _opposing_evidence(direction, vote_summary, structure_state, volatility_state, decision)
    risk_notes = _risk_notes(decision)

    if direction == CandidateDirection.LONG:
        scenario = _build_long_scenario(
            latest_close=latest_close,
            support_resistance=support_resistance,
            supporting_evidence=supporting,
            opposing_evidence=opposing,
            risk_notes=risk_notes,
        )
    elif direction == CandidateDirection.SHORT:
        scenario = _build_short_scenario(
            latest_close=latest_close,
            support_resistance=support_resistance,
            supporting_evidence=supporting,
            opposing_evidence=opposing,
            risk_notes=risk_notes,
        )
    elif direction == CandidateDirection.STOP_TRADING:
        scenario = _build_stop_trading_scenario(
            supporting_evidence=supporting,
            opposing_evidence=opposing,
            risk_notes=risk_notes,
        )
    else:
        scenario = _build_wait_scenario(
            supporting_evidence=supporting,
            opposing_evidence=opposing,
            risk_notes=risk_notes,
        )

    return {
        "candidate_direction": direction.value,
        "candidate_direction_confidence": decision.candidate_direction_confidence.value,
        "risk_gate_status": decision.risk_gate_status.value,
        "conflict_level": decision.conflict_level.value,
        "candidate_scenarios": [scenario],
        "boundary": {
            "candidate_direction_only": True,
            "not_a_trading_instruction": True,
            "no_large_model_call": True,
            "no_automatic_trading": True,
        },
    }


def build_validation_plan() -> Mapping[str, Any]:
    """Return the reusable stage-18 validation plan for later evaluation."""

    return {
        "evaluation_horizons_base_bars": [1, 3, 6],
        "activation_check": "以后续已收盘 4h K线判断候选成立条件是否被满足。",
        "invalidation_check": "以后续已收盘 4h K线判断候选失效条件是否被满足。",
        "floating_range_check": "以后续 K线 high/low 估算候选方向的最大有利与不利波动。",
        "target_observation_check": "检查候选目标观察区是否被触达或被明显拒绝。",
        "notes": "本阶段只生成验证计划，不执行复盘，也不生成最终建议。",
    }


def build_stage19_question_list() -> Mapping[str, Any]:
    """Build the deterministic question list consumed by the next analysis layer."""

    return {
        "question_schema_version": "stage19_question_v1",
        "questions": [
            "当前候选方向是否被价格结构支持？",
            "当前波动率是否支持候选失效条件的距离？",
            "当前目标观察区与候选失效条件之间的初步风险收益比是否合理？",
            "当前结构是否存在假突破或追涨追跌风险？",
            "多个策略是否真正独立，还是重复表达同一个趋势因子？",
            "如果策略信号与风控冲突，应优先等待还是停止交易？",
            "哪些条件必须成立，才允许候选方向从 wait 转为 long 或 short？",
            "当前候选场景的反方证据是否足以否决方向？",
            "如果当前候选判断错误，最可能错在哪里？",
        ],
        "boundary": {
            "questions_only": True,
            "no_model_call_in_stage18": True,
            "candidate_direction_is_not_an_execution_decision": True,
        },
    }


def _build_long_scenario(
    *,
    latest_close: Decimal,
    support_resistance: Mapping[str, object],
    supporting_evidence: list[str],
    opposing_evidence: list[str],
    risk_notes: list[str],
) -> Mapping[str, Any]:
    support = _nearest_candidate_below(support_resistance.get("support_candidates"), latest_close)
    resistance = _nearest_candidate_above(support_resistance.get("resistance_candidates"), latest_close)
    support_price = _candidate_price(support)
    resistance_price = _candidate_price(resistance)
    reward_risk = _reward_risk_ratio(
        latest_close=latest_close,
        favorable_target=resistance_price,
        invalidation_level=support_price,
        direction="long",
    )
    return {
        "scenario_type": "long_candidate",
        "activation_condition": _condition_with_candidate(
            "4h 收盘价重新站上最近压力候选上方，且后续回落不迅速跌回区间内。",
            resistance,
        ),
        "invalidation_condition": _condition_with_candidate(
            "4h 收盘价跌破最近支撑候选下方，则该多头候选场景失效。",
            support,
        ),
        "target_observation_zone": _zone_text(resistance, fallback="观察最近 swing high 至上方压力候选区域。"),
        "preliminary_reward_risk_ratio": _decimal_to_float(reward_risk),
        "supporting_evidence": supporting_evidence,
        "opposing_evidence": opposing_evidence,
        "risk_notes": risk_notes,
        "validation_plan": list(build_validation_plan().values())[:4],
    }


def _build_short_scenario(
    *,
    latest_close: Decimal,
    support_resistance: Mapping[str, object],
    supporting_evidence: list[str],
    opposing_evidence: list[str],
    risk_notes: list[str],
) -> Mapping[str, Any]:
    support = _nearest_candidate_below(support_resistance.get("support_candidates"), latest_close)
    resistance = _nearest_candidate_above(support_resistance.get("resistance_candidates"), latest_close)
    support_price = _candidate_price(support)
    resistance_price = _candidate_price(resistance)
    reward_risk = _reward_risk_ratio(
        latest_close=latest_close,
        favorable_target=support_price,
        invalidation_level=resistance_price,
        direction="short",
    )
    return {
        "scenario_type": "short_candidate",
        "activation_condition": _condition_with_candidate(
            "4h 收盘价跌破最近支撑候选下方，且后续反抽不能重新站回区间内。",
            support,
        ),
        "invalidation_condition": _condition_with_candidate(
            "4h 收盘价重新站上最近压力候选上方，则该空头候选场景失效。",
            resistance,
        ),
        "target_observation_zone": _zone_text(support, fallback="观察最近 swing low 至下方支撑候选区域。"),
        "preliminary_reward_risk_ratio": _decimal_to_float(reward_risk),
        "supporting_evidence": supporting_evidence,
        "opposing_evidence": opposing_evidence,
        "risk_notes": risk_notes,
        "validation_plan": list(build_validation_plan().values())[:4],
    }


def _build_wait_scenario(
    *,
    supporting_evidence: list[str],
    opposing_evidence: list[str],
    risk_notes: list[str],
) -> Mapping[str, Any]:
    return {
        "scenario_type": "wait_candidate",
        "activation_condition": "等待多空结构或风险状态进一步清晰，至少观察后续 1 到 3 根 4h 收盘。",
        "invalidation_condition": "如果风险门禁继续否决或多空冲突加剧，wait 候选继续有效。",
        "target_observation_zone": "观察最近支撑压力候选之间的震荡区间。",
        "preliminary_reward_risk_ratio": None,
        "supporting_evidence": supporting_evidence,
        "opposing_evidence": opposing_evidence,
        "risk_notes": risk_notes,
        "validation_plan": list(build_validation_plan().values())[:4],
    }


def _build_stop_trading_scenario(
    *,
    supporting_evidence: list[str],
    opposing_evidence: list[str],
    risk_notes: list[str],
) -> Mapping[str, Any]:
    return {
        "scenario_type": "stop_trading_candidate",
        "activation_condition": "波动率或风险门禁极端，候选场景要求先停止新的方向判断。",
        "invalidation_condition": "风险状态回落到可评估区间，且 4h 结构重新稳定后，该停止候选失效。",
        "target_observation_zone": "只观察风险回落和结构恢复，不观察方向目标。",
        "preliminary_reward_risk_ratio": None,
        "supporting_evidence": supporting_evidence,
        "opposing_evidence": opposing_evidence,
        "risk_notes": risk_notes,
        "validation_plan": list(build_validation_plan().values())[:4],
    }


def _supporting_strategy_names(direction: CandidateDirection, summary: StrategyVoteSummary) -> list[str]:
    if direction == CandidateDirection.LONG:
        return _strategy_names(summary.long_strategies)
    if direction == CandidateDirection.SHORT:
        return _strategy_names(summary.short_strategies)
    if direction == CandidateDirection.STOP_TRADING:
        return _strategy_names(summary.risk_strategies) or ["风险门禁否决"]
    names = _strategy_names(summary.neutral_strategies) + _strategy_names(summary.risk_strategies)
    if not names:
        names = ["聚合规则倾向等待"]
    return names


def _opposing_evidence(
    direction: CandidateDirection,
    summary: StrategyVoteSummary,
    structure_state: str,
    volatility_state: str,
    decision: AggregationDecision,
) -> list[str]:
    evidence: list[str] = []
    if direction == CandidateDirection.LONG:
        evidence.extend(f"{name} 支持空头或反向风险" for name in _strategy_names(summary.short_strategies))
    elif direction == CandidateDirection.SHORT:
        evidence.extend(f"{name} 支持多头或反向风险" for name in _strategy_names(summary.long_strategies))
    else:
        evidence.extend(f"{name} 支持多头" for name in _strategy_names(summary.long_strategies))
        evidence.extend(f"{name} 支持空头" for name in _strategy_names(summary.short_strategies))
    if structure_state in {"range", "mixed", "insufficient_data"}:
        evidence.append(f"价格结构为 {structure_state}，方向确认度不足")
    if volatility_state in {"expanded", "extreme"}:
        evidence.append(f"波动率状态为 {volatility_state}，候选条件更容易被噪音影响")
    if decision.conflict_level in {ConflictLevel.MEDIUM, ConflictLevel.HIGH}:
        evidence.append(f"策略冲突等级为 {decision.conflict_level.value}")
    return evidence


def _risk_notes(decision: AggregationDecision) -> list[str]:
    notes = [
        "candidate_direction 只是聚合层候选方向，不是最终建议。",
        "成立条件、失效条件和目标观察区只用于后续验证，不是操作指令。",
    ]
    if decision.risk_gate_status != RiskGateStatus.PASS:
        notes.append(f"风控门禁状态：{decision.risk_gate_status.value}")
    return notes


def _strategy_names(items: tuple[Mapping[str, Any], ...]) -> list[str]:
    return [str(item.get("strategy_name", "")) for item in items if str(item.get("strategy_name", ""))]


def _nearest_candidate_below(candidates: object, latest_close: Decimal) -> Mapping[str, Any] | None:
    parsed = _candidate_list(candidates)
    below = [item for item in parsed if _candidate_price(item) is not None and _candidate_price(item) < latest_close]
    if not below:
        return None
    return max(below, key=lambda item: _candidate_price(item) or Decimal("0"))


def _nearest_candidate_above(candidates: object, latest_close: Decimal) -> Mapping[str, Any] | None:
    parsed = _candidate_list(candidates)
    above = [item for item in parsed if _candidate_price(item) is not None and _candidate_price(item) > latest_close]
    if not above:
        return None
    return min(above, key=lambda item: _candidate_price(item) or Decimal("0"))


def _candidate_list(candidates: object) -> list[Mapping[str, Any]]:
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, Mapping)]


def _candidate_price(candidate: Mapping[str, Any] | None) -> Decimal | None:
    if candidate is None:
        return None
    try:
        return Decimal(str(candidate.get("price")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _condition_with_candidate(base_text: str, candidate: Mapping[str, Any] | None) -> str:
    price = _candidate_price(candidate)
    if price is None:
        return base_text
    return f"{base_text} 参考价位：{_decimal_text(price)}。"


def _zone_text(candidate: Mapping[str, Any] | None, *, fallback: str) -> str:
    price = _candidate_price(candidate)
    if price is None:
        return fallback
    return f"观察 {_decimal_text(price)} 附近的候选区域及其上/下方反应。"


def _reward_risk_ratio(
    *,
    latest_close: Decimal,
    favorable_target: Decimal | None,
    invalidation_level: Decimal | None,
    direction: str,
) -> Decimal | None:
    if favorable_target is None or invalidation_level is None or latest_close <= 0:
        return None
    if direction == "long":
        reward = favorable_target - latest_close
        risk = latest_close - invalidation_level
    else:
        reward = latest_close - favorable_target
        risk = invalidation_level - latest_close
    if reward <= 0 or risk <= 0:
        return None
    return (reward / risk).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


__all__ = [
    "build_candidate_scenarios",
    "build_stage19_question_list",
    "build_validation_plan",
]
