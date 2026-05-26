"""Deterministic aggregation rules for stage-18.

This file belongs to `app/strategy/aggregation`. It classifies stage-16
result rows, projects already persisted direction labels into analysis
hypothesis directions, computes risk/conflict metadata, and builds evidence
groupings.

Called by: `app/strategy/aggregation/service.py`.

External services: none. MySQL: none. Redis: none. Hermes: none.
DeepSeek/large models: none. Trading execution: none. It does not request
market data, write formal Kline tables, implement real strategy classes, judge
long/short from Klines, or generate final suggestions.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from app.strategy.aggregation.indicators import (
    build_support_resistance_candidates,
    build_swing_structure,
    calculate_volatility_metrics,
)
from app.strategy.aggregation.types import (
    AggregationDecision,
    AggregationRiskLevel,
    AnalysisHypothesisDirection,
    AnalysisHypothesisConfidence,
    ConflictLevel,
    RiskGateStatus,
    StrategyAggregationStatus,
    StrategyVoteSummary,
)

EFFECTIVE_STRATEGY_STATUSES = frozenset({"success", "no_signal"})
RISK_ORDER = {
    "not_applicable": 0,
    "unknown": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "extreme": 4,
}


def classify_strategy_results(strategy_results: tuple[Any, ...]) -> StrategyVoteSummary:
    """Classify strategy result rows into directional/risk/quality groups."""

    long_items: list[Mapping[str, Any]] = []
    short_items: list[Mapping[str, Any]] = []
    neutral_items: list[Mapping[str, Any]] = []
    risk_items: list[Mapping[str, Any]] = []
    not_implemented_items: list[Mapping[str, Any]] = []
    failed_items: list[Mapping[str, Any]] = []
    invalid_items: list[Mapping[str, Any]] = []
    effective_count = 0
    long_strength = 0.0
    short_strength = 0.0
    max_risk = "unknown"

    for row in strategy_results:
        item = _strategy_result_item(row)
        status = item["strategy_status"]
        direction = item["direction_bias"]
        risk = item["risk_level"]
        strength = float(item["signal_strength"])
        if status == "not_implemented":
            not_implemented_items.append(item)
            continue
        if status == "failed":
            failed_items.append(item)
            continue
        if status == "invalid":
            invalid_items.append(item)
            continue
        if status not in EFFECTIVE_STRATEGY_STATUSES:
            neutral_items.append(item)
            continue

        effective_count += 1
        if _risk_rank(risk) > _risk_rank(max_risk):
            max_risk = risk
        if direction in {"bullish_bias", "long_bias", "bullish", "long"}:
            long_items.append(item)
            long_strength += strength
        elif direction in {"bearish_bias", "short_bias", "bearish", "short"}:
            short_items.append(item)
            short_strength += strength
        elif direction == "not_applicable" and risk not in {"not_applicable", "unknown"}:
            risk_items.append(item)
        else:
            neutral_items.append(item)

    return StrategyVoteSummary(
        effective_strategy_count=effective_count,
        long_strategies=tuple(long_items),
        short_strategies=tuple(short_items),
        neutral_strategies=tuple(neutral_items),
        risk_strategies=tuple(risk_items),
        not_implemented_strategies=tuple(not_implemented_items),
        failed_strategies=tuple(failed_items),
        invalid_strategies=tuple(invalid_items),
        long_strength=long_strength,
        short_strength=short_strength,
        max_risk_level=_aggregation_risk_level(max_risk),
    )


def build_aggregation_decision(summary: StrategyVoteSummary) -> AggregationDecision:
    """Project existing stage-16 direction rows into a stage-18 hypothesis."""

    risk_level = summary.max_risk_level
    conflict_level = _conflict_level(summary, risk_level=risk_level)
    risk_gate = _risk_gate(summary, risk_level=risk_level, conflict_level=conflict_level)
    direction_consensus = _direction_consensus(summary)

    if risk_gate == RiskGateStatus.INSUFFICIENT_DATA:
        direction = AnalysisHypothesisDirection.WAIT
    elif risk_gate == RiskGateStatus.BLOCKED_BY_VOLATILITY:
        direction = AnalysisHypothesisDirection.STOP_TRADING if risk_level == AggregationRiskLevel.EXTREME else AnalysisHypothesisDirection.WAIT
    elif risk_gate == RiskGateStatus.BLOCKED_BY_CONFLICT:
        direction = AnalysisHypothesisDirection.WAIT
    elif summary.long_strength > summary.short_strength and summary.long_strategies:
        direction = AnalysisHypothesisDirection.LONG
    elif summary.short_strength > summary.long_strength and summary.short_strategies:
        direction = AnalysisHypothesisDirection.SHORT
    else:
        direction = AnalysisHypothesisDirection.WAIT

    confidence = _analysis_hypothesis_confidence(summary, conflict_level=conflict_level, risk_gate=risk_gate)
    return AggregationDecision(
        analysis_hypothesis_direction=direction,
        analysis_hypothesis_confidence=confidence,
        risk_level=risk_level,
        risk_gate_status=risk_gate,
        conflict_level=conflict_level,
        direction_consensus=direction_consensus,
        message=_decision_message(direction, risk_gate=risk_gate, conflict_level=conflict_level),
    )


def build_support_resistance_probe(*, restored_snapshot: Any, latest_close: Decimal, strategy_run: Any) -> Mapping[str, Any]:
    """Build context for hypotheses without choosing a long/short direction."""

    swing = build_swing_structure(
        restored_snapshot.rows_4h,
        interval_value=str(getattr(strategy_run, "base_interval_value", "4h") or "4h"),
    )
    volatility = calculate_volatility_metrics(restored_snapshot.rows_4h)
    candidates = dict(build_support_resistance_candidates(swing_structure=swing, latest_close=latest_close))
    candidates["structure_state"] = swing.structure_state
    candidates["volatility_state"] = volatility.volatility_state
    return candidates


def aggregation_status_from_inputs(*, strategy_run: Any, vote_summary: StrategyVoteSummary) -> StrategyAggregationStatus:
    """Return success or partial_success based on input quality."""

    if str(getattr(strategy_run, "status", "")) == "partial_success":
        return StrategyAggregationStatus.PARTIAL_SUCCESS
    if vote_summary.failed_strategies or vote_summary.invalid_strategies or vote_summary.not_implemented_strategies:
        return StrategyAggregationStatus.PARTIAL_SUCCESS
    return StrategyAggregationStatus.SUCCESS


def build_evidence_json(vote_summary: StrategyVoteSummary) -> Mapping[str, Any]:
    """Return JSON-ready grouped evidence preserving independent strategy views."""

    return {
        "long_strategies": list(vote_summary.long_strategies),
        "short_strategies": list(vote_summary.short_strategies),
        "neutral_strategies": list(vote_summary.neutral_strategies),
        "risk_strategies": list(vote_summary.risk_strategies),
        "not_implemented_strategies": list(vote_summary.not_implemented_strategies),
        "failed_strategies": list(vote_summary.failed_strategies),
        "invalid_strategies": list(vote_summary.invalid_strategies),
        "analysis_hypothesis_direction_is_analysis_hypothesis_only": True,
        "is_strategy_signal": False,
        "is_trading_advice": False,
        "is_executable": False,
        "strategy_logic_implemented": False,
    }


def supporting_items(direction: AnalysisHypothesisDirection, summary: StrategyVoteSummary) -> list[Mapping[str, Any]]:
    """Return upstream rows supporting the analysis hypothesis projection."""

    if direction == AnalysisHypothesisDirection.LONG:
        return list(summary.long_strategies)
    if direction == AnalysisHypothesisDirection.SHORT:
        return list(summary.short_strategies)
    if direction == AnalysisHypothesisDirection.STOP_TRADING:
        return list(summary.risk_strategies)
    return list(summary.neutral_strategies) + list(summary.risk_strategies)


def opposing_items(direction: AnalysisHypothesisDirection, summary: StrategyVoteSummary) -> list[Mapping[str, Any]]:
    """Return upstream rows opposing or conflicting with the projection."""

    if direction == AnalysisHypothesisDirection.LONG:
        return list(summary.short_strategies)
    if direction == AnalysisHypothesisDirection.SHORT:
        return list(summary.long_strategies)
    return list(summary.long_strategies) + list(summary.short_strategies)


def _strategy_result_item(row: Any) -> Mapping[str, Any]:
    common_payload = _json_loads(getattr(row, "common_payload_json", "{}"), default={})
    if isinstance(common_payload, Mapping) and common_payload.get("schema_version"):
        return _strategy_result_item_from_common_payload(row, common_payload)
    return {
        "strategy_name": str(getattr(row, "strategy_name", "")),
        "strategy_version": str(getattr(row, "strategy_version", "")),
        "strategy_status": str(getattr(row, "strategy_status", "")),
        "direction_bias": str(getattr(row, "direction_bias", "")),
        "risk_level": str(getattr(row, "risk_level", "")),
        "signal_strength": _safe_strength(getattr(row, "signal_strength", 0)),
        "reason_codes": _json_loads(getattr(row, "reason_codes_json", "[]"), default=[]),
        "reason_text": str(getattr(row, "reason_text", "")),
        "metrics": _json_loads(getattr(row, "metrics_json", "{}"), default={}),
    }


def _strategy_result_item_from_common_payload(row: Any, common_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    legacy_metrics = _json_loads(getattr(row, "metrics_json", "{}"), default={})
    strategy_payload = _json_loads(getattr(row, "strategy_payload_json", "{}"), default={})
    strategy_role = getattr(row, "strategy_role", None)
    return {
        "strategy_name": str(getattr(row, "strategy_name", "")),
        "strategy_version": str(getattr(row, "strategy_version", "")),
        "strategy_status": str(getattr(row, "strategy_status", "")),
        "direction_bias": _common_market_bias_for_stage18(common_payload, row),
        "risk_level": str(common_payload.get("risk_level") or getattr(row, "risk_level", "")),
        "signal_strength": _safe_strength(common_payload.get("signal_strength", getattr(row, "signal_strength", 0))),
        "reason_codes": _list_or_empty(common_payload.get("reason_codes")),
        "reason_text": str(common_payload.get("reason_text") or getattr(row, "reason_text", "")),
        "metrics": {
            "common_payload": dict(common_payload),
            "legacy_metrics": legacy_metrics,
            "strategy_private_payload_summary": _private_payload_summary(strategy_payload),
        },
        "contract_version": getattr(row, "contract_version", None),
        "strategy_role": strategy_role,
        "common_payload_hash": getattr(row, "common_payload_hash", None),
    }


def _safe_strength(value: Any) -> float:
    try:
        parsed = float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, parsed))


def _common_market_bias_for_stage18(common_payload: Mapping[str, Any], row: Any) -> str:
    strategy_role = str(getattr(row, "strategy_role", "") or "")
    if strategy_role and strategy_role != "directional":
        return "not_applicable"
    normalized = str(common_payload.get("market_bias") or getattr(row, "direction_bias", "") or "")
    if normalized == "wait":
        return "neutral"
    return normalized


def _list_or_empty(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _private_payload_summary(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {"available": False}
    return {
        "available": bool(value),
        "top_level_keys": sorted(str(key) for key in value.keys())[:20],
        "participates_in_common_aggregation": False,
    }


def _json_loads(value: Any, *, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _risk_rank(value: str) -> int:
    return RISK_ORDER.get(str(value), 0)


def _aggregation_risk_level(value: str) -> AggregationRiskLevel:
    normalized = value if value in {"low", "medium", "high", "extreme"} else "unknown"
    return AggregationRiskLevel(normalized)


def _conflict_level(summary: StrategyVoteSummary, *, risk_level: AggregationRiskLevel) -> ConflictLevel:
    if summary.effective_strategy_count <= 0:
        return ConflictLevel.MEDIUM
    if summary.long_strategies and summary.short_strategies:
        count_diff = abs(len(summary.long_strategies) - len(summary.short_strategies))
        strength_gap = abs(summary.long_strength - summary.short_strength)
        if count_diff == 0 or strength_gap <= 0.25:
            return ConflictLevel.HIGH
        return ConflictLevel.MEDIUM
    if risk_level in {AggregationRiskLevel.HIGH, AggregationRiskLevel.EXTREME} and (
        summary.long_strategies or summary.short_strategies
    ):
        return ConflictLevel.MEDIUM
    if summary.long_strategies or summary.short_strategies:
        return ConflictLevel.LOW
    return ConflictLevel.NONE


def _risk_gate(
    summary: StrategyVoteSummary,
    *,
    risk_level: AggregationRiskLevel,
    conflict_level: ConflictLevel,
) -> RiskGateStatus:
    if summary.effective_strategy_count <= 0:
        return RiskGateStatus.INSUFFICIENT_DATA
    if risk_level in {AggregationRiskLevel.HIGH, AggregationRiskLevel.EXTREME}:
        return RiskGateStatus.BLOCKED_BY_VOLATILITY
    if conflict_level == ConflictLevel.HIGH:
        return RiskGateStatus.BLOCKED_BY_CONFLICT
    if risk_level == AggregationRiskLevel.MEDIUM or conflict_level == ConflictLevel.MEDIUM:
        return RiskGateStatus.CAUTION
    return RiskGateStatus.PASS


def _direction_consensus(summary: StrategyVoteSummary) -> str:
    if summary.long_strategies and not summary.short_strategies:
        return "long"
    if summary.short_strategies and not summary.long_strategies:
        return "short"
    if summary.long_strategies and summary.short_strategies:
        return "mixed"
    if summary.risk_strategies and not summary.neutral_strategies:
        return "risk_only"
    return "neutral"


def _analysis_hypothesis_confidence(
    summary: StrategyVoteSummary,
    *,
    conflict_level: ConflictLevel,
    risk_gate: RiskGateStatus,
) -> AnalysisHypothesisConfidence:
    if risk_gate in {RiskGateStatus.BLOCKED_BY_CONFLICT, RiskGateStatus.BLOCKED_BY_VOLATILITY}:
        return AnalysisHypothesisConfidence.LOW
    if conflict_level in {ConflictLevel.HIGH, ConflictLevel.MEDIUM}:
        return AnalysisHypothesisConfidence.LOW
    if max(summary.long_strength, summary.short_strength) >= 1.2 and summary.effective_strategy_count >= 2:
        return AnalysisHypothesisConfidence.HIGH
    if summary.long_strategies or summary.short_strategies:
        return AnalysisHypothesisConfidence.MEDIUM
    return AnalysisHypothesisConfidence.LOW


def _decision_message(
    direction: AnalysisHypothesisDirection,
    *,
    risk_gate: RiskGateStatus,
    conflict_level: ConflictLevel,
) -> str:
    return (
        f"Stage-18 aggregation projected analysis_hypothesis_direction={direction.value}; "
        f"risk_gate_status={risk_gate.value}; conflict_level={conflict_level.value}. "
        "This is an analysis hypothesis only, not a strategy signal or trading suggestion."
    )


__all__ = [
    "aggregation_status_from_inputs",
    "build_aggregation_decision",
    "build_evidence_json",
    "build_support_resistance_probe",
    "classify_strategy_results",
    "opposing_items",
    "supporting_items",
]
