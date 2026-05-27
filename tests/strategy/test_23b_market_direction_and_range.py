from __future__ import annotations

import json
from contextlib import contextmanager
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
from app.strategy.strategies.short_term_range_strategy import ShortTermRangeStrategy
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
    strategy_name = "failing_23b_fixture"
    strategy_version = "v1"

    def evaluate(self, _input_data: StrategyEvaluationInput) -> StrategyResult:
        raise RuntimeError("fixture failure")


def kline_row(index: int, *, interval_value: str, close: Decimal) -> Any:
    interval_ms = 14_400_000 if interval_value == "4h" else 86_400_000
    open_time_ms = 1_700_000_000_000 + index * interval_ms
    wick = Decimal("80") if interval_value == "4h" else Decimal("180")
    return SimpleNamespace(
        symbol="BTCUSDT",
        interval_value=interval_value,
        open_time_ms=open_time_ms,
        open_time_utc=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
        high_price=close + wick,
        low_price=close - wick,
        close_price=close,
    )


def trending_rows(count: int, *, interval_value: str, start: str = "50000", step: str = "35") -> tuple[Any, ...]:
    return tuple(
        kline_row(index, interval_value=interval_value, close=Decimal(start) + Decimal(step) * Decimal(index))
        for index in range(count)
    )


