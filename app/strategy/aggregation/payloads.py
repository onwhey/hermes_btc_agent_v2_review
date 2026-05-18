"""Persistence payload builders for stage-18 aggregation.

This file belongs to `app/strategy/aggregation`. It converts service decisions
into typed persistence payloads and compact service results.

Called by: `app/strategy/aggregation/service.py`.

External services: none. MySQL: none. Redis: none. Hermes: none.
DeepSeek/large models: none. Trading execution: none. It does not request
market data, write formal Kline tables, or create final suggestions.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.strategy.aggregation.candidate_scenario_builder import build_validation_plan
from app.strategy.aggregation.rules import opposing_items, supporting_items
from app.strategy.aggregation.types import (
    AGGREGATION_VERSION,
    ANALYSIS_HYPOTHESIS_SEMANTICS,
    CANDIDATE_SCENARIO_VERSION,
    DIRECTION_PROJECTION_SOURCE,
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_SUCCESS,
    INDICATOR_VERSION,
    MATERIAL_SCHEMA_VERSION,
    RISK_GATE_PROJECTION_SOURCE,
    AggregationDecision,
    AggregationRiskLevel,
    AnalysisHypothesisConfidence,
    AnalysisHypothesisDirection,
    ConflictLevel,
    RiskGateStatus,
    StrategyAggregationPersistencePayload,
    StrategyAggregationRequest,
    StrategyAggregationResult,
    StrategyAggregationStatus,
    StrategyVoteSummary,
)


def build_success_payload(
    *,
    request: StrategyAggregationRequest,
    strategy_run: Any,
    vote_summary: StrategyVoteSummary,
    decision: AggregationDecision,
    status: StrategyAggregationStatus,
    aggregation_run_id: str,
    trace_id: str,
    candidate_scenarios_json: Mapping[str, Any],
    summary_json: Mapping[str, Any],
    evidence_json: Mapping[str, Any],
    conflict_json: Mapping[str, Any],
    validation_plan_json: Mapping[str, Any],
    message: str,
    hermes_enabled: bool,
) -> StrategyAggregationPersistencePayload:
    """Build a persistence payload for success/partial_success aggregation."""

    return StrategyAggregationPersistencePayload(
        aggregation_run_id=aggregation_run_id,
        strategy_signal_run_id=request.strategy_signal_run_id,
        snapshot_id=getattr(strategy_run, "snapshot_id", None),
        symbol=str(getattr(strategy_run, "symbol", request.symbol)),
        base_interval=str(getattr(strategy_run, "base_interval_value", request.base_interval_value)),
        higher_interval=str(getattr(strategy_run, "higher_interval_value", request.higher_interval_value)),
        aggregation_version=AGGREGATION_VERSION,
        material_schema_version=MATERIAL_SCHEMA_VERSION,
        indicator_version=INDICATOR_VERSION,
        candidate_scenario_version=CANDIDATE_SCENARIO_VERSION,
        status=status,
        input_strategy_count=int(getattr(strategy_run, "strategy_count", 0) or 0),
        input_success_count=int(getattr(strategy_run, "success_count", 0) or 0),
        input_failed_count=int(getattr(strategy_run, "failed_count", 0) or 0),
        input_invalid_count=int(getattr(strategy_run, "invalid_count", 0) or 0),
        input_not_implemented_count=int(getattr(strategy_run, "not_implemented_count", 0) or 0),
        effective_strategy_count=vote_summary.effective_strategy_count,
        analysis_hypothesis_direction=decision.analysis_hypothesis_direction.value,
        analysis_hypothesis_confidence=decision.analysis_hypothesis_confidence.value,
        analysis_hypothesis_semantics=ANALYSIS_HYPOTHESIS_SEMANTICS,
        direction_projection_source=DIRECTION_PROJECTION_SOURCE,
        stop_trading_source=_stop_trading_source_for_direction(decision.analysis_hypothesis_direction),
        risk_gate_projection_source=_risk_gate_projection_source_for_direction(decision.analysis_hypothesis_direction),
        is_strategy_signal=False,
        is_trading_advice=False,
        is_executable=False,
        strategy_logic_implemented=False,
        promotion_allowed=False,
        promotion_requires_future_strategy_and_llm_stage=True,
        risk_level=decision.risk_level.value,
        risk_gate_status=decision.risk_gate_status.value,
        conflict_level=decision.conflict_level.value,
        direction_consensus=decision.direction_consensus,
        long_strategies_json={"items": list(vote_summary.long_strategies)},
        short_strategies_json={"items": list(vote_summary.short_strategies)},
        neutral_strategies_json={"items": list(vote_summary.neutral_strategies)},
        supporting_strategies_json={"items": supporting_items(decision.analysis_hypothesis_direction, vote_summary)},
        opposing_strategies_json={"items": opposing_items(decision.analysis_hypothesis_direction, vote_summary)},
        risk_strategies_json={"items": list(vote_summary.risk_strategies)},
        not_implemented_strategies_json={"items": list(vote_summary.not_implemented_strategies)},
        failed_strategies_json={"items": list(vote_summary.failed_strategies)},
        invalid_strategies_json={"items": list(vote_summary.invalid_strategies)},
        candidate_scenarios_json=candidate_scenarios_json,
        summary_json=summary_json,
        evidence_json=evidence_json,
        conflict_json=conflict_json,
        validation_plan_json=validation_plan_json,
        message=message,
        error_code=None,
        error_message=None,
        trace_id=trace_id,
        trigger_source=request.trigger_source,
        created_by=request.created_by,
        hermes_enabled=hermes_enabled,
        hermes_status=None,
        hermes_message=None,
        hermes_error=None,
        hermes_sent_at_utc=None,
    )


def build_blocked_payload(
    *,
    request: StrategyAggregationRequest,
    strategy_run: Any,
    aggregation_run_id: str,
    trace_id: str,
    message: str,
    error_code: str,
    error_message: str | None,
    hermes_enabled: bool,
) -> StrategyAggregationPersistencePayload:
    """Build a persistence payload for a blocked aggregation audit row."""

    return StrategyAggregationPersistencePayload(
        aggregation_run_id=aggregation_run_id,
        strategy_signal_run_id=request.strategy_signal_run_id,
        snapshot_id=getattr(strategy_run, "snapshot_id", None),
        symbol=str(getattr(strategy_run, "symbol", request.symbol)),
        base_interval=str(getattr(strategy_run, "base_interval_value", request.base_interval_value)),
        higher_interval=str(getattr(strategy_run, "higher_interval_value", request.higher_interval_value)),
        aggregation_version=AGGREGATION_VERSION,
        material_schema_version=MATERIAL_SCHEMA_VERSION,
        indicator_version=INDICATOR_VERSION,
        candidate_scenario_version=CANDIDATE_SCENARIO_VERSION,
        status=StrategyAggregationStatus.BLOCKED,
        input_strategy_count=int(getattr(strategy_run, "strategy_count", 0) or 0),
        input_success_count=int(getattr(strategy_run, "success_count", 0) or 0),
        input_failed_count=int(getattr(strategy_run, "failed_count", 0) or 0),
        input_invalid_count=int(getattr(strategy_run, "invalid_count", 0) or 0),
        input_not_implemented_count=int(getattr(strategy_run, "not_implemented_count", 0) or 0),
        effective_strategy_count=0,
        analysis_hypothesis_direction=AnalysisHypothesisDirection.WAIT.value,
        analysis_hypothesis_confidence=AnalysisHypothesisConfidence.LOW.value,
        analysis_hypothesis_semantics=ANALYSIS_HYPOTHESIS_SEMANTICS,
        direction_projection_source=DIRECTION_PROJECTION_SOURCE,
        stop_trading_source=None,
        risk_gate_projection_source=None,
        is_strategy_signal=False,
        is_trading_advice=False,
        is_executable=False,
        strategy_logic_implemented=False,
        promotion_allowed=False,
        promotion_requires_future_strategy_and_llm_stage=True,
        risk_level=AggregationRiskLevel.UNKNOWN.value,
        risk_gate_status=RiskGateStatus.INSUFFICIENT_DATA.value,
        conflict_level=ConflictLevel.MEDIUM.value,
        direction_consensus="insufficient_data",
        long_strategies_json={"items": []},
        short_strategies_json={"items": []},
        neutral_strategies_json={"items": []},
        supporting_strategies_json={"items": []},
        opposing_strategies_json={"items": []},
        risk_strategies_json={"items": []},
        not_implemented_strategies_json={"items": []},
        failed_strategies_json={"items": []},
        invalid_strategies_json={"items": []},
        candidate_scenarios_json={"analysis_hypothesis_direction": "wait", "candidate_scenarios": []},
        summary_json={"blocked": True, "error_code": error_code},
        evidence_json={"items": []},
        conflict_json={"conflict_level": "medium", "reason": "insufficient_data"},
        validation_plan_json=build_validation_plan(),
        message=message,
        error_code=error_code,
        error_message=error_message,
        trace_id=trace_id,
        trigger_source=request.trigger_source,
        created_by=request.created_by,
        hermes_enabled=hermes_enabled,
        hermes_status=None,
        hermes_message=None,
        hermes_error=None,
        hermes_sent_at_utc=None,
    )


def build_result_from_decision(
    *,
    request: StrategyAggregationRequest,
    strategy_run: Any,
    vote_summary: StrategyVoteSummary,
    decision: AggregationDecision,
    status: StrategyAggregationStatus,
    aggregation_run_id: str,
    material_pack_id: str,
    trace_id: str,
    message: str,
) -> StrategyAggregationResult:
    """Build the compact service result for success/partial_success."""

    return StrategyAggregationResult(
        status=status,
        exit_code=EXIT_SUCCESS,
        aggregation_run_id=aggregation_run_id,
        material_pack_id=material_pack_id,
        strategy_signal_run_id=request.strategy_signal_run_id,
        trace_id=trace_id,
        snapshot_id=getattr(strategy_run, "snapshot_id", None),
        analysis_hypothesis_direction=decision.analysis_hypothesis_direction,
        analysis_hypothesis_confidence=decision.analysis_hypothesis_confidence,
        analysis_hypothesis_semantics=ANALYSIS_HYPOTHESIS_SEMANTICS,
        direction_projection_source=DIRECTION_PROJECTION_SOURCE,
        stop_trading_source=_stop_trading_source_for_direction(decision.analysis_hypothesis_direction),
        risk_gate_projection_source=_risk_gate_projection_source_for_direction(decision.analysis_hypothesis_direction),
        is_strategy_signal=False,
        is_trading_advice=False,
        is_executable=False,
        strategy_logic_implemented=False,
        promotion_allowed=False,
        promotion_requires_future_strategy_and_llm_stage=True,
        risk_level=decision.risk_level,
        risk_gate_status=decision.risk_gate_status,
        conflict_level=decision.conflict_level,
        input_strategy_count=int(getattr(strategy_run, "strategy_count", 0) or 0),
        input_success_count=int(getattr(strategy_run, "success_count", 0) or 0),
        input_failed_count=int(getattr(strategy_run, "failed_count", 0) or 0),
        input_invalid_count=int(getattr(strategy_run, "invalid_count", 0) or 0),
        input_not_implemented_count=int(getattr(strategy_run, "not_implemented_count", 0) or 0),
        effective_strategy_count=vote_summary.effective_strategy_count,
        message=message,
    )


def blocked_result(
    request: StrategyAggregationRequest,
    *,
    aggregation_run_id: str,
    material_pack_id: str | None,
    trace_id: str,
    snapshot_id: str | None = None,
    message: str,
    error_code: str,
    error_message: str | None = None,
) -> StrategyAggregationResult:
    """Build a compact blocked service result."""

    return StrategyAggregationResult(
        status=StrategyAggregationStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        aggregation_run_id=aggregation_run_id,
        material_pack_id=material_pack_id,
        strategy_signal_run_id=request.strategy_signal_run_id,
        trace_id=trace_id,
        snapshot_id=snapshot_id,
        analysis_hypothesis_direction=AnalysisHypothesisDirection.WAIT,
        analysis_hypothesis_confidence=AnalysisHypothesisConfidence.LOW,
        analysis_hypothesis_semantics=ANALYSIS_HYPOTHESIS_SEMANTICS,
        direction_projection_source=DIRECTION_PROJECTION_SOURCE,
        risk_level=AggregationRiskLevel.UNKNOWN,
        risk_gate_status=RiskGateStatus.INSUFFICIENT_DATA,
        conflict_level=ConflictLevel.MEDIUM,
        message=message,
        error_code=error_code,
        error_message=error_message,
    )


def failed_result(
    request: StrategyAggregationRequest,
    *,
    aggregation_run_id: str,
    material_pack_id: str | None,
    trace_id: str,
    message: str,
    snapshot_id: str | None = None,
    error_message: str,
) -> StrategyAggregationResult:
    """Build a compact failed service result."""

    return StrategyAggregationResult(
        status=StrategyAggregationStatus.FAILED,
        exit_code=EXIT_FAILED,
        aggregation_run_id=aggregation_run_id,
        material_pack_id=material_pack_id,
        strategy_signal_run_id=request.strategy_signal_run_id,
        trace_id=trace_id,
        snapshot_id=snapshot_id,
        message=message,
        error_message=error_message,
    )


def _stop_trading_source_for_direction(direction: AnalysisHypothesisDirection) -> str | None:
    if direction == AnalysisHypothesisDirection.STOP_TRADING:
        return RISK_GATE_PROJECTION_SOURCE
    return None


def _risk_gate_projection_source_for_direction(direction: AnalysisHypothesisDirection) -> str | None:
    if direction == AnalysisHypothesisDirection.STOP_TRADING:
        return RISK_GATE_PROJECTION_SOURCE
    return None


__all__ = [
    "blocked_result",
    "build_blocked_payload",
    "build_result_from_decision",
    "build_success_payload",
    "failed_result",
]
