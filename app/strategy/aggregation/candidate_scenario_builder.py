"""Analysis-hypothesis builder for stage-18 material packs.

This file belongs to `app/strategy/aggregation`. It projects already persisted
stage-16 direction labels into analysis hypotheses for the stage-19 material
pack.

Called by: `app/strategy/aggregation/service.py` and
`app/strategy/aggregation/material_builder.py`.

Stage 18 does not implement real strategies, does not judge long/short from
Klines, does not generate strategy signals, does not generate trading advice,
and does not produce executable instructions. The long/short/wait/stop_trading
values emitted here are hypothesis placeholders only. Real strategy classes
must be developed later as independent stages/modules/classes.

External services: none. MySQL: none. Redis: none. Hermes: none.
DeepSeek/large models: none. Trading execution: none. It never outputs entry,
exit, position size, leverage, order, or final suggestion fields.
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

ANALYSIS_HYPOTHESIS_SEMANTICS = "analysis_hypothesis_only"
ANALYSIS_HYPOTHESIS_SOURCE = "fixture_or_existing_signal_projection"


def build_candidate_scenarios(
    *,
    decision: AggregationDecision,
    vote_summary: StrategyVoteSummary,
    latest_close: Decimal,
    support_resistance: Mapping[str, object],
    structure_state: str,
    volatility_state: str,
) -> Mapping[str, Any]:
    """Build JSON-ready analysis hypotheses from deterministic inputs.

    Parameters: aggregation decision projected from existing stage-16 rows,
    normalized stage-16 vote summary, latest close, support/resistance context,
    and structure/volatility context.
    Return value: mapping with exactly one analysis hypothesis plus explicit
    boundary flags.
    Failure scenarios: malformed context prices are skipped, leaving a nullable
    observation ratio rather than failing the aggregation.
    External effects: none.
    """

    projected_direction = _project_direction_from_existing_signal(decision.candidate_direction, vote_summary)
    supporting = _supporting_projection_names(projected_direction, vote_summary)
    opposing = _opposing_evidence(projected_direction, vote_summary, structure_state, volatility_state, decision)
    risk_notes = _risk_notes(decision)

    if projected_direction == CandidateDirection.LONG:
        hypothesis = _build_long_hypothesis(
            latest_close=latest_close,
            support_resistance=support_resistance,
            supporting_evidence=supporting,
            opposing_evidence=opposing,
            risk_notes=risk_notes,
        )
    elif projected_direction == CandidateDirection.SHORT:
        hypothesis = _build_short_hypothesis(
            latest_close=latest_close,
            support_resistance=support_resistance,
            supporting_evidence=supporting,
            opposing_evidence=opposing,
            risk_notes=risk_notes,
        )
    elif projected_direction == CandidateDirection.STOP_TRADING:
        hypothesis = _build_stop_trading_hypothesis(
            supporting_evidence=supporting,
            opposing_evidence=opposing,
            risk_notes=risk_notes,
        )
    else:
        hypothesis = _build_wait_hypothesis(
            supporting_evidence=supporting,
            opposing_evidence=opposing,
            risk_notes=risk_notes,
        )

    return {
        "candidate_direction": projected_direction.value,
        "candidate_direction_semantics": ANALYSIS_HYPOTHESIS_SEMANTICS,
        "requested_candidate_direction": decision.candidate_direction.value,
        "candidate_direction_confidence": decision.candidate_direction_confidence.value,
        "risk_gate_status": decision.risk_gate_status.value,
        "conflict_level": decision.conflict_level.value,
        "direction_projection_source": ANALYSIS_HYPOTHESIS_SOURCE,
        "candidate_scenarios": [hypothesis],
        "boundary": _analysis_hypothesis_boundary(),
    }


def build_validation_plan() -> Mapping[str, Any]:
    """Return the keyed stage-18 validation plan for later evaluation."""

    return {
        "evaluation_horizons_base_bars": [1, 3, 6],
        "activation_check": (
            "Later layers may check whether already-closed future 4h bars make "
            "the analysis hypothesis worth reviewing."
        ),
        "invalidation_check": (
            "Later layers may check whether already-closed future 4h bars make "
            "the analysis hypothesis invalid as a research path."
        ),
        "floating_range_check": (
            "Later layers may estimate favorable/unfavorable movement for "
            "evaluation only; this is not an execution rule."
        ),
        "target_observation_check": (
            "Later layers may check whether the observation zone was touched or "
            "rejected for post-analysis review."
        ),
        "notes": (
            "Stage 18 only prepares a validation plan. It does not backtest, "
            "does not call a model, and does not generate final advice."
        ),
    }


def build_stage19_question_list() -> Mapping[str, Any]:
    """Build the deterministic question list consumed by the next analysis layer."""

    return {
        "question_schema_version": "stage19_question_v1",
        "questions": [
            "Does the projected analysis hypothesis deserve model review, or should it stay wait?",
            "Which evidence supports the projection, and which evidence weakens it?",
            "Is volatility too high for the hypothesis to be useful as analysis material?",
            "Are the support/resistance observations context only, or are they being over-interpreted?",
            "Which future strategy module would be required before this could become a real signal?",
            "Which later LLM/advice lifecycle checks are still missing?",
            "What would invalidate the hypothesis as analysis material?",
            "What is the strongest opposing evidence?",
            "Where could the deterministic aggregation be misleading?",
        ],
        "boundary": {
            "questions_only": True,
            "no_model_call_in_stage18": True,
            "candidate_direction_is_analysis_hypothesis_only": True,
            "is_strategy_signal": False,
            "is_trading_advice": False,
            "is_executable": False,
            "strategy_logic_implemented": False,
        },
    }


def _project_direction_from_existing_signal(
    requested_direction: CandidateDirection,
    vote_summary: StrategyVoteSummary,
) -> CandidateDirection:
    """Project only explicit upstream direction labels into a hypothesis.

    Stage 18 must not invent a long/short hypothesis from Klines, support /
    resistance, reward/risk, or other material-pack indicators. If the existing
    stage-16 rows do not explicitly provide the requested directional side, the
    safe output is wait.
    """

    if requested_direction == CandidateDirection.LONG and vote_summary.long_strategies:
        return CandidateDirection.LONG
    if requested_direction == CandidateDirection.SHORT and vote_summary.short_strategies:
        return CandidateDirection.SHORT
    if requested_direction == CandidateDirection.STOP_TRADING and vote_summary.risk_strategies:
        return CandidateDirection.STOP_TRADING
    if requested_direction == CandidateDirection.STOP_TRADING:
        return CandidateDirection.STOP_TRADING
    return CandidateDirection.WAIT


def _build_long_hypothesis(
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
    observation_ratio = _reward_risk_ratio(
        latest_close=latest_close,
        favorable_target=resistance_price,
        invalidation_level=support_price,
        direction="long",
    )
    scenario = _base_hypothesis(
        scenario_type="long_hypothesis",
        hypothesis_direction="long",
        supporting_evidence=supporting_evidence,
        opposing_evidence=opposing_evidence,
        risk_notes=risk_notes,
    )
    scenario.update(
        {
            "activation_check": _context_with_candidate(
                "Review only whether the upstream long-side projection remains plausible near resistance context.",
                resistance,
            ),
            "invalidation_check": _context_with_candidate(
                "Review only whether the upstream long-side projection becomes weak near support context.",
                support,
            ),
            "target_observation_zone": _zone_text(
                resistance,
                fallback="Observe recent swing-high and resistance context only.",
            ),
            "preliminary_reward_risk_ratio": _decimal_to_float(observation_ratio),
            "preliminary_reward_risk_ratio_semantics": "observation_math_only_not_strategy_signal",
            "support_resistance_context": _support_resistance_context(support=support, resistance=resistance),
        }
    )
    return scenario


def _build_short_hypothesis(
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
    observation_ratio = _reward_risk_ratio(
        latest_close=latest_close,
        favorable_target=support_price,
        invalidation_level=resistance_price,
        direction="short",
    )
    scenario = _base_hypothesis(
        scenario_type="short_hypothesis",
        hypothesis_direction="short",
        supporting_evidence=supporting_evidence,
        opposing_evidence=opposing_evidence,
        risk_notes=risk_notes,
    )
    scenario.update(
        {
            "activation_check": _context_with_candidate(
                "Review only whether the upstream short-side projection remains plausible near support context.",
                support,
            ),
            "invalidation_check": _context_with_candidate(
                "Review only whether the upstream short-side projection becomes weak near resistance context.",
                resistance,
            ),
            "target_observation_zone": _zone_text(
                support,
                fallback="Observe recent swing-low and support context only.",
            ),
            "preliminary_reward_risk_ratio": _decimal_to_float(observation_ratio),
            "preliminary_reward_risk_ratio_semantics": "observation_math_only_not_strategy_signal",
            "support_resistance_context": _support_resistance_context(support=support, resistance=resistance),
        }
    )
    return scenario


def _build_wait_hypothesis(
    *,
    supporting_evidence: list[str],
    opposing_evidence: list[str],
    risk_notes: list[str],
) -> Mapping[str, Any]:
    scenario = _base_hypothesis(
        scenario_type="wait_hypothesis",
        hypothesis_direction="wait",
        supporting_evidence=supporting_evidence,
        opposing_evidence=opposing_evidence,
        risk_notes=risk_notes,
    )
    scenario.update(
        {
            "activation_check": "Keep the analysis path neutral until an upstream stage explicitly provides direction.",
            "invalidation_check": "Wait remains valid while directional evidence is missing or conflicted.",
            "target_observation_zone": "Observe range/context only; no directional target is produced.",
            "preliminary_reward_risk_ratio": None,
            "preliminary_reward_risk_ratio_semantics": "not_applicable_to_wait_hypothesis",
        }
    )
    return scenario


def _build_stop_trading_hypothesis(
    *,
    supporting_evidence: list[str],
    opposing_evidence: list[str],
    risk_notes: list[str],
) -> Mapping[str, Any]:
    scenario = _base_hypothesis(
        scenario_type="stop_trading_hypothesis",
        hypothesis_direction="stop_trading",
        supporting_evidence=supporting_evidence,
        opposing_evidence=opposing_evidence,
        risk_notes=risk_notes,
    )
    scenario.update(
        {
            "activation_check": "Review risk context only; no directional analysis should be promoted.",
            "invalidation_check": "The stop-trading hypothesis expires only after later risk context normalizes.",
            "target_observation_zone": "Observe volatility and risk normalization only.",
            "preliminary_reward_risk_ratio": None,
            "preliminary_reward_risk_ratio_semantics": "not_applicable_to_stop_trading_hypothesis",
        }
    )
    return scenario


def _base_hypothesis(
    *,
    scenario_type: str,
    hypothesis_direction: str,
    supporting_evidence: list[str],
    opposing_evidence: list[str],
    risk_notes: list[str],
) -> dict[str, Any]:
    return {
        "scenario_type": scenario_type,
        "scenario_semantics": ANALYSIS_HYPOTHESIS_SEMANTICS,
        "hypothesis_direction": hypothesis_direction,
        "source": ANALYSIS_HYPOTHESIS_SOURCE,
        "projected_from_existing_stage16_signal": True,
        "supporting_evidence": supporting_evidence,
        "opposing_evidence": opposing_evidence,
        "risk_notes": risk_notes,
        "validation_plan": build_validation_plan(),
        **_analysis_hypothesis_boundary(),
    }


def _analysis_hypothesis_boundary() -> dict[str, Any]:
    return {
        "is_strategy_signal": False,
        "is_trading_advice": False,
        "is_executable": False,
        "strategy_logic_implemented": False,
        "promotion_allowed": False,
        "promotion_requires_future_strategy_and_llm_stage": True,
    }


def _supporting_projection_names(direction: CandidateDirection, summary: StrategyVoteSummary) -> list[str]:
    if direction == CandidateDirection.LONG:
        return _strategy_names(summary.long_strategies)
    if direction == CandidateDirection.SHORT:
        return _strategy_names(summary.short_strategies)
    if direction == CandidateDirection.STOP_TRADING:
        return _strategy_names(summary.risk_strategies) or ["risk_gate_projection"]
    names = _strategy_names(summary.neutral_strategies) + _strategy_names(summary.risk_strategies)
    if not names:
        names = ["stage18_wait_boundary"]
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
        evidence.extend(f"{name} projects the opposite or risk side" for name in _strategy_names(summary.short_strategies))
    elif direction == CandidateDirection.SHORT:
        evidence.extend(f"{name} projects the opposite or risk side" for name in _strategy_names(summary.long_strategies))
    else:
        evidence.extend(f"{name} projects long-side context" for name in _strategy_names(summary.long_strategies))
        evidence.extend(f"{name} projects short-side context" for name in _strategy_names(summary.short_strategies))
    if structure_state in {"range", "mixed", "insufficient_data"}:
        evidence.append(f"structure_state={structure_state}; direction confidence is limited")
    if volatility_state in {"expanded", "extreme"}:
        evidence.append(f"volatility_state={volatility_state}; hypothesis review is riskier")
    if decision.conflict_level in {ConflictLevel.MEDIUM, ConflictLevel.HIGH}:
        evidence.append(f"conflict_level={decision.conflict_level.value}")
    return evidence


def _risk_notes(decision: AggregationDecision) -> list[str]:
    notes = [
        "candidate_direction is an analysis hypothesis placeholder only.",
        "It is not a strategy signal, not trading advice, and not executable.",
        "Real strategy logic must be implemented later in independent strategy modules.",
    ]
    if decision.risk_gate_status != RiskGateStatus.PASS:
        notes.append(f"risk_gate_status={decision.risk_gate_status.value}")
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


def _context_with_candidate(base_text: str, candidate: Mapping[str, Any] | None) -> str:
    price = _candidate_price(candidate)
    if price is None:
        return base_text
    return f"{base_text} Context price: {_decimal_text(price)}."


def _zone_text(candidate: Mapping[str, Any] | None, *, fallback: str) -> str:
    price = _candidate_price(candidate)
    if price is None:
        return fallback
    return f"Observe context around {_decimal_text(price)}; this is not a target instruction."


def _support_resistance_context(
    *,
    support: Mapping[str, Any] | None,
    resistance: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    return {
        "support_context": support or {},
        "resistance_context": resistance or {},
        "semantics": "observation_context_only",
        "is_strategy_signal": False,
        "is_trading_advice": False,
    }


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
    "ANALYSIS_HYPOTHESIS_SEMANTICS",
    "ANALYSIS_HYPOTHESIS_SOURCE",
    "build_candidate_scenarios",
    "build_stage19_question_list",
    "build_validation_plan",
]