def oscillating_rows(count: int, *, interval_value: str = "4h") -> tuple[Any, ...]:
    pattern = (Decimal("-160"), Decimal("-70"), Decimal("30"), Decimal("120"), Decimal("70"), Decimal("-30"))
    rows: list[Any] = []
    for index in range(count):
        cycle_lift = Decimal(index // len(pattern)) * Decimal("4")
        close = Decimal("60000") + pattern[index % len(pattern)] + cycle_lift
        rows.append(kline_row(index, interval_value=interval_value, close=close))
    return tuple(rows)


def strategy_input(
    *,
    base_rows: tuple[Any, ...] | None = None,
    higher_rows: tuple[Any, ...] | None = None,
) -> StrategyEvaluationInput:
    active_base_rows = base_rows or oscillating_rows(120, interval_value="4h")
    active_higher_rows = higher_rows or trending_rows(140, interval_value="1d", start="42000", step="45")
    return StrategyEvaluationInput(
        snapshot_id="MCS-23B",
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
        trace_id="trace-23b",
        evaluated_at_utc=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )


def test_market_direction_regime_strategy_outputs_context_result_with_private_payload() -> None:
    result = MarketDirectionRegimeStrategy(
        {
            "strategy_version": "23B-test",
            "strategy_role": "context",
            "provides": ["primary_regime", "regime_phase", "market_environment_context"],
            "minimum_required_bars": {"base": 80, "higher": 80},
        }
    ).evaluate(strategy_input())
    signal = adapt_strategy_result_to_signal(result)

    assert isinstance(result, StrategyResult)
    assert validate_strategy_result(result).passed is True
    assert result.strategy_role == "context"
    assert signal.strategy_role == "context"
    assert result.strategy_payload_json["primary_regime"] in {
        "uptrend",
        "downtrend",
        "range",
        "volatile",
        "mixed",
        "insufficient_data",
        "unknown",
    }
    assert "regime_phase" in result.strategy_payload_json
    common = result.common_result.to_jsonable()
    assert common["not_trading_advice"] is True
    assert common["context_summary"]
    assert common["primary_regime"] == result.strategy_payload_json["primary_regime"]
    assert common["regime_phase"] == result.strategy_payload_json["regime_phase"]
    assert common["trend_strength"] == result.strategy_payload_json["trend_strength"]
    assert common["decision_implication"] == result.strategy_payload_json["decision_implication"]
    assert common["market_environment_context"]


def test_short_term_range_strategy_outputs_context_result_with_private_range_fields() -> None:
    result = ShortTermRangeStrategy(
        {
            "strategy_version": "23B-test",
            "strategy_role": "context",
            "provides": ["short_term_range", "range_position", "range_quality"],
            "lookback_bars": {"base": 48},
            "minimum_required_bars": {"base": 36},
        }
    ).evaluate(strategy_input(base_rows=oscillating_rows(80)))

    assert validate_strategy_result(result).passed is True
    assert result.strategy_role == "context"
    payload = result.strategy_payload_json
    assert payload["recent_range_high"] is not None
    assert payload["recent_range_low"] is not None
    assert payload["range_position"] in {
        "above_range",
        "upper_edge",
        "upper_half",
        "middle",
        "lower_half",
        "lower_edge",
        "below_range",
        "unknown",
    }
    assert payload["range_quality"] in {"clear", "weak", "wide", "narrow", "noisy", "insufficient_data", "unknown"}
    common = result.common_result.to_jsonable()
    for private_key in ("recent_range_high", "recent_range_low", "range_position", "range_quality"):
        assert private_key not in common
    assert common["context_summary"] or common["evidence_items"]


def test_default_strategy_configs_declare_provides_and_allow_duplicate_context_role() -> None:
    registry = StrategyRegistry()

    strategies = registry.load_enabled_strategies()
    context_strategies = [strategy for strategy in strategies if getattr(strategy, "strategy_role", "") == "context"]

    assert len(context_strategies) >= 2
    assert {strategy.strategy_name for strategy in context_strategies} >= {
        "market_direction_regime",
        "short_term_range",
    }
    for strategy in context_strategies:
        assert tuple(strategy.provides)


def test_strategy_registry_reads_nested_23b_config_and_skips_disabled_strategy(tmp_path: Path) -> None:
    (tmp_path / "strategy_registry.yaml").write_text(
        "enabled_strategies:\n  - market_direction_regime\n  - short_term_range\n",
        encoding="utf-8",
    )
    (tmp_path / "market_direction_regime_strategy.yaml").write_text(
        "\n".join(
            (
                "enabled: true",
                "strategy_version: 23B-test",
                "strategy_role: context",
                "provides:",
                "  - primary_regime",
                "  - regime_phase",
                "lookback_bars:",
                "  base: 90",
                "  higher: 90",
                "minimum_required_bars:",
                "  base: 30",
                "  higher: 30",
                "thresholds:",
                "  trend_change_threshold: \"0.025\"",
            )
        ),
        encoding="utf-8",
    )
    (tmp_path / "short_term_range_strategy.yaml").write_text(
        "enabled: false\nstrategy_version: 23B-test\nstrategy_role: context\nprovides:\n  - short_term_range\n",
        encoding="utf-8",
    )
    registry = StrategyRegistry(
        config_dir=tmp_path,
        strategy_classes={
            "market_direction_regime": MarketDirectionRegimeStrategy,
            "short_term_range": ShortTermRangeStrategy,
        },
    )

    strategies = registry.load_enabled_strategies()

    assert len(strategies) == 1
    strategy = strategies[0]
    assert isinstance(strategy, MarketDirectionRegimeStrategy)
    assert strategy.base_lookback_bars == 90
    assert strategy.higher_lookback_bars == 90
    assert strategy.trend_change_threshold == Decimal("0.025")


def test_same_context_role_strategies_run_together_and_one_failure_is_isolated() -> None:
    runner = StrategyRunner(
        registry=FakeRegistry(
            (
                MarketDirectionRegimeStrategy({"minimum_required_bars": {"base": 40, "higher": 40}}),
                ShortTermRangeStrategy({"minimum_required_bars": {"base": 36}}),
                FailingStrategy(),
            )
        )
    )

    result = runner.run_strategies(strategy_input())

    assert result.status == StrategyRunStatus.PARTIAL_SUCCESS
    assert len(result.signals) == 3
    assert sum(1 for signal in result.signals if signal.strategy_role == "context") == 2
    assert sum(1 for signal in result.signals if signal.strategy_status == StrategySignalStatus.FAILED) == 1


def test_insufficient_data_outputs_private_insufficient_data_without_exception() -> None:
    input_data = strategy_input(base_rows=oscillating_rows(5), higher_rows=trending_rows(5, interval_value="1d"))

    regime = MarketDirectionRegimeStrategy({"minimum_required_bars": {"base": 80, "higher": 80}}).evaluate(input_data)
    range_result = ShortTermRangeStrategy({"minimum_required_bars": {"base": 36}}).evaluate(input_data)

    assert regime.strategy_status == "invalid"
    assert regime.strategy_payload_json["primary_regime"] == "insufficient_data"
    assert range_result.strategy_status == "invalid"
    assert range_result.strategy_payload_json["range_quality"] == "insufficient_data"
    assert validate_strategy_result(regime).passed is True
    assert validate_strategy_result(range_result).passed is True


def test_run_strategy_signals_persists_23b_contract_fields_without_private_leakage() -> None:
    session = FakeSession()
    runner = StrategyRunner(
        registry=FakeRegistry(
            (
                MarketDirectionRegimeStrategy({"minimum_required_bars": {"base": 40, "higher": 40}}),
                ShortTermRangeStrategy({"minimum_required_bars": {"base": 36}}),
            )
        )
    )
    service = StrategySignalService(
        input_builder=FakeInputBuilder(strategy_input()),
        runner=runner,
        result_repository=StrategySignalResultRepository(),
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            snapshot_id="MCS-23B",
            trigger_source="cli",
            dry_run=False,
            confirm_write=True,
            trace_id="trace-23b",
        ),
    )

    assert result.status == StrategyRunStatus.SUCCESS
    assert session.commits == 1
    result_rows = session.added[1:]
    assert len(result_rows) == 2
    for row in result_rows:
        assert row.strategy_role == "context"
        common = json.loads(row.common_payload_json)
        private_payload = json.loads(row.strategy_payload_json)
        assert common["not_trading_advice"] is True
        assert private_payload
        if row.strategy_name == "market_direction_regime":
            assert common["primary_regime"] == private_payload["primary_regime"]
            assert common["regime_phase"] == private_payload["regime_phase"]
            assert common["trend_strength"] == private_payload["trend_strength"]
            assert common["decision_implication"] == private_payload["decision_implication"]
        for private_key in ("recent_range_high", "recent_range_low", "range_position", "range_quality"):
            assert private_key not in common


def test_stage18_reads_23b_context_results_without_direction_vote_or_crash() -> None:
    input_data = strategy_input()
    signals = tuple(
        adapt_strategy_result_to_signal(strategy.evaluate(input_data))
        for strategy in (
            MarketDirectionRegimeStrategy({"minimum_required_bars": {"base": 40, "higher": 40}}),
            ShortTermRangeStrategy({"minimum_required_bars": {"base": 36}}),
        )
    )
    rows = tuple(
        SimpleNamespace(
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
        for signal in signals
    )

    summary = classify_strategy_results(rows)

    assert summary.effective_strategy_count == 2
    assert len(summary.long_strategies) == 0
    assert len(summary.short_strategies) == 0
    assert len(summary.neutral_strategies) + len(summary.risk_strategies) == 2
