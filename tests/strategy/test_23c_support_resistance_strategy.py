from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.strategy.aggregation.rules import classify_strategy_results
from app.strategy.common.result_adapter import adapt_strategy_result_to_signal
from app.strategy.common.result_contract import StrategyResult
from app.strategy.common.result_validator import validate_strategy_result
from app.strategy.registry import StrategyRegistry
from app.strategy.result_repository import StrategySignalResultRepository
from app.strategy.runner import StrategyRunner
from app.strategy.signal_service import StrategySignalService
from app.strategy.strategies.market_direction_regime_strategy import MarketDirectionRegimeStrategy
from app.strategy.strategies.support_resistance_strategy import SupportResistanceStrategy
from app.strategy.types import (
    StrategyEvaluationInput,
    StrategyRunStatus,
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


class FailingStrategy:
    strategy_name = "failing_23c_fixture"
    strategy_version = "v1"

    def evaluate(self, _input_data: StrategyEvaluationInput) -> StrategyResult:
        raise RuntimeError("fixture failure")


def kline_row(index: int, *, interval_value: str, close: Decimal, wick: Decimal = Decimal("95")) -> Any:
    interval_ms = 14_400_000 if interval_value == "4h" else 86_400_000
    open_time_ms = 1_700_000_000_000 + index * interval_ms
    return SimpleNamespace(
        symbol="BTCUSDT",
        interval_value=interval_value,
        open_time_ms=open_time_ms,
        open_time_utc=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
        high_price=close + wick,
        low_price=close - wick,
        close_price=close,
    )


def wave_rows(count: int, *, interval_value: str = "4h", base: str = "60000") -> tuple[Any, ...]:
    pattern = (
        Decimal("-900"),
        Decimal("-420"),
        Decimal("120"),
        Decimal("720"),
        Decimal("280"),
        Decimal("-260"),
    )
    rows: list[Any] = []
    for index in range(count):
        drift = Decimal(index // len(pattern)) * Decimal("8")
        close = Decimal(base) + pattern[index % len(pattern)] + drift
        rows.append(kline_row(index, interval_value=interval_value, close=close))
    rows[6] = kline_row(6, interval_value=interval_value, close=Decimal(base) - Decimal("5200"), wick=Decimal("180"))
    rows[-1] = kline_row(count - 1, interval_value=interval_value, close=Decimal(base) + Decimal("160"), wick=Decimal("70"))
    return tuple(rows)


def higher_rows(count: int) -> tuple[Any, ...]:
    pattern = (Decimal("-1100"), Decimal("-360"), Decimal("460"), Decimal("1240"), Decimal("520"), Decimal("-240"))
    return tuple(
        kline_row(index, interval_value="1d", close=Decimal("59000") + pattern[index % len(pattern)] + Decimal(index * 6), wick=Decimal("180"))
        for index in range(count)
    )


def strategy_input(
    *,
    base_rows: tuple[Any, ...] | None = None,
    higher: tuple[Any, ...] | None = None,
) -> StrategyEvaluationInput:
    active_base_rows = base_rows or wave_rows(180)
    active_higher_rows = higher or higher_rows(180)
    return StrategyEvaluationInput(
        snapshot_id="MCS-23C",
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        base_klines=active_base_rows,
        higher_klines=active_higher_rows,
        lookback_base_count=len(active_base_rows),
        lookback_higher_count=len(active_higher_rows),
        latest_base_open_time_ms=active_base_rows[-1].open_time_ms,
        latest_higher_open_time_ms=active_higher_rows[-1].open_time_ms,
        base_start_open_time_ms=active_base_rows[0].open_time_ms,
        base_end_open_time_ms=active_base_rows[-1].open_time_ms,
        higher_start_open_time_ms=active_higher_rows[0].open_time_ms,
        higher_end_open_time_ms=active_higher_rows[-1].open_time_ms,
        base_quality_check_id=301,
        higher_quality_check_id=401,
        trace_id="trace-23c",
        evaluated_at_utc=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )


def support_resistance_strategy(**overrides: Any) -> SupportResistanceStrategy:
    config = {
        "strategy_version": "23C-test",
        "strategy_role": "support_resistance",
        "provides": [
            "key_levels",
            "support_zones",
            "resistance_zones",
            "range_boundaries",
            "invalidation_reference_zones",
            "target_observation_zones",
            "role_flip_candidates",
        ],
        "lookback_bars": {"base": 180, "higher": 180},
        "minimum_required_bars": {"base": 80, "higher": 120},
        "thresholds": {
            "swing_left_bars": 2,
            "swing_right_bars": 2,
            "min_swing_move_pct": "0.002",
            "cluster_width_pct": "0.007",
            "max_zone_width_pct": "0.025",
            "nearest_distance_pct": "0.12",
            "major_level_min_strength": "0.50",
            "outlier_reaction_min_pct": "0.01",
        },
        "output_limits": {
            "nearest_support": 3,
            "nearest_resistance": 3,
            "major_support": 5,
            "major_resistance": 5,
            "historical_reference": 5,
        },
    }
    config.update(overrides)
    return SupportResistanceStrategy(config)


def result_key_levels(result: StrategyResult) -> list[dict[str, Any]]:
    return list(result.common_result.to_jsonable().get("key_levels", []))


def test_support_resistance_strategy_outputs_role_result_and_key_levels() -> None:
    result = support_resistance_strategy().evaluate(strategy_input())
    key_levels = result_key_levels(result)

    assert result.strategy_role == "support_resistance"
    assert result.strategy_status == "success"
    assert validate_strategy_result(result).passed is True
    assert key_levels
    assert any(item["level_group"] == "nearest_support" for item in key_levels)
    assert any(item["level_group"] == "nearest_resistance" for item in key_levels)
    assert any(item["level_group"] == "major_support" for item in key_levels)
    assert any(item["level_group"] == "major_resistance" for item in key_levels)
    assert any(item["level_group"] == "range_upper_boundary" for item in key_levels)
    assert any(item["level_group"] == "range_lower_boundary" for item in key_levels)
    assert any(item["level_group"] == "role_flip_candidate" for item in key_levels)
    assert result.common_result.to_jsonable()["not_trading_advice"] is True


def test_config_declares_support_resistance_role_and_provides() -> None:
    source = Path("configs/strategies/support_resistance_strategy.yaml").read_text(encoding="utf-8")
    registry = StrategyRegistry()
    strategies = registry.load_enabled_strategies()
    strategy = next(item for item in strategies if item.strategy_name == "support_resistance_strategy")

    assert "strategy_role: support_resistance" in source
    assert "key_levels" in source
    assert "role_flip_candidates" in source
    assert strategy.strategy_role == "support_resistance"
    assert "support_zones" in strategy.provides


def test_private_swing_and_cluster_details_do_not_enter_common_result() -> None:
    result = support_resistance_strategy().evaluate(strategy_input())
    common = result.common_result.to_jsonable()
    payload = result.strategy_payload_json

    assert payload["raw_swing_points"]
    assert payload["merged_level_clusters"]
    assert payload["cluster_scoring_details"]
    assert payload["reaction_strength_details"]
    assert payload["recency_score_details"]
    assert "raw_swing_points" not in common
    assert "merged_level_clusters" not in common
    assert "cluster_scoring_details" not in common
    assert "reaction_strength_details" not in common
    assert "recency_score_details" not in common


def test_wide_zone_quality_is_lowered_by_configured_width_threshold() -> None:
    result = support_resistance_strategy(
        thresholds={
            "swing_left_bars": 2,
            "swing_right_bars": 2,
            "min_swing_move_pct": "0.002",
            "cluster_width_pct": "0.060",
            "max_zone_width_pct": "0.001",
            "nearest_distance_pct": "0.12",
            "major_level_min_strength": "0.30",
            "outlier_reaction_min_pct": "0.01",
        }
    ).evaluate(strategy_input())

    assert any(cluster["zone_quality"] == "wide" for cluster in result.strategy_payload_json["merged_level_clusters"])


def test_isolated_spike_does_not_become_high_strength_key_level() -> None:
    result = support_resistance_strategy().evaluate(strategy_input())
    outliers = result.strategy_payload_json["excluded_outliers"]
    all_clusters = result.strategy_payload_json["merged_level_clusters"]

    assert outliers
    assert all(Decimal(str(cluster["strength_score"])) < Decimal("0.70") for cluster in outliers)
    assert all(
        Decimal(str(cluster["strength_score"])) < Decimal("0.85")
        for cluster in all_clusters
        if cluster["zone_quality"] == "outlier"
    )


def test_historical_reference_has_lower_current_relevance() -> None:
    result = support_resistance_strategy().evaluate(strategy_input())
    historical = [
        item
        for item in result_key_levels(result)
        if item["level_group"] == "historical_reference"
    ]

    assert historical
    assert all(Decimal(str(item["current_relevance_score"])) <= Decimal("0.50") for item in historical)


def test_insufficient_data_outputs_invalid_without_exception() -> None:
    input_data = strategy_input(base_rows=wave_rows(10), higher=higher_rows(20))

    result = support_resistance_strategy().evaluate(input_data)

    assert result.strategy_status == "invalid"
    assert validate_strategy_result(result).passed is True
    assert result.strategy_payload_json["insufficient_data"]["actual_base_count"] == 10
    assert result.strategy_payload_json["raw_swing_points"] == []


def test_disabled_support_resistance_config_does_not_break_registry(tmp_path: Path) -> None:
    (tmp_path / "strategy_registry.yaml").write_text(
        "enabled_strategies:\n  - support_resistance_strategy\n  - market_direction_regime\n",
        encoding="utf-8",
    )
    (tmp_path / "support_resistance_strategy.yaml").write_text(
        "enabled: false\nstrategy_version: 23C-test\nstrategy_role: support_resistance\nprovides:\n  - key_levels\n",
        encoding="utf-8",
    )
    (tmp_path / "market_direction_regime_strategy.yaml").write_text(
        "enabled: true\nstrategy_version: 23B-test\nstrategy_role: context\nprovides:\n  - primary_regime\nminimum_required_bars:\n  base: 10\n  higher: 10\n",
        encoding="utf-8",
    )
    registry = StrategyRegistry(
        config_dir=tmp_path,
        strategy_classes={
            "support_resistance_strategy": SupportResistanceStrategy,
            "market_direction_regime": MarketDirectionRegimeStrategy,
        },
    )

    strategies = registry.load_enabled_strategies()

    assert [strategy.strategy_name for strategy in strategies] == ["market_direction_regime"]


def test_runner_isolates_support_resistance_neighbor_failure() -> None:
    runner = StrategyRunner(registry=FakeRegistry((support_resistance_strategy(), FailingStrategy())))

    result = runner.run_strategies(strategy_input())

    assert result.status == StrategyRunStatus.PARTIAL_SUCCESS
    assert result.signals[0].strategy_name == "support_resistance_strategy"
    assert result.signals[0].strategy_status == StrategySignalStatus.SUCCESS
    assert result.signals[1].strategy_status == StrategySignalStatus.FAILED


def test_run_strategy_signals_persists_23c_result() -> None:
    session = FakeSession()
    service = StrategySignalService(
        input_builder=FakeInputBuilder(strategy_input()),
        runner=StrategyRunner(registry=FakeRegistry((support_resistance_strategy(),))),
        result_repository=StrategySignalResultRepository(),
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            snapshot_id="MCS-23C",
            trigger_source="cli",
            dry_run=False,
            confirm_write=True,
            trace_id="trace-23c",
        ),
    )

    assert result.status == StrategyRunStatus.SUCCESS
    assert session.commits == 1
    row = session.added[1]
    assert row.strategy_role == "support_resistance"
    assert json.loads(row.common_payload_json)["key_levels"]
    assert json.loads(row.strategy_payload_json)["raw_swing_points"]


def test_stage18_reads_23c_result_without_crash() -> None:
    signal = adapt_strategy_result_to_signal(support_resistance_strategy().evaluate(strategy_input()))
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
