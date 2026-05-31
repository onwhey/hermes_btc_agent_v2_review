from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.strategy.aggregation.rules import classify_strategy_results
from app.strategy.aggregation.evidence_aggregator import StrategyEvidenceAggregator
from app.strategy.aggregation.evidence_config import EvidenceAggregationConfig
from app.strategy.aggregation.evidence_types import ParticipationMode, StrategyGovernance
from app.strategy.common.constants import MAX_COMMON_PAYLOAD_BYTES, MAX_STRATEGY_PAYLOAD_BYTES
from app.strategy.common.payload_tools import payload_size_bytes
from app.strategy.common.result_adapter import adapt_strategy_result_to_signal
from app.strategy.common.result_contract import StrategyResult
from app.strategy.common.result_validator import validate_strategy_result
from app.strategy.registry import StrategyRegistry
from app.strategy.result_repository import StrategySignalResultRepository
from app.strategy.runner import StrategyRunner
from app.strategy.signal_service import StrategySignalService
from app.strategy.strategies.breakout_pullback_trigger_strategy import BreakoutPullbackTriggerStrategy
from app.strategy.strategies.market_direction_regime_strategy import MarketDirectionRegimeStrategy
from app.strategy.strategies.support_resistance_strategy import SupportResistanceStrategy, _zone_quality
from app.strategy.types import (
    StrategyEvaluationInput,
    StrategyRunStatus,
    StrategySignalRunRequest,
    StrategySignalStatus,
)
from tests.strategy import NoOpEvidenceAggregationHook


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


class FakeGovernanceProvider:
    def __init__(self, governance: dict[str, StrategyGovernance]) -> None:
        self.governance = governance
        self.config = EvidenceAggregationConfig(
            required_roles=("support_resistance",),
            required_role_provides={"support_resistance": ("key_levels",)},
            default_governance=StrategyGovernance(
                strategy_name="default",
                strategy_role="",
                participation_mode=ParticipationMode.OBSERVE_ONLY.value,
                decision_weight=Decimal("0"),
            ),
        )

    def get_aggregation_config(self) -> EvidenceAggregationConfig:
        return self.config

    def get_strategy_governance(self, *, strategy_name: str, strategy_role: str | None = None) -> StrategyGovernance:
        return self.governance.get(
            strategy_name,
            StrategyGovernance(strategy_name=strategy_name, strategy_role=strategy_role or ""),
        )


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


