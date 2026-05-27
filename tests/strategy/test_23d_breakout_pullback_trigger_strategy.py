from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.strategy.aggregation.rules import classify_strategy_results
from app.strategy.base import BaseStrategy
from app.strategy.common.result_adapter import adapt_strategy_result_to_signal
from app.strategy.common.result_contract import StrategyCommonResult, StrategyEvidenceItem, StrategyResult
from app.strategy.common.result_validator import validate_strategy_result
from app.strategy.evidence_context import EvidenceContext
from app.strategy.registry import StrategyRegistry
from app.strategy.result_repository import StrategySignalResultRepository
from app.strategy.runner import StrategyRunner
from app.strategy.signal_service import StrategySignalService
from app.strategy.strategies.breakout_pullback_trigger_strategy import BreakoutPullbackTriggerStrategy
from app.strategy.strategies.market_direction_regime_strategy import MarketDirectionRegimeStrategy
from app.strategy.types import (
    DirectionBias,
    RiskLevel,
    StrategyEvaluationInput,
    StrategyRunStatus,
    StrategySignal,
    StrategySignalRunRequest,
    StrategySignalStatus,
)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    def add(self, row: Any) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushes += 1
        for index, row in enumerate(self.added, start=1):
            if getattr(row, "id", None) is None:
                setattr(row, "id", index)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeRegistry:
    def __init__(self, strategies: tuple[Any, ...]) -> None:
        self._strategies = strategies

    def load_enabled_strategies(self) -> tuple[Any, ...]:
        return self._strategies


class FakeInputBuilder:
    def __init__(self, input_data: StrategyEvaluationInput) -> None:
        self.input_data = input_data

    def build_input_from_snapshot(self, *_args: Any, **_kwargs: Any) -> StrategyEvaluationInput:
        return self.input_data


class PublicKeyLevelProvider(BaseStrategy):
    strategy_name = "support_resistance_strategy"
    strategy_version = "23C-test"
    strategy_role = "support_resistance"
    provides = ("key_levels",)

    def __init__(self, key_levels: tuple[dict[str, Any], ...]) -> None:
        self.key_levels = key_levels

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        return support_result(input_data.trace_id, self.key_levels)


class FailingStrategy(BaseStrategy):
    strategy_name = "failing_23d_fixture"
    strategy_version = "v1"
    strategy_role = "context"

    def evaluate(self, _input_data: StrategyEvaluationInput) -> StrategyResult:
        raise RuntimeError("fixture failure")


def kline_row(
    index: int,
    *,
    open_price: str,
    high: str,
    low: str,
    close: str,
    volume: str = "100",
    interval_value: str = "4h",
) -> Any:
    interval_ms = 14_400_000 if interval_value == "4h" else 86_400_000
    open_time_ms = 1_700_000_000_000 + index * interval_ms
    return SimpleNamespace(
        symbol="BTCUSDT",
        interval_value=interval_value,
        open_time_ms=open_time_ms,
        open_time_utc=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
        open_price=Decimal(open_price),
        high_price=Decimal(high),
        low_price=Decimal(low),
        close_price=Decimal(close),
        volume=Decimal(volume),
    )


def base_rows_with_latest(latest: Any, *, previous_breakout: bool = False) -> tuple[Any, ...]:
    rows = [
        kline_row(0, open_price="95", high="99", low="94", close="98"),
        kline_row(1, open_price="98", high="100", low="97", close="99"),
        kline_row(2, open_price="99", high="100", low="98", close="99"),
        kline_row(3, open_price="99", high="100", low="98", close="99"),
        latest,
    ]
    if previous_breakout:
        rows[2] = kline_row(2, open_price="101.8", high="103", low="101.5", close="102.5")
        rows[3] = kline_row(3, open_price="102", high="103", low="101.8", close="102.6")
    return tuple(rows)


def base_rows_for_support_latest(latest: Any) -> tuple[Any, ...]:
    return (
        kline_row(0, open_price="105", high="106", low="102", close="104"),
        kline_row(1, open_price="104", high="105", low="102", close="103"),
        kline_row(2, open_price="103", high="104", low="102", close="103"),
        kline_row(3, open_price="103", high="104", low="102", close="103"),
        latest,
    )


