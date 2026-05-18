"""Material-pack builder for stage-18 strategy aggregation.

This file belongs to `app/strategy/aggregation`. It builds the deterministic
analysis material pack from a restored MarketContextSnapshot window and an
already computed aggregation decision.

Called by: `app/strategy/aggregation/service.py`.

External services: none. MySQL: none in this file. Redis: none. Hermes: none.
DeepSeek/large models: none. Trading execution: none. Formal Kline impact:
none; all Kline rows are caller-provided read-only rows from the snapshot
restore contract. Stage 18 does not implement real strategies and does not
convert Kline indicators into executable long/short signals.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.strategy.aggregation.candidate_scenario_builder import (
    ANALYSIS_HYPOTHESIS_SEMANTICS,
    build_stage19_question_list,
    build_validation_plan,
)
from app.strategy.aggregation.indicators import (
    build_support_resistance_candidates,
    build_swing_structure,
    calculate_volatility_metrics,
    kline_summary,
    latest_close_price,
    max_open_time_ms,
    open_time_utc_text,
)
from app.strategy.aggregation.types import (
    AGGREGATION_VERSION,
    CANDIDATE_SCENARIO_VERSION,
    INDICATOR_VERSION,
    MATERIAL_SCHEMA_VERSION,
    AggregationDecision,
    MaterialPackBuildResult,
    StrategyVoteSummary,
)

MIN_BASE_KLINES_FOR_MATERIAL = 20
MIN_HIGHER_KLINES_FOR_MATERIAL = 1


def build_future_leakage_guard(restored_snapshot: Any) -> Mapping[str, Any]:
    """Build a read-only guard proving no post-snapshot Klines were used.

    Parameters: `restored_snapshot` from stage-15 repository restoration.
    Return value: JSON-ready guard including maximum row open times and
    snapshot window boundaries.
    Failure scenarios: malformed snapshot rows raise `ValueError`.
    External effects: none.
    """

    snapshot = restored_snapshot.snapshot
    max_base_open_time_ms = max_open_time_ms(restored_snapshot.rows_4h)
    max_higher_open_time_ms = max_open_time_ms(restored_snapshot.rows_1d)
    snapshot_base_end_ms = _optional_int(getattr(snapshot, "end_4h_open_time_ms", None))
    snapshot_higher_end_ms = _optional_int(getattr(snapshot, "end_1d_open_time_ms", None))
    base_uses_future = (
        max_base_open_time_ms is not None
        and snapshot_base_end_ms is not None
        and max_base_open_time_ms > snapshot_base_end_ms
    )
    higher_uses_future = (
        max_higher_open_time_ms is not None
        and snapshot_higher_end_ms is not None
        and max_higher_open_time_ms > snapshot_higher_end_ms
    )
    return {
        "max_base_open_time_used_ms": max_base_open_time_ms,
        "max_base_open_time_used_utc": open_time_utc_text(max_base_open_time_ms),
        "snapshot_target_base_open_time_ms": snapshot_base_end_ms,
        "snapshot_target_base_open_time_utc": open_time_utc_text(snapshot_base_end_ms),
        "max_higher_open_time_used_ms": max_higher_open_time_ms,
        "max_higher_open_time_used_utc": open_time_utc_text(max_higher_open_time_ms),
        "snapshot_target_higher_open_time_ms": snapshot_higher_end_ms,
        "snapshot_target_higher_open_time_utc": open_time_utc_text(snapshot_higher_end_ms),
        "uses_future_klines": bool(base_uses_future or higher_uses_future),
        "base_uses_future_klines": bool(base_uses_future),
        "higher_uses_future_klines": bool(higher_uses_future),
    }


def build_material_pack(
    *,
    strategy_signal_run: Any,
    strategy_signal_results: tuple[Any, ...],
    restored_snapshot: Any,
    vote_summary: StrategyVoteSummary,
    decision: AggregationDecision,
    candidate_scenarios_json: Mapping[str, Any],
) -> MaterialPackBuildResult:
    """Build deterministic material JSON for the next analysis layer.

    Parameters: stage-16 run/result rows, restored snapshot Kline windows,
    normalized vote summary, aggregation decision, and candidate scenarios.
    Return value: all JSON-ready payload sections for `analysis_material_pack`.
    Failure scenarios: insufficient Klines or malformed Kline rows raise
    `ValueError`; the stage-18 service converts those to blocked/failed
    results as appropriate.
    External effects: none.
    """

    rows_4h = tuple(restored_snapshot.rows_4h)
    rows_1d = tuple(restored_snapshot.rows_1d)
    if len(rows_4h) < MIN_BASE_KLINES_FOR_MATERIAL:
        raise ValueError("base Kline window is insufficient for stage-18 material pack")
    if len(rows_1d) < MIN_HIGHER_KLINES_FOR_MATERIAL:
        raise ValueError("higher Kline window is insufficient for stage-18 material pack")

    snapshot = restored_snapshot.snapshot
    latest_close = latest_close_price(rows_4h)
    swing_structure = build_swing_structure(
        rows_4h,
        interval_value=str(getattr(strategy_signal_run, "base_interval_value", "4h") or "4h"),
    )
    volatility = calculate_volatility_metrics(rows_4h)
    support_resistance = build_support_resistance_candidates(
        swing_structure=swing_structure,
        latest_close=latest_close,
    )
    data_window_json = _build_data_window_json(snapshot=snapshot, rows_4h=rows_4h, rows_1d=rows_1d)
    future_guard = build_future_leakage_guard(restored_snapshot)
    validation_plan = build_validation_plan()
    question_json = build_stage19_question_list()
    strategy_conflicts = _build_strategy_conflict_json(vote_summary=vote_summary, decision=decision)
    opposing_evidence = _collect_opposing_evidence(candidate_scenarios_json)

    material_json: Mapping[str, Any] = {
        "material_schema_version": MATERIAL_SCHEMA_VERSION,
        "aggregation_version": AGGREGATION_VERSION,
        "indicator_version": INDICATOR_VERSION,
        "candidate_scenario_version": CANDIDATE_SCENARIO_VERSION,
        "strategy_signal_run_id": getattr(strategy_signal_run, "run_id", ""),
        "snapshot_id": getattr(strategy_signal_run, "snapshot_id", ""),
        "symbol": getattr(strategy_signal_run, "symbol", ""),
        "base_interval": getattr(strategy_signal_run, "base_interval_value", ""),
        "higher_interval": getattr(strategy_signal_run, "higher_interval_value", ""),
        "kline_window_summary": {
            "latest_base_kline": kline_summary(rows_4h[-1]),
            "latest_higher_kline": kline_summary(rows_1d[-1]),
            "recent_base_klines_summary": [kline_summary(row) for row in rows_4h[-6:]],
            "recent_higher_klines_summary": [kline_summary(row) for row in rows_1d[-6:]],
            "base_window_count": len(rows_4h),
            "higher_window_count": len(rows_1d),
        },
        "swing": {
            "recent_swing_highs": [
                point.as_dict(latest_close=latest_close) for point in swing_structure.recent_swing_highs
            ],
            "recent_swing_lows": [
                point.as_dict(latest_close=latest_close) for point in swing_structure.recent_swing_lows
            ],
            "structure_labels": list(swing_structure.structure_labels),
            "structure_state": swing_structure.structure_state,
        },
        "volatility": {
            "atr_14": _maybe_float(volatility.atr_14),
            "atr_percent": _maybe_float(volatility.atr_percent),
            "avg_range_percent_3": _maybe_float(volatility.avg_range_percent_3),
            "avg_range_percent_6": _maybe_float(volatility.avg_range_percent_6),
            "avg_range_percent_20": _maybe_float(volatility.avg_range_percent_20),
            "range_expansion_state": volatility.range_expansion_state,
            "volatility_state": volatility.volatility_state,
        },
        "support_resistance": support_resistance,
        "analysis_hypothesis_direction": decision.analysis_hypothesis_direction.value,
        "analysis_hypothesis_semantics": ANALYSIS_HYPOTHESIS_SEMANTICS,
        "direction_projection_source": candidate_scenarios_json.get("direction_projection_source"),
        "stop_trading_source": candidate_scenarios_json.get("stop_trading_source"),
        "risk_gate_projection_source": candidate_scenarios_json.get("risk_gate_projection_source"),
        "hypothesis_invalidation_check": _first_scenario_value(
            candidate_scenarios_json,
            "invalidation_check",
        ),
        "hypothesis_target_observation_zone": _first_scenario_value(
            candidate_scenarios_json,
            "target_observation_zone",
        ),
        "context_upside_downside_ratio": _first_scenario_value(
            candidate_scenarios_json,
            "context_upside_downside_ratio",
        ),
        "context_upside_downside_ratio_semantics": _first_scenario_value(
            candidate_scenarios_json,
            "context_upside_downside_ratio_semantics",
        ),
        "strategy_conflict_points": strategy_conflicts,
        "opposing_evidence": opposing_evidence,
        "question_list_for_stage19": question_json,
        "boundary": {
            "deterministic_material_only": True,
            "analysis_hypothesis_direction_only": True,
            "analysis_hypothesis_direction_is_analysis_hypothesis_only": True,
            "is_strategy_signal": False,
            "is_trading_advice": False,
            "is_executable": False,
            "strategy_logic_implemented": False,
            "promotion_allowed": False,
            "promotion_requires_future_strategy_and_llm_stage": True,
            "no_large_model_call": True,
            "no_automatic_trading": True,
        },
    }

    summary_json = {
        "analysis_hypothesis_direction": decision.analysis_hypothesis_direction.value,
        "risk_level": decision.risk_level.value,
        "risk_gate_status": decision.risk_gate_status.value,
        "conflict_level": decision.conflict_level.value,
        "structure_state": swing_structure.structure_state,
        "volatility_state": volatility.volatility_state,
        "effective_strategy_count": vote_summary.effective_strategy_count,
        "strategy_signal_result_count": len(strategy_signal_results),
    }
    return MaterialPackBuildResult(
        material_json=material_json,
        question_json=question_json,
        validation_plan_json=validation_plan,
        data_window_json=data_window_json,
        future_leakage_guard_json=future_guard,
        summary_json=summary_json,
    )


def _build_data_window_json(*, snapshot: Any, rows_4h: tuple[Any, ...], rows_1d: tuple[Any, ...]) -> Mapping[str, Any]:
    return {
        "base_interval": getattr(snapshot, "base_interval_value", "4h"),
        "base_open_time_start_ms": getattr(snapshot, "start_4h_open_time_ms", None),
        "base_open_time_start_utc": open_time_utc_text(getattr(snapshot, "start_4h_open_time_ms", None)),
        "base_open_time_end_ms": getattr(snapshot, "end_4h_open_time_ms", None),
        "base_open_time_end_utc": open_time_utc_text(getattr(snapshot, "end_4h_open_time_ms", None)),
        "base_kline_count": len(rows_4h),
        "higher_interval": getattr(snapshot, "higher_interval_value", "1d"),
        "higher_open_time_start_ms": getattr(snapshot, "start_1d_open_time_ms", None),
        "higher_open_time_start_utc": open_time_utc_text(getattr(snapshot, "start_1d_open_time_ms", None)),
        "higher_open_time_end_ms": getattr(snapshot, "end_1d_open_time_ms", None),
        "higher_open_time_end_utc": open_time_utc_text(getattr(snapshot, "end_1d_open_time_ms", None)),
        "higher_kline_count": len(rows_1d),
        "source_tables": ["market_kline_4h", "market_kline_1d"],
        "source_snapshot_id": getattr(snapshot, "snapshot_id", ""),
    }


def _build_strategy_conflict_json(
    *,
    vote_summary: StrategyVoteSummary,
    decision: AggregationDecision,
) -> Mapping[str, Any]:
    return {
        "conflict_level": decision.conflict_level.value,
        "direction_consensus": decision.direction_consensus,
        "long_strategy_count": len(vote_summary.long_strategies),
        "short_strategy_count": len(vote_summary.short_strategies),
        "neutral_strategy_count": len(vote_summary.neutral_strategies),
        "risk_strategy_count": len(vote_summary.risk_strategies),
        "not_implemented_strategy_count": len(vote_summary.not_implemented_strategies),
        "failed_strategy_count": len(vote_summary.failed_strategies),
        "invalid_strategy_count": len(vote_summary.invalid_strategies),
        "analysis_hypothesis_only": True,
        "why_stage18_is_not_strategy_logic": (
            "Stage 18 only stores analysis hypotheses projected from existing "
            "stage-16 rows. Later strategy, model, and advice lifecycle stages "
            "have not run."
        ),
        "is_strategy_signal": False,
        "is_trading_advice": False,
        "is_executable": False,
        "strategy_logic_implemented": False,
        "why_hypothesis_is_not_execution_decision": (
            "Stage 18 only stores analysis hypotheses for later review; "
            "model analysis and advice lifecycle stages have not run."
        ),
    }


def _collect_opposing_evidence(candidate_scenarios_json: Mapping[str, Any]) -> list[Any]:
    scenarios = candidate_scenarios_json.get("candidate_scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return []
    first = scenarios[0]
    if not isinstance(first, Mapping):
        return []
    value = first.get("opposing_evidence")
    return list(value) if isinstance(value, list) else []


def _first_scenario_value(candidate_scenarios_json: Mapping[str, Any], key: str) -> Any:
    scenarios = candidate_scenarios_json.get("candidate_scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return None
    first = scenarios[0]
    if not isinstance(first, Mapping):
        return None
    return first.get(key)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _maybe_float(value: Any | None) -> float | None:
    if value is None:
        return None
    return float(value)


__all__ = [
    "MIN_BASE_KLINES_FOR_MATERIAL",
    "build_future_leakage_guard",
    "build_material_pack",
]