def dense_swing_rows(count: int, *, interval_value: str = "4h", base: str = "60000") -> tuple[Any, ...]:
    pattern = (
        Decimal("-1200"),
        Decimal("760"),
        Decimal("-840"),
        Decimal("1320"),
        Decimal("-360"),
        Decimal("980"),
        Decimal("-1040"),
        Decimal("420"),
    )
    rows: list[Any] = []
    for index in range(count):
        drift = Decimal(index // len(pattern)) * Decimal("5")
        spread_bucket = Decimal(index % 47) * Decimal("28")
        close = Decimal(base) + pattern[index % len(pattern)] + spread_bucket + drift
        rows.append(kline_row(index, interval_value=interval_value, close=close, wick=Decimal("120")))
    rows[-1] = kline_row(count - 1, interval_value=interval_value, close=Decimal(base) + Decimal("600"), wick=Decimal("90"))
    return tuple(rows)


def role_flip_rows(count: int = 140) -> tuple[Any, ...]:
    rows: list[Any] = []
    for index in range(count):
        close = Decimal("60500") + Decimal((index % 7) - 3) * Decimal("6")
        rows.append(kline_row(index, interval_value="4h", close=close, wick=Decimal("70")))
    for index in range(38, 43):
        close = Decimal("59600") + Decimal(index - 40) * Decimal("10")
        rows[index] = kline_row(index, interval_value="4h", close=close, wick=Decimal("60"))
    rows[40] = kline_row(40, interval_value="4h", close=Decimal("59920"), wick=Decimal("80"))
    for index in range(88, 93):
        close = Decimal("60380") + Decimal(index - 90) * Decimal("10")
        rows[index] = kline_row(index, interval_value="4h", close=close, wick=Decimal("80"))
    rows[90] = kline_row(90, interval_value="4h", close=Decimal("60100"), wick=Decimal("80"))
    rows[-1] = kline_row(count - 1, interval_value="4h", close=Decimal("60500"), wick=Decimal("70"))
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


def aggregation_row_from_signal(signal: Any) -> Any:
    return SimpleNamespace(
        strategy_name=signal.strategy_name,
        strategy_version=signal.strategy_version,
        strategy_status=signal.strategy_status.value,
        validation_status=signal.validation_status,
        strategy_role=signal.strategy_role,
        common_payload_json=json.dumps(signal.common_payload_json, ensure_ascii=False),
        reason_text=signal.reason_text,
        signal_strength=Decimal(str(signal.signal_strength)),
    )


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
    assert result.common_result.to_jsonable()["not_trading_advice"] is True


def test_role_flip_candidate_requires_real_role_flip_zone() -> None:
    result = support_resistance_strategy().evaluate(strategy_input(base_rows=role_flip_rows()))
    key_levels = result_key_levels(result)
    role_flip_candidates = [
        item
        for item in key_levels
        if item["level_group"] == "role_flip_candidate"
    ]

    assert role_flip_candidates
    assert all(item["role_flip_status"] != "none" for item in role_flip_candidates)
    assert any(
        item["role_flip_status"] in {"resistance_to_support", "support_to_resistance", "unconfirmed"}
        for item in role_flip_candidates
    )
    assert any(
        {"swing_high", "swing_low"}.issubset(set(detail["source_point_types"]))
        for detail in result.strategy_payload_json["role_flip_detection_details"]
    )


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


def test_large_23c_candidate_pool_keeps_payload_under_contract_limit() -> None:
    strategy = support_resistance_strategy(
        lookback_bars={"base": 520, "higher": 365},
        minimum_required_bars={"base": 80, "higher": 120},
        thresholds={
            "swing_left_bars": 1,
            "swing_right_bars": 1,
            "min_swing_move_pct": "0.001",
            "cluster_width_pct": "0.0015",
            "max_zone_width_pct": "0.025",
            "nearest_distance_pct": "0.20",
            "major_level_min_strength": "0.20",
            "outlier_reaction_min_pct": "0.004",
        },
        output_limits={
            "nearest_support": 8,
            "nearest_resistance": 8,
            "major_support": 10,
            "major_resistance": 10,
            "historical_reference": 10,
            "role_flip_candidate": 10,
        },
    )

    result = strategy.evaluate(
        strategy_input(
            base_rows=dense_swing_rows(520),
            higher=dense_swing_rows(365, interval_value="1d", base="59200"),
        )
    )
    common = result.common_result.to_jsonable()
    payload = result.strategy_payload_json
    validation = validate_strategy_result(result)

    assert validation.passed is True
    assert payload_size_bytes(common) <= MAX_COMMON_PAYLOAD_BYTES
    assert payload_size_bytes(payload) <= MAX_STRATEGY_PAYLOAD_BYTES
    assert common["key_levels"]
    assert common["nearest_support"] or common["nearest_resistance"]
    assert common["current_price"]
    assert common["level_count"] == len(common["key_levels"])
    assert payload["truncation_summary"]["payload_trimmed"] is True
    assert len(payload["raw_swing_points"]) <= 12
    assert len(payload["merged_level_clusters"]) <= 12
    assert "all_candidates" not in payload
    assert "all_clusters_full_detail" not in payload
    assert "per_kline_debug" not in payload


def test_payload_trimming_preserves_top_public_key_levels() -> None:
    result = support_resistance_strategy(
        lookback_bars={"base": 520, "higher": 365},
        thresholds={
            "swing_left_bars": 1,
            "swing_right_bars": 1,
            "min_swing_move_pct": "0.001",
            "cluster_width_pct": "0.0015",
            "max_zone_width_pct": "0.025",
            "nearest_distance_pct": "0.20",
            "major_level_min_strength": "0.20",
            "outlier_reaction_min_pct": "0.004",
        },
        output_limits={
            "nearest_support": 8,
            "nearest_resistance": 8,
            "major_support": 10,
            "major_resistance": 10,
            "historical_reference": 10,
            "role_flip_candidate": 10,
        },
    ).evaluate(
        strategy_input(
            base_rows=dense_swing_rows(520),
            higher=dense_swing_rows(365, interval_value="1d", base="59200"),
        )
    )
    common_key_levels = result.common_result.to_jsonable()["key_levels"]
    selected_key_levels = result.strategy_payload_json["selected_key_levels"]
    selected_groups = {(item["level_group"], item["cluster_id"]) for item in selected_key_levels}

    assert any(item["level_group"] == "nearest_support" for item in common_key_levels)
    assert any(item["level_group"] == "nearest_resistance" for item in common_key_levels)
    assert selected_groups
    assert len(selected_key_levels) <= 16


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


def test_zone_quality_keeps_outlier_and_makes_weak_reachable() -> None:
    strategy = support_resistance_strategy()

    assert _zone_quality(
        touch_count=1,
        reaction_strength=Decimal("0.020"),
        width_pct=Decimal("0.004"),
        strategy=strategy,
    ) == "outlier"
    assert _zone_quality(
        touch_count=2,
        reaction_strength=Decimal("0.001"),
        width_pct=Decimal("0.004"),
        strategy=strategy,
    ) == "weak"


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


def test_23d_reads_trimmed_23c_public_key_levels() -> None:
    runner = StrategyRunner(
        registry=FakeRegistry(
            (
                support_resistance_strategy(),
                BreakoutPullbackTriggerStrategy(
                    {
                        "strategy_version": "23D-test",
                        "minimum_required_bars": {"base": 30},
                        "thresholds": {
                            "breakout_distance_pct": "0.002",
                            "pullback_tolerance_pct": "0.010",
                            "wick_rejection_ratio": "0.35",
                            "volume_ratio_confirmation": "1.10",
                        },
                    }
                ),
            )
        )
    )

    result = runner.run_strategies(strategy_input())
    trigger_signal = next(signal for signal in result.signals if signal.strategy_name == "breakout_pullback_trigger_strategy")

    assert trigger_signal.strategy_status != StrategySignalStatus.INVALID
    assert "missing_support_resistance_key_levels" not in trigger_signal.reason_codes
    assert trigger_signal.common_payload_json.get("trigger_state") != "insufficient_key_levels"


def test_23f_coverage_accepts_valid_trimmed_support_resistance_key_levels() -> None:
    signal = adapt_strategy_result_to_signal(support_resistance_strategy().evaluate(strategy_input()))
    row = aggregation_row_from_signal(signal)
    provider = FakeGovernanceProvider(
        {
            "support_resistance_strategy": StrategyGovernance(
                strategy_name="support_resistance_strategy",
                strategy_role="support_resistance",
                provides=("key_levels",),
                participation_mode=ParticipationMode.EVIDENCE_ONLY.value,
                decision_weight=Decimal("0"),
            )
        }
    )

    aggregation = StrategyEvidenceAggregator(governance_provider=provider).aggregate_strategy_evidence(
        aggregation_id="SEA-23C",
        strategy_signal_run=SimpleNamespace(
            run_id="SSR-23C",
            symbol="BTCUSDT",
            base_interval_value="4h",
            higher_interval_value="1d",
        ),
        strategy_signal_results=(row,),
        trace_id="trace-23c",
    )
    coverage = aggregation.role_coverage_matrix["roles"]["support_resistance"]

    assert coverage["covered"] is True
    assert "key_levels" in coverage["provided"]
    assert coverage["effective_coverage_count"] == 1


def test_run_strategy_signals_persists_23c_result() -> None:
    session = FakeSession()
    service = StrategySignalService(
        input_builder=FakeInputBuilder(strategy_input()),
        runner=StrategyRunner(registry=FakeRegistry((support_resistance_strategy(),))),
        result_repository=StrategySignalResultRepository(),
        auto_evidence_aggregation_hook=NoOpEvidenceAggregationHook(),
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
