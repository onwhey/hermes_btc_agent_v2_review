from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

from app.strategy.aggregation.rules import classify_strategy_results
from app.strategy.common.result_contract import StrategyCommonResult, StrategyResult, StrategyRole, StrategyScenarioCandidate
from app.strategy.common.result_validator import validate_strategy_result


def test_stage18_prefers_common_payload_when_present() -> None:
    common_payload = StrategyCommonResult(
        market_bias="bullish_bias",
        risk_level="low",
        signal_strength="0.80",
        confidence_score="0.70",
        reason_codes=("common_contract_reason",),
        reason_text="Common contract row should drive stage18 classification.",
        scenario_candidates=(
            StrategyScenarioCandidate(
                scenario_type="long_candidate",
                direction_bias="bullish_bias",
                activation_condition="Observe continuation.",
                invalidation_condition="Observation weakens.",
                risk_boundary="Recent range boundary.",
                observation_period_bars=3,
            ),
        ),
    ).to_jsonable()
    result = StrategyResult(
        strategy_name="fixture_common",
        strategy_version="v1",
        strategy_role=StrategyRole.DIRECTIONAL.value,
        strategy_status="success",
        common_result=StrategyCommonResult(
            market_bias="bullish_bias",
            risk_level="low",
            signal_strength="0.80",
            confidence_score="0.70",
            reason_codes=("common_contract_reason",),
            reason_text="Common contract row should drive stage18 classification.",
            scenario_candidates=(
                StrategyScenarioCandidate(
                    scenario_type="long_candidate",
                    direction_bias="bullish_bias",
                    activation_condition="Observe continuation.",
                    invalidation_condition="Observation weakens.",
                    risk_boundary="Recent range boundary.",
                    observation_period_bars=3,
                ),
            ),
        ),
    )
    assert validate_strategy_result(result).passed is True
    row = SimpleNamespace(
        strategy_name="fixture_common",
        strategy_version="v1",
        strategy_status="success",
        direction_bias="neutral",
        risk_level="unknown",
        signal_strength=Decimal("0.10"),
        reason_codes_json=json.dumps(["legacy_reason"]),
        reason_text="legacy",
        metrics_json=json.dumps({"legacy": True}),
        common_payload_json=json.dumps(common_payload, ensure_ascii=False),
        strategy_payload_json=json.dumps({"private": {"ignored": True}}, ensure_ascii=False),
        contract_version="strategy_result_contract_v1",
        strategy_role="directional",
        common_payload_hash="hash",
    )

    summary = classify_strategy_results((row,))

    assert summary.effective_strategy_count == 1
    assert len(summary.long_strategies) == 1
    assert summary.long_strategies[0]["reason_codes"] == ["common_contract_reason"]
    assert summary.long_strategies[0]["metrics"]["strategy_private_payload_summary"] == {
        "available": True,
        "top_level_keys": ["private"],
        "participates_in_common_aggregation": False,
    }


def test_stage18_falls_back_to_legacy_strategy_signal_fields() -> None:
    row = SimpleNamespace(
        strategy_name="legacy_fixture",
        strategy_version="v1",
        strategy_status="success",
        direction_bias="bearish_bias",
        risk_level="medium",
        signal_strength=Decimal("0.60"),
        reason_codes_json=json.dumps(["legacy_reason"]),
        reason_text="legacy row",
        metrics_json=json.dumps({"legacy": True}),
        common_payload_json=None,
    )

    summary = classify_strategy_results((row,))

    assert summary.effective_strategy_count == 1
    assert len(summary.short_strategies) == 1
    assert summary.short_strategies[0]["reason_codes"] == ["legacy_reason"]