def higher_rows(count: int = 30) -> tuple[Any, ...]:
    return tuple(
        kline_row(
            index,
            open_price="90",
            high="110",
            low="80",
            close="95",
            interval_value="1d",
        )
        for index in range(count)
    )


def strategy_input(base_rows: tuple[Any, ...]) -> StrategyEvaluationInput:
    active_higher_rows = higher_rows()
    return StrategyEvaluationInput(
        snapshot_id="MCS-23D",
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        base_klines=base_rows,
        higher_klines=active_higher_rows,
        lookback_base_count=len(base_rows),
        lookback_higher_count=len(active_higher_rows),
        latest_base_open_time_ms=base_rows[-1].open_time_ms,
        latest_higher_open_time_ms=active_higher_rows[-1].open_time_ms,
        base_start_open_time_ms=base_rows[0].open_time_ms,
        base_end_open_time_ms=base_rows[-1].open_time_ms,
        higher_start_open_time_ms=active_higher_rows[0].open_time_ms,
        higher_end_open_time_ms=active_higher_rows[-1].open_time_ms,
        base_quality_check_id=301,
        higher_quality_check_id=401,
        trace_id="trace-23d",
        evaluated_at_utc=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )


def breakout_strategy(**overrides: Any) -> BreakoutPullbackTriggerStrategy:
    config = {
        "strategy_version": "23D-test",
        "strategy_role": "filter",
        "provides": [
            "breakout_pullback_trigger",
            "trigger_state",
            "filter_decision",
            "tested_level_summary",
            "volume_confirmation",
        ],
        "requires": [{"role": "support_resistance", "provides": "key_levels"}],
        "consumes": ["common_result.key_levels"],
        "lookback_bars": {"base": 20, "higher": 20},
        "minimum_required_bars": {"base": 5, "higher": 5},
        "thresholds": {
            "min_breakout_pct": "0.005",
            "min_breakdown_pct": "0.005",
            "zone_touch_tolerance_pct": "0.002",
            "wick_rejection_ratio": "0.45",
            "pullback_max_depth_pct": "0.012",
            "confirmation_bars": 2,
            "recent_trigger_lookback_bars": 4,
        },
        "volume": {
            "enabled": True,
            "ma_period": 3,
            "expansion_ratio": "1.2",
            "contraction_ratio": "0.75",
            "spike_ratio": "2.0",
        },
        "output_limits": {"tested_levels": 5},
    }
    config.update(overrides)
    return BreakoutPullbackTriggerStrategy(config)


def resistance_level(**overrides: Any) -> dict[str, Any]:
    level = {
        "level_id": "R1",
        "level_type": "resistance",
        "level_group": "nearest_resistance",
        "zone_low": "100",
        "zone_high": "101",
        "zone_mid": "100.5",
        "confidence_score": "0.80",
        "current_relevance_score": "0.90",
        "distance_from_current_price_pct": "0.01",
        "role_flip_status": "none",
        "zone_quality": "clear",
        "reason": "fixture resistance zone",
    }
    level.update(overrides)
    return level


def support_level(**overrides: Any) -> dict[str, Any]:
    level = {
        "level_id": "S1",
        "level_type": "support",
        "level_group": "nearest_support",
        "zone_low": "100",
        "zone_high": "101",
        "zone_mid": "100.5",
        "confidence_score": "0.80",
        "current_relevance_score": "0.90",
        "distance_from_current_price_pct": "0.01",
        "role_flip_status": "none",
        "zone_quality": "clear",
        "reason": "fixture support zone",
    }
    level.update(overrides)
    return level


def evidence_context(
    key_levels: tuple[dict[str, Any], ...],
    *,
    private_payload: dict[str, Any] | None = None,
) -> EvidenceContext:
    signal = StrategySignal(
        strategy_name="support_resistance_strategy",
        strategy_version="23C-test",
        strategy_status=StrategySignalStatus.SUCCESS,
        direction_bias=DirectionBias.NOT_APPLICABLE,
        risk_level=RiskLevel.UNKNOWN,
        common_payload_json={
            "schema_version": "strategy_common_result_v1",
            "key_levels": list(key_levels),
            "not_trading_advice": True,
        },
        strategy_payload_json=private_payload or {},
        strategy_role="support_resistance",
        trace_id="trace-23d",
    )
    return EvidenceContext.empty().with_signal(signal)


def support_result(trace_id: str, key_levels: tuple[dict[str, Any], ...]) -> StrategyResult:
    return StrategyResult(
        strategy_name="support_resistance_strategy",
        strategy_version="23C-test",
        strategy_role="support_resistance",
        strategy_status=StrategySignalStatus.SUCCESS.value,
        common_result=StrategyCommonResult(
            market_bias="not_applicable",
            risk_level="unknown",
            signal_strength="0.70",
            confidence_score="0.70",
            reason_codes=("fixture_key_levels",),
            reason_text="公开关键位摘要已生成。",
            key_levels=key_levels,
            evidence_items=(
                StrategyEvidenceItem(
                    evidence_type="fixture_key_levels",
                    direction="not_applicable",
                    strength="0.70",
                    description="公开关键位摘要已生成。",
                    source="support_resistance_strategy",
                ),
            ),
            not_trading_advice=True,
        ),
        strategy_model_material_json={"summary": "fixture public key levels"},
        strategy_payload_json={"raw_swing_points": [{"private": True}]},
        trace_id=trace_id,
    )


def evaluate_with_level(base_rows: tuple[Any, ...], level: dict[str, Any]) -> StrategyResult:
    return breakout_strategy().evaluate_with_evidence(strategy_input(base_rows), evidence_context((level,)))


def test_breakout_pullback_strategy_outputs_filter_role_result() -> None:
    latest = kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150")
    result = evaluate_with_level(base_rows_with_latest(latest), resistance_level())
    common = result.common_result.to_jsonable()

    assert result.strategy_role == "filter"
    assert result.strategy_status == "success"
    assert validate_strategy_result(result).passed is True
    assert common["trigger_state"] == "breakout_confirmed"
    assert common["filter_decision"] == "passed"
    assert common["volume_confirmation"] == "confirming"
    assert common["not_trading_advice"] is True


def test_config_declares_provides_requires_and_consumes() -> None:
    source = Path("configs/strategies/breakout_pullback_trigger_strategy.yaml").read_text(encoding="utf-8")
    registry = StrategyRegistry()
    strategy = next(
        item for item in registry.load_enabled_strategies() if item.strategy_name == "breakout_pullback_trigger_strategy"
    )

    assert "strategy_role: filter" in source
    assert "requires:" in source
    assert "consumes:" in source
    assert strategy.strategy_role == "filter"
    assert "trigger_state" in strategy.provides
    assert strategy.requires == ({"role": "support_resistance", "provides": "key_levels"},)
    assert "common_result.key_levels" in strategy.consumes


def test_runner_orders_support_resistance_before_23d_and_passes_public_key_levels() -> None:
    latest = kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150")
    input_data = strategy_input(base_rows_with_latest(latest))
    runner = StrategyRunner(
        registry=FakeRegistry(
            (
                breakout_strategy(),
                PublicKeyLevelProvider((resistance_level(),)),
            )
        )
    )

    result = runner.run_strategies(input_data)

    assert result.status == StrategyRunStatus.SUCCESS
    assert [signal.strategy_name for signal in result.signals] == [
        "support_resistance_strategy",
        "breakout_pullback_trigger_strategy",
    ]
    assert result.signals[1].common_payload_json["trigger_state"] == "breakout_confirmed"


def test_missing_23c_key_levels_outputs_not_applicable_without_exception() -> None:
    latest = kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150")
    result = breakout_strategy().evaluate_with_evidence(
        strategy_input(base_rows_with_latest(latest)),
        EvidenceContext.empty(),
    )
    common = result.common_result.to_jsonable()

    assert result.strategy_status == "no_signal"
    assert common["trigger_state"] == "insufficient_key_levels"
    assert common["filter_decision"] == "not_applicable"
    assert result.strategy_payload_json["selected_key_level_candidates"] == []


def test_23d_reads_only_public_common_key_levels_not_private_payload() -> None:
    latest = kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150")
    context = evidence_context(
        (resistance_level(),),
        private_payload={"key_levels": [support_level(level_id="PRIVATE-SHOULD-NOT-BE-READ")]},
    )

    result = breakout_strategy().evaluate_with_evidence(strategy_input(base_rows_with_latest(latest)), context)

    assert result.common_result.to_jsonable()["tested_level_summary"]["level_id"] == "R1"
    assert result.strategy_payload_json["tested_level_id"] == "R1"


def test_breakout_attempt_and_false_breakout_states() -> None:
    attempt = evaluate_with_level(
        base_rows_with_latest(kline_row(4, open_price="101", high="104", low="100", close="102", volume="150")),
        resistance_level(),
    )
    false_breakout = evaluate_with_level(
        base_rows_with_latest(kline_row(4, open_price="100.5", high="104", low="99.8", close="100.5", volume="250")),
        resistance_level(),
    )

    assert attempt.common_result.to_jsonable()["trigger_state"] == "breakout_attempt"
    assert attempt.common_result.to_jsonable()["filter_decision"] == "uncertain"
    assert false_breakout.common_result.to_jsonable()["trigger_state"] == "false_breakout"
    assert false_breakout.common_result.to_jsonable()["filter_decision"] == "blocked"
    assert false_breakout.strategy_payload_json["false_breakout_details"]


def test_breakdown_confirmed_and_false_breakdown_states() -> None:
    confirmed = evaluate_with_level(
        base_rows_for_support_latest(kline_row(4, open_price="99", high="99.5", low="97.8", close="98", volume="150")),
        support_level(),
    )
    false_breakdown = evaluate_with_level(
        base_rows_for_support_latest(kline_row(4, open_price="100.5", high="101.2", low="97", close="100.5", volume="250")),
        support_level(),
    )

    assert confirmed.common_result.to_jsonable()["trigger_state"] == "breakdown_confirmed"
    assert confirmed.common_result.to_jsonable()["filter_decision"] == "passed"
    assert false_breakdown.common_result.to_jsonable()["trigger_state"] == "false_breakdown"
    assert false_breakdown.common_result.to_jsonable()["filter_decision"] == "blocked"
    assert false_breakdown.strategy_payload_json["false_breakdown_details"]


def test_pullback_testing_and_confirmed_states() -> None:
    confirmed = evaluate_with_level(
        base_rows_with_latest(
            kline_row(4, open_price="101", high="102.2", low="100.7", close="101.5", volume="120"),
            previous_breakout=True,
        ),
        resistance_level(),
    )
    testing = evaluate_with_level(
        base_rows_with_latest(
            kline_row(4, open_price="100.7", high="101.2", low="100.4", close="100.8", volume="120"),
            previous_breakout=True,
        ),
        resistance_level(),
    )

    assert confirmed.common_result.to_jsonable()["trigger_state"] == "pullback_confirmed"
    assert confirmed.common_result.to_jsonable()["filter_decision"] == "passed"
    assert testing.common_result.to_jsonable()["trigger_state"] == "pullback_testing"
    assert testing.common_result.to_jsonable()["filter_decision"] == "uncertain"


def test_role_flip_resistance_to_support_uses_support_retest_direction() -> None:
    result = evaluate_with_level(
        base_rows_with_latest(
            kline_row(4, open_price="101", high="102.2", low="100.7", close="101.5", volume="120"),
            previous_breakout=True,
        ),
        resistance_level(
            level_group="role_flip_candidate",
            role_flip_status="resistance_to_support",
        ),
    )
    common = result.common_result.to_jsonable()

    assert common["tested_level_summary"]["role_flip_status"] == "resistance_to_support"
    assert common["trigger_state"] in {"pullback_testing", "pullback_confirmed"}
    assert common["trigger_state"] not in {"breakout_attempt", "breakout_confirmed"}
    assert result.strategy_payload_json["pullback_detection_details"]["pullback_direction"] == "after_breakout"


def test_role_flip_support_to_resistance_uses_resistance_retest_direction() -> None:
    rows = (
        kline_row(0, open_price="105", high="106", low="102", close="104"),
        kline_row(1, open_price="104", high="105", low="102", close="103"),
        kline_row(2, open_price="100", high="100.5", low="97.8", close="98"),
        kline_row(3, open_price="98.5", high="99", low="97.5", close="98.5"),
        kline_row(4, open_price="99.5", high="100.8", low="98.8", close="99.2", volume="120"),
    )
    result = evaluate_with_level(
        rows,
        support_level(
            level_group="role_flip_candidate",
            role_flip_status="support_to_resistance",
        ),
    )
    common = result.common_result.to_jsonable()

    assert common["tested_level_summary"]["role_flip_status"] == "support_to_resistance"
    assert common["trigger_state"] in {"pullback_testing", "pullback_confirmed"}
    assert common["trigger_state"] not in {"breakdown_attempt", "breakdown_confirmed"}
    assert result.strategy_payload_json["pullback_detection_details"]["pullback_direction"] == "after_breakdown"


def test_unconfirmed_role_flip_candidate_stays_conservative() -> None:
    result = evaluate_with_level(
        base_rows_with_latest(kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150")),
        resistance_level(
            level_group="role_flip_candidate",
            role_flip_status="unconfirmed",
        ),
    )
    common = result.common_result.to_jsonable()

    assert common["tested_level_summary"]["role_flip_status"] == "unconfirmed"
    assert common["trigger_state"] == "no_clear_trigger"
    assert common["filter_decision"] == "uncertain"
    assert common["trigger_state"] not in {"breakout_confirmed", "breakdown_confirmed"}


def test_volume_expansion_confirms_and_contraction_weakens_confirmed_breakout() -> None:
    expanded = evaluate_with_level(
        base_rows_with_latest(kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150")),
        resistance_level(),
    )
    contracted = evaluate_with_level(
        base_rows_with_latest(kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="50")),
        resistance_level(),
    )
    expanded_common = expanded.common_result.to_jsonable()
    contracted_common = contracted.common_result.to_jsonable()

    assert expanded_common["volume_confirmation"] == "confirming"
    assert expanded_common["filter_decision"] == "passed"
    assert contracted_common["volume_confirmation"] == "weakening"
    assert contracted_common["filter_decision"] == "uncertain"
    assert Decimal(expanded_common["confidence_score"]) > Decimal(contracted_common["confidence_score"])


def test_private_calculation_details_do_not_enter_common_result() -> None:
    latest = kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150")
    result = evaluate_with_level(base_rows_with_latest(latest), resistance_level())
    common = result.common_result.to_jsonable()
    payload = result.strategy_payload_json

    assert payload["breakout_distance_pct"] is not None
    assert payload["volume_ratio"] == "1.5000"
    assert payload["calculation_params"]
    assert "breakout_distance_pct" not in common
    assert "breakdown_distance_pct" not in common
    assert "wick_rejection_ratio" not in common
    assert "volume_ratio" not in common
    assert "pullback_detection_details" not in common


def test_insufficient_data_outputs_invalid_without_exception() -> None:
    rows = base_rows_with_latest(kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150"))[:3]

    result = breakout_strategy().evaluate_with_evidence(strategy_input(rows), evidence_context((resistance_level(),)))

    assert result.strategy_status == "invalid"
    assert validate_strategy_result(result).passed is True
    assert result.common_result.to_jsonable()["trigger_state"] == "insufficient_data"
    assert result.strategy_payload_json["insufficient_data"]["actual_base_count"] == 3


def test_disabled_23d_config_does_not_break_registry(tmp_path: Path) -> None:
    (tmp_path / "strategy_registry.yaml").write_text(
        "enabled_strategies:\n  - breakout_pullback_trigger_strategy\n  - market_direction_regime\n",
        encoding="utf-8",
    )
    (tmp_path / "breakout_pullback_trigger_strategy.yaml").write_text(
        "enabled: false\nstrategy_version: 23D-test\nstrategy_role: filter\nprovides:\n  - trigger_state\n",
        encoding="utf-8",
    )
    (tmp_path / "market_direction_regime_strategy.yaml").write_text(
        "enabled: true\nstrategy_version: 23B-test\nstrategy_role: context\nprovides:\n  - primary_regime\nminimum_required_bars:\n  base: 10\n  higher: 10\n",
        encoding="utf-8",
    )
    registry = StrategyRegistry(
        config_dir=tmp_path,
        strategy_classes={
            "breakout_pullback_trigger_strategy": BreakoutPullbackTriggerStrategy,
            "market_direction_regime": MarketDirectionRegimeStrategy,
        },
    )

    strategies = registry.load_enabled_strategies()

    assert [strategy.strategy_name for strategy in strategies] == ["market_direction_regime"]


def test_runner_isolates_single_strategy_failure_and_23d_still_runs() -> None:
    latest = kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150")
    runner = StrategyRunner(
        registry=FakeRegistry(
            (
                PublicKeyLevelProvider((resistance_level(),)),
                FailingStrategy(),
                breakout_strategy(),
            )
        )
    )

    result = runner.run_strategies(strategy_input(base_rows_with_latest(latest)))

    assert result.status == StrategyRunStatus.PARTIAL_SUCCESS
    assert result.signals[1].strategy_status == StrategySignalStatus.FAILED
    assert result.signals[2].strategy_name == "breakout_pullback_trigger_strategy"
    assert result.signals[2].common_payload_json["trigger_state"] == "breakout_confirmed"


def test_run_strategy_signals_persists_23d_result() -> None:
    latest = kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150")
    session = FakeSession()
    service = StrategySignalService(
        input_builder=FakeInputBuilder(strategy_input(base_rows_with_latest(latest))),
        runner=StrategyRunner(
            registry=FakeRegistry(
                (
                    PublicKeyLevelProvider((resistance_level(),)),
                    breakout_strategy(),
                )
            )
        ),
        result_repository=StrategySignalResultRepository(),
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            snapshot_id="MCS-23D",
            trigger_source="cli",
            dry_run=False,
            confirm_write=True,
            trace_id="trace-23d",
        ),
    )

    assert result.status == StrategyRunStatus.SUCCESS
    assert session.commits == 1
    breakout_row = next(row for row in session.added if getattr(row, "strategy_name", "") == "breakout_pullback_trigger_strategy")
    common = json.loads(breakout_row.common_payload_json)
    payload = json.loads(breakout_row.strategy_payload_json)
    assert breakout_row.strategy_role == "filter"
    assert common["trigger_state"] == "breakout_confirmed"
    assert payload["breakout_distance_pct"] is not None


def test_stage18_reads_23d_result_without_crash() -> None:
    latest = kline_row(4, open_price="101.8", high="103.2", low="101.6", close="103", volume="150")
    signal = adapt_strategy_result_to_signal(evaluate_with_level(base_rows_with_latest(latest), resistance_level()))
    row = SimpleNamespace(
        strategy_name=signal.strategy_name,
        strategy_version=signal.strategy_version,
        strategy_status=signal.strategy_status.value,
        direction_bias=signal.direction_bias.value,
        risk_level=signal.risk_level.value,
        signal_strength=Decimal(str(signal.signal_strength)),
        reason_codes_json=json.dumps(list(signal.reason_codes), ensure_ascii=False),
        reason_text=signal.reason_text,
        metrics_json=json.dumps(signal.metrics, ensure_ascii=False),
        common_payload_json=json.dumps(signal.common_payload_json, ensure_ascii=False),
        strategy_payload_json=json.dumps(signal.strategy_payload_json, ensure_ascii=False),
        contract_version=signal.contract_version,
        strategy_role=signal.strategy_role,
        common_payload_hash=signal.common_payload_hash,
    )

    summary = classify_strategy_results((row,))

    assert summary.effective_strategy_count == 1
    assert len(summary.long_strategies) == 0
    assert len(summary.short_strategies) == 0
