from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.strategy.input_builder as input_builder_module
import app.strategy.result_repository as result_repository_module
import app.strategy.runner as runner_module
import app.strategy.signal_service as signal_service_module
import app.strategy.snapshot_resolver as snapshot_resolver_module
from app.market_context.snapshot_types import MarketContextSnapshotResult, MarketContextSnapshotStatus
from app.strategy.base import BaseStrategy
from app.strategy.input_builder import StrategyInputBuilder
from app.strategy.registry import StrategyRegistry
from app.strategy.result_repository import StrategySignalResultRepository
from app.strategy.runner import StrategyRunner
from app.strategy.signal_service import StrategySignalService
from app.strategy.snapshot_resolver import SnapshotResolver
from app.strategy.strategies.gann_placeholder_strategy import GannPlaceholderStrategy
from app.strategy.strategies.trend_structure_strategy import TrendStructureStrategy
from app.strategy.strategies.volatility_risk_strategy import VolatilityRiskStrategy
from app.strategy.types import (
    DirectionBias,
    EXIT_BLOCKED,
    EXIT_SUCCESS,
    RiskLevel,
    SnapshotResolveResult,
    StrategyConfigError,
    StrategyEvaluationInput,
    StrategyRunStatus,
    StrategyRunnerResult,
    StrategySignal,
    StrategySignalRunRequest,
    StrategySignalRunResult,
    StrategySignalStatus,
)
from scripts import run_strategy_signals as strategy_cli

CURRENT_TIME_MS = int(datetime(2026, 5, 16, 8, 10, tzinfo=timezone.utc).timestamp() * 1000)
EXPECTED_4H_LATEST_MS = int(datetime(2026, 5, 16, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
EXPECTED_1D_LATEST_MS = int(datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

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


class FakeSnapshotRepository:
    def __init__(self, snapshots: list[Any] | None = None) -> None:
        self.snapshots = {row.snapshot_id: row for row in snapshots or []}
        self.restored_windows: dict[str, tuple[tuple[Any, ...], tuple[Any, ...]]] = {}
        self.reusable_calls = 0
        self.restore_calls: list[str] = []
        self.formal_kline_write_attempted = False

    def add_snapshot_with_rows(self, snapshot: Any, rows_4h: tuple[Any, ...], rows_1d: tuple[Any, ...]) -> None:
        self.snapshots[snapshot.snapshot_id] = snapshot
        self.restored_windows[snapshot.snapshot_id] = (rows_4h, rows_1d)

    def get_snapshot_by_snapshot_id(self, _db_session: Any, *, snapshot_id: str) -> Any | None:
        return self.snapshots.get(snapshot_id)

    def restore_snapshot_kline_windows(self, _db_session: Any, *, snapshot_id: str) -> Any:
        self.restore_calls.append(snapshot_id)
        snapshot = self.snapshots[snapshot_id]
        rows_4h, rows_1d = self.restored_windows[snapshot_id]
        if len(rows_4h) != snapshot.actual_4h_count:
            raise RuntimeError("4h restore count mismatch")
        if len(rows_1d) != snapshot.actual_1d_count:
            raise RuntimeError("1d restore count mismatch")
        if [row.open_time_ms for row in rows_4h] != sorted(row.open_time_ms for row in rows_4h):
            raise RuntimeError("4h rows are not ordered")
        if [row.open_time_ms for row in rows_1d] != sorted(row.open_time_ms for row in rows_1d):
            raise RuntimeError("1d rows are not ordered")
        return SimpleNamespace(snapshot=snapshot, rows_4h=rows_4h, rows_1d=rows_1d)

    def list_reusable_created_snapshots(
        self,
        _db_session: Any,
        *,
        symbol: str,
        base_interval_value: str,
        higher_interval_value: str,
        lookback_base_count: int,
        lookback_higher_count: int,
        expected_base_open_time_ms: int,
        expected_higher_open_time_ms: int,
    ) -> tuple[Any, ...]:
        self.reusable_calls += 1
        candidates = [
            snapshot
            for snapshot in self.snapshots.values()
            if snapshot.status == "created"
            and snapshot.symbol == symbol
            and snapshot.base_interval_value == base_interval_value
            and snapshot.higher_interval_value == higher_interval_value
            and snapshot.lookback_4h_count == lookback_base_count
            and snapshot.lookback_1d_count == lookback_higher_count
            and snapshot.end_4h_open_time_ms >= expected_base_open_time_ms
            and snapshot.end_1d_open_time_ms >= expected_higher_open_time_ms
            and snapshot.latest_4h_data_quality_status in {"healthy", "passed"}
            and snapshot.latest_1d_data_quality_status in {"healthy", "passed"}
        ]
        return tuple(sorted(candidates, key=lambda item: item.created_at_utc, reverse=True))

    def bulk_upsert(self, *_args: Any, **_kwargs: Any) -> None:
        self.formal_kline_write_attempted = True
        raise AssertionError("strategy framework must not write formal Kline tables")


class FakeInputBuilder:
    def __init__(self, input_data: StrategyEvaluationInput | None = None, fail_message: str | None = None) -> None:
        self.input_data = input_data or build_strategy_input()
        self.fail_message = fail_message
        self.calls: list[dict[str, Any]] = []

    def build_input_from_snapshot(self, _db_session: Any, **kwargs: Any) -> StrategyEvaluationInput:
        self.calls.append(kwargs)
        if self.fail_message:
            from app.strategy.types import StrategyInputBuildError

            raise StrategyInputBuildError(self.fail_message)
        return self.input_data


class FakeRunner:
    def __init__(self, result: StrategyRunnerResult | None = None) -> None:
        self.result = result or StrategyRunnerResult(
            status=StrategyRunStatus.PARTIAL_SUCCESS,
            signals=(
                success_signal("trend_structure"),
                StrategySignal(
                    strategy_name="gann_placeholder",
                    strategy_version="placeholder_v1",
                    strategy_status=StrategySignalStatus.NOT_IMPLEMENTED,
                    direction_bias=DirectionBias.NOT_APPLICABLE,
                    risk_level=RiskLevel.NOT_APPLICABLE,
                    reason_codes=("gann_strategy_not_implemented",),
                    reason_text="江恩策略尚未实现，本阶段仅保留扩展位。",
                    trace_id="trace-test",
                ),
            ),
            message="independent signals completed",
        )
        self.calls: list[StrategyEvaluationInput] = []

    def run_strategies(self, input_data: StrategyEvaluationInput) -> StrategyRunnerResult:
        self.calls.append(input_data)
        return self.result


class FakeResolver:
    def __init__(self, result: SnapshotResolveResult | None = None, fail_message: str | None = None) -> None:
        self.result = result
        self.fail_message = fail_message
        self.calls: list[dict[str, Any]] = []

    def ensure_latest_snapshot(self, _db_session: Any, **kwargs: Any) -> SnapshotResolveResult:
        self.calls.append(kwargs)
        if self.fail_message:
            raise RuntimeError(self.fail_message)
        assert self.result is not None
        return self.result


class FailingResultRepository:
    def __init__(self) -> None:
        self.create_calls = 0

    def create_strategy_signal_run_with_results(self, *_args: Any, **_kwargs: Any) -> None:
        self.create_calls += 1
        raise AssertionError("dry-run must not write strategy signal tables")


class PassingStrategy(BaseStrategy):
    strategy_name = "passing"
    strategy_version = "v1"

    def __init__(self, _config: Any | None = None) -> None:
        pass

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategySignal:
        return success_signal(self.strategy_name, trace_id=input_data.trace_id)


class FailingStrategy(BaseStrategy):
    strategy_name = "failing"
    strategy_version = "v1"

    def __init__(self, _config: Any | None = None) -> None:
        pass

    def evaluate(self, _input_data: StrategyEvaluationInput) -> StrategySignal:
        raise RuntimeError("strategy failed")


class FakeRegistry:
    def __init__(self, strategies: tuple[BaseStrategy, ...]) -> None:
        self.strategies = strategies

    def load_enabled_strategies(self) -> tuple[BaseStrategy, ...]:
        return self.strategies


def kline_row(index: int, *, interval_value: str = "4h") -> Any:
    interval_ms = 14_400_000 if interval_value == "4h" else 86_400_000
    start_ms = 1_700_000_000_000
    close = Decimal("50000") + Decimal(index * 10)
    return SimpleNamespace(
        symbol="BTCUSDT",
        interval_value=interval_value,
        open_time_ms=start_ms + index * interval_ms,
        open_time_utc=datetime.fromtimestamp((start_ms + index * interval_ms) / 1000, tz=timezone.utc),
        high_price=close + Decimal("120"),
        low_price=close - Decimal("100"),
        close_price=close,
    )


def build_rows(count: int, *, interval_value: str = "4h") -> tuple[Any, ...]:
    return tuple(kline_row(index, interval_value=interval_value) for index in range(count))


def snapshot_row(
    *,
    snapshot_id: str = "MCS-BTCUSDT-4H-1D-created",
    status: str = "created",
    rows_4h: tuple[Any, ...] | None = None,
    rows_1d: tuple[Any, ...] | None = None,
    end_4h_open_time_ms: int = EXPECTED_4H_LATEST_MS,
    end_1d_open_time_ms: int = EXPECTED_1D_LATEST_MS,
) -> Any:
    base_rows = rows_4h or build_rows(180, interval_value="4h")
    higher_rows = rows_1d or build_rows(365, interval_value="1d")
    return SimpleNamespace(
        id=1,
        snapshot_id=snapshot_id,
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        status=status,
        lookback_4h_count=len(base_rows),
        lookback_1d_count=len(higher_rows),
        actual_4h_count=len(base_rows),
        actual_1d_count=len(higher_rows),
        start_4h_open_time_ms=base_rows[0].open_time_ms,
        end_4h_open_time_ms=end_4h_open_time_ms,
        start_1d_open_time_ms=higher_rows[0].open_time_ms,
        end_1d_open_time_ms=end_1d_open_time_ms,
        latest_4h_open_time_ms=end_4h_open_time_ms,
        latest_1d_open_time_ms=end_1d_open_time_ms,
        latest_4h_quality_check_id=301,
        latest_1d_quality_check_id=401,
        latest_4h_data_quality_status="healthy",
        latest_1d_data_quality_status="healthy",
        created_at_utc=datetime(2026, 5, 16, 8, 5, tzinfo=timezone.utc),
        trace_id="trace-snapshot",
    )


def build_strategy_input() -> StrategyEvaluationInput:
    rows_4h = build_rows(180, interval_value="4h")
    rows_1d = build_rows(365, interval_value="1d")
    return StrategyEvaluationInput(
        snapshot_id="MCS-BTCUSDT-4H-1D-created",
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        base_klines=rows_4h,
        higher_klines=rows_1d,
        lookback_base_count=180,
        lookback_higher_count=365,
        latest_base_open_time_ms=rows_4h[-1].open_time_ms,
        latest_higher_open_time_ms=rows_1d[-1].open_time_ms,
        base_start_open_time_ms=rows_4h[0].open_time_ms,
        base_end_open_time_ms=rows_4h[-1].open_time_ms,
        higher_start_open_time_ms=rows_1d[0].open_time_ms,
        higher_end_open_time_ms=rows_1d[-1].open_time_ms,
        base_quality_check_id=301,
        higher_quality_check_id=401,
        trace_id="trace-test",
        evaluated_at_utc=datetime(2026, 5, 16, 8, 10, tzinfo=timezone.utc),
    )


def success_signal(strategy_name: str, *, trace_id: str = "trace-test") -> StrategySignal:
    return StrategySignal(
        strategy_name=strategy_name,
        strategy_version="v1",
        strategy_status=StrategySignalStatus.SUCCESS,
        direction_bias=DirectionBias.NEUTRAL,
        risk_level=RiskLevel.MEDIUM,
        signal_strength=0.5,
        reason_codes=("stable_code",),
        reason_text="策略独立信号已生成。",
        metrics={"sample_metric": "1.0"},
        debug_info={"debug_scope": "non_sensitive"},
        trace_id=trace_id,
    )


def test_snapshot_resolver_reuses_latest_created_snapshot_without_generation() -> None:
    rows_4h = build_rows(180, interval_value="4h")
    rows_1d = build_rows(365, interval_value="1d")
    snapshot = snapshot_row(rows_4h=rows_4h, rows_1d=rows_1d)
    repository = FakeSnapshotRepository()
    repository.add_snapshot_with_rows(snapshot, rows_4h, rows_1d)

    def fail_snapshot_service(**_kwargs: Any) -> Any:
        raise AssertionError("reusable snapshot must avoid new snapshot generation")

    resolver = SnapshotResolver(snapshot_repository=repository, snapshot_service=fail_snapshot_service)
    result = resolver.ensure_latest_snapshot(
        FakeSession(),
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        lookback_base_count=180,
        lookback_higher_count=365,
        dry_run=True,
        confirm_write=False,
        current_time_ms=CURRENT_TIME_MS,
        trace_id="trace-test",
    )

    assert result.status == StrategyRunStatus.SUCCESS
    assert result.snapshot_id == snapshot.snapshot_id
    assert result.reused_existing_snapshot is True
    assert result.created_new_snapshot is False
    assert repository.reusable_calls == 1
    assert repository.restore_calls == [snapshot.snapshot_id]


def test_snapshot_resolver_blocks_dry_run_without_reusable_snapshot_and_without_generation() -> None:
    repository = FakeSnapshotRepository()
    service_calls: list[Any] = []

    def fail_snapshot_service(**kwargs: Any) -> Any:
        service_calls.append(kwargs)
        raise AssertionError("dry-run must not create MarketContextSnapshot")

    resolver = SnapshotResolver(snapshot_repository=repository, snapshot_service=fail_snapshot_service)
    result = resolver.ensure_latest_snapshot(
        FakeSession(),
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        lookback_base_count=180,
        lookback_higher_count=365,
        dry_run=True,
        confirm_write=False,
        current_time_ms=CURRENT_TIME_MS,
        trace_id="trace-test",
    )

    assert result.status == StrategyRunStatus.BLOCKED
    assert result.snapshot_id is None
    assert result.blocked_reason == "snapshot_creation_requires_confirm_write"
    assert "不会创建新快照" in result.message
    assert service_calls == []


def test_snapshot_resolver_generates_snapshot_when_no_reusable_snapshot() -> None:
    rows_4h = build_rows(180, interval_value="4h")
    rows_1d = build_rows(365, interval_value="1d")
    repository = FakeSnapshotRepository()
    service_calls: list[Any] = []

    def fake_snapshot_service(**kwargs: Any) -> Any:
        service_calls.append(kwargs)
        created_snapshot = snapshot_row(
            snapshot_id="MCS-BTCUSDT-4H-1D-new",
            rows_4h=rows_4h,
            rows_1d=rows_1d,
        )
        repository.add_snapshot_with_rows(created_snapshot, rows_4h, rows_1d)
        return MarketContextSnapshotResult(
            status=MarketContextSnapshotStatus.CREATED,
            exit_code=0,
            trace_id="trace-test",
            snapshot_id=created_snapshot.snapshot_id,
        )

    resolver = SnapshotResolver(snapshot_repository=repository, snapshot_service=fake_snapshot_service)
    result = resolver.ensure_latest_snapshot(
        FakeSession(),
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        lookback_base_count=180,
        lookback_higher_count=365,
        dry_run=False,
        confirm_write=True,
        current_time_ms=CURRENT_TIME_MS,
        trace_id="trace-test",
    )

    assert result.status == StrategyRunStatus.SUCCESS
    assert result.snapshot_id == "MCS-BTCUSDT-4H-1D-new"
    assert result.created_new_snapshot is True
    assert len(service_calls) == 1
    assert service_calls[0]["request"].confirm_write is True
    assert service_calls[0]["request"].dry_run is False


def test_snapshot_resolver_blocks_when_snapshot_build_is_blocked_without_old_fallback() -> None:
    stale_rows_4h = build_rows(180, interval_value="4h")
    stale_rows_1d = build_rows(365, interval_value="1d")
    stale_snapshot = snapshot_row(
        snapshot_id="MCS-BTCUSDT-4H-1D-old",
        rows_4h=stale_rows_4h,
        rows_1d=stale_rows_1d,
        end_4h_open_time_ms=EXPECTED_4H_LATEST_MS - 14_400_000,
        end_1d_open_time_ms=EXPECTED_1D_LATEST_MS,
    )
    repository = FakeSnapshotRepository()
    repository.add_snapshot_with_rows(stale_snapshot, stale_rows_4h, stale_rows_1d)

    def blocked_snapshot_service(**_kwargs: Any) -> Any:
        return MarketContextSnapshotResult(
            status=MarketContextSnapshotStatus.BLOCKED,
            exit_code=2,
            trace_id="trace-test",
            snapshot_id="MCS-BTCUSDT-4H-1D-blocked",
            blocked_reason="snapshot_not_ready",
        )

    resolver = SnapshotResolver(snapshot_repository=repository, snapshot_service=blocked_snapshot_service)
    result = resolver.ensure_latest_snapshot(
        FakeSession(),
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        lookback_base_count=180,
        lookback_higher_count=365,
        dry_run=False,
        confirm_write=True,
        current_time_ms=CURRENT_TIME_MS,
        trace_id="trace-test",
    )

    assert result.status == StrategyRunStatus.BLOCKED
    assert result.snapshot_id == "MCS-BTCUSDT-4H-1D-blocked"
    assert result.snapshot_id != stale_snapshot.snapshot_id
    assert repository.restore_calls == []


def test_strategy_input_builder_restores_base_and_higher_windows() -> None:
    rows_4h = build_rows(180, interval_value="4h")
    rows_1d = build_rows(365, interval_value="1d")
    snapshot = snapshot_row(rows_4h=rows_4h, rows_1d=rows_1d)
    repository = FakeSnapshotRepository()
    repository.add_snapshot_with_rows(snapshot, rows_4h, rows_1d)
    builder = StrategyInputBuilder(snapshot_repository=repository)

    input_data = builder.build_input_from_snapshot(
        FakeSession(),
        snapshot_id=snapshot.snapshot_id,
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        trace_id="trace-test",
    )

    assert input_data.snapshot_id == snapshot.snapshot_id
    assert len(input_data.base_klines) == snapshot.actual_4h_count
    assert len(input_data.higher_klines) == snapshot.actual_1d_count
    assert [row.open_time_ms for row in input_data.base_klines] == sorted(
        row.open_time_ms for row in input_data.base_klines
    )
    assert [row.open_time_ms for row in input_data.higher_klines] == sorted(
        row.open_time_ms for row in input_data.higher_klines
    )


def test_strategy_registry_loads_configs_and_rejects_duplicates(tmp_path: Path) -> None:
    (tmp_path / "strategy_registry.yaml").write_text(
        "enabled_strategies:\n  - passing\n\ndefault_base_interval: 4h\n",
        encoding="utf-8",
    )
    (tmp_path / "passing_strategy.yaml").write_text("enabled: true\n", encoding="utf-8")
    registry = StrategyRegistry(config_dir=tmp_path, strategy_classes={"passing": PassingStrategy})

    strategies = registry.load_enabled_strategies()

    assert len(strategies) == 1
    assert strategies[0].strategy_name == "passing"

    (tmp_path / "strategy_registry.yaml").write_text(
        "enabled_strategies:\n  - passing\n  - passing\n",
        encoding="utf-8",
    )
    with pytest.raises(StrategyConfigError):
        registry.load_enabled_strategies()


def test_strategy_registry_rejects_non_base_strategy(tmp_path: Path) -> None:
    class NotStrategy:
        strategy_name = "bad"
        strategy_version = "v1"

        def __init__(self, _config: Any | None = None) -> None:
            pass

    (tmp_path / "strategy_registry.yaml").write_text("enabled_strategies:\n  - bad\n", encoding="utf-8")
    (tmp_path / "bad_strategy.yaml").write_text("enabled: true\n", encoding="utf-8")
    registry = StrategyRegistry(config_dir=tmp_path, strategy_classes={"bad": NotStrategy})  # type: ignore[arg-type]

    with pytest.raises(StrategyConfigError):
        registry.load_enabled_strategies()


def test_strategy_runner_isolates_one_strategy_failure() -> None:
    runner = StrategyRunner(registry=FakeRegistry((PassingStrategy(), FailingStrategy(), PassingStrategy())))

    result = runner.run_strategies(build_strategy_input())

    assert result.status == StrategyRunStatus.PARTIAL_SUCCESS
    assert len(result.signals) == 3
    assert sum(1 for signal in result.signals if signal.strategy_status == StrategySignalStatus.FAILED) == 1
    assert result.signals[0].strategy_status == StrategySignalStatus.SUCCESS
    assert result.signals[2].strategy_status == StrategySignalStatus.SUCCESS


def test_initial_strategies_emit_independent_signals_without_trade_fields() -> None:
    input_data = build_strategy_input()

    signals = (
        TrendStructureStrategy(
            {"ma_short_period": 18, "ma_mid_period": 54, "min_required_base_klines": 120}
        ).evaluate(input_data),
        VolatilityRiskStrategy(
            {
                "lookback_period": 28,
                "min_required_base_klines": 120,
                "high_volatility_percentile": "0.75",
                "extreme_volatility_percentile": "0.92",
            }
        ).evaluate(input_data),
        GannPlaceholderStrategy({"placeholder_note": "stage_16_only"}).evaluate(input_data),
    )

    assert signals[0].strategy_status in {StrategySignalStatus.SUCCESS, StrategySignalStatus.NO_SIGNAL}
    assert signals[1].strategy_status == StrategySignalStatus.SUCCESS
    assert signals[2].strategy_status == StrategySignalStatus.NOT_IMPLEMENTED
    assert signals[0].debug_info["strategy_config"] == {
        "ma_short_period": 18,
        "ma_mid_period": 54,
        "min_required_base_klines": 120,
    }
    assert signals[0].debug_info["snapshot_id"] == input_data.snapshot_id
    assert signals[0].debug_info["higher_interval_value"] == "1d"
    assert signals[1].debug_info["strategy_config"] == {
        "lookback_period": 28,
        "min_required_base_klines": 120,
        "high_volatility_percentile": "0.75",
        "extreme_volatility_percentile": "0.92",
    }
    assert signals[1].debug_info["snapshot_id"] == input_data.snapshot_id
    assert signals[2].debug_info["strategy_boundary"] == "independent_signal_only"
    assert signals[2].debug_info["implementation_status"] == "placeholder"
    assert signals[2].debug_info["strategy_config"] == {"placeholder_note": "stage_16_only"}
    assert all(signal.reason_text for signal in signals)
    assert all(any(ord(char) > 127 for char in signal.reason_text) for signal in signals)
    serialized = json.dumps([signal.__dict__ for signal in signals], ensure_ascii=False, default=str)
    forbidden = (
        "open_position",
        "close_position",
        "take_profit",
        "stop_loss",
        "position_size",
        "leverage",
        "buy",
        "sell",
    )
    for word in forbidden:
        assert word not in serialized


def test_signal_service_dry_run_does_not_write_strategy_tables() -> None:
    session = FakeSession()
    result_repository = FailingResultRepository()
    service = StrategySignalService(
        input_builder=FakeInputBuilder(),
        runner=FakeRunner(),
        result_repository=result_repository,
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            snapshot_id="MCS-BTCUSDT-4H-1D-created",
            trigger_source="cli",
            dry_run=True,
            confirm_write=False,
            trace_id="trace-test",
        ),
    )

    assert result.exit_code == EXIT_SUCCESS
    assert result.status == StrategyRunStatus.PARTIAL_SUCCESS
    assert session.added == []
    assert session.commits == 0
    assert session.rollbacks == 0
    assert result_repository.create_calls == 0


def test_signal_service_accepts_scheduler_trigger_for_stage17_app_call() -> None:
    session = FakeSession()
    service = StrategySignalService(
        input_builder=FakeInputBuilder(),
        runner=FakeRunner(),
        result_repository=FailingResultRepository(),
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            snapshot_id="MCS-BTCUSDT-4H-1D-created",
            trigger_source="scheduler",
            dry_run=True,
            confirm_write=False,
            trace_id="trace-scheduler",
        ),
    )

    assert result.status == StrategyRunStatus.PARTIAL_SUCCESS
    assert result.exit_code == EXIT_SUCCESS
    assert session.added == []


def test_signal_service_confirm_write_persists_run_and_independent_results() -> None:
    session = FakeSession()
    service = StrategySignalService(
        input_builder=FakeInputBuilder(),
        runner=FakeRunner(),
        result_repository=StrategySignalResultRepository(),
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            snapshot_id="MCS-BTCUSDT-4H-1D-created",
            trigger_source="cli",
            dry_run=False,
            confirm_write=True,
            trace_id="trace-test",
        ),
    )

    assert result.exit_code == EXIT_SUCCESS
    assert "策略信号运行记录和结果记录已写入。" in result.message
    assert "Strategy signal run/result rows have been written" not in result.message
    assert session.commits == 1
    assert len(session.added) == 3
    run_row = session.added[0]
    result_rows = session.added[1:]
    assert run_row.run_id == result.run_id
    assert run_row.snapshot_id == result.snapshot_id
    assert run_row.status == StrategyRunStatus.PARTIAL_SUCCESS.value
    assert run_row.strategy_count == 2
    assert len(result_rows) == 2
    for row in result_rows:
        assert json.loads(row.reason_codes_json)
        assert json.loads(row.metrics_json) is not None
        assert "open_position" not in row.metrics_json
        assert row.snapshot_id == "MCS-BTCUSDT-4H-1D-created"


def test_signal_service_confirm_write_blocked_persists_only_run_audit_message() -> None:
    session = FakeSession()
    resolver = FakeResolver(
        SnapshotResolveResult(
            status=StrategyRunStatus.BLOCKED,
            snapshot_id=None,
            message="当前没有可复用的最新 MarketContextSnapshot，策略信号运行被阻断。",
            blocked_reason="snapshot_creation_requires_confirm_write",
            trace_id="trace-test",
        )
    )
    service = StrategySignalService(
        snapshot_resolver=resolver,  # type: ignore[arg-type]
        input_builder=FakeInputBuilder(),
        runner=FakeRunner(),
        result_repository=StrategySignalResultRepository(),
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            ensure_latest_snapshot=True,
            trigger_source="cli",
            dry_run=False,
            confirm_write=True,
            trace_id="trace-test",
            current_time_ms=CURRENT_TIME_MS,
        ),
    )

    assert result.status == StrategyRunStatus.BLOCKED
    assert result.exit_code == EXIT_BLOCKED
    assert result.signals == ()
    assert session.commits == 1
    assert len(session.added) == 1
    run_row = session.added[0]
    assert run_row.run_id == result.run_id
    assert run_row.status == StrategyRunStatus.BLOCKED.value
    assert "策略信号运行审计记录已写入，未写入策略结果记录。" in result.message
    assert "Strategy signal run/result rows have been written" not in result.message


def test_signal_service_ensure_latest_snapshot_uses_resolver_and_blocks_when_snapshot_blocked() -> None:
    session = FakeSession()
    resolver = FakeResolver(
        SnapshotResolveResult(
            status=StrategyRunStatus.BLOCKED,
            snapshot_id="MCS-BTCUSDT-4H-1D-blocked",
            message="snapshot blocked",
            blocked_reason="snapshot_not_ready",
            trace_id="trace-test",
        )
    )
    service = StrategySignalService(
        snapshot_resolver=resolver,  # type: ignore[arg-type]
        input_builder=FakeInputBuilder(),
        runner=FakeRunner(),
        result_repository=StrategySignalResultRepository(),
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            ensure_latest_snapshot=True,
            trigger_source="cli",
            dry_run=True,
            confirm_write=False,
            trace_id="trace-test",
            current_time_ms=CURRENT_TIME_MS,
        ),
    )

    assert result.status == StrategyRunStatus.BLOCKED
    assert result.exit_code == EXIT_BLOCKED
    assert result.blocked_reason == "snapshot_not_ready"
    assert len(resolver.calls) == 1
    assert resolver.calls[0]["dry_run"] is True
    assert resolver.calls[0]["confirm_write"] is False
    assert session.added == []


def test_signal_service_ensure_latest_snapshot_success_continues_to_runner() -> None:
    session = FakeSession()
    resolver = FakeResolver(
        SnapshotResolveResult(
            status=StrategyRunStatus.SUCCESS,
            snapshot_id="MCS-BTCUSDT-4H-1D-latest",
            message="reused",
            reused_existing_snapshot=True,
            trace_id="trace-test",
        )
    )
    input_builder = FakeInputBuilder()
    runner = FakeRunner()
    service = StrategySignalService(
        snapshot_resolver=resolver,  # type: ignore[arg-type]
        input_builder=input_builder,
        runner=runner,
        result_repository=StrategySignalResultRepository(),
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            ensure_latest_snapshot=True,
            trigger_source="cli",
            dry_run=True,
            confirm_write=False,
            trace_id="trace-test",
            current_time_ms=CURRENT_TIME_MS,
        ),
    )

    assert result.status == StrategyRunStatus.PARTIAL_SUCCESS
    assert input_builder.calls[0]["snapshot_id"] == "MCS-BTCUSDT-4H-1D-latest"
    assert resolver.calls[0]["dry_run"] is True
    assert resolver.calls[0]["confirm_write"] is False
    assert len(runner.calls) == 1


def test_signal_service_returns_structured_failed_when_resolver_raises() -> None:
    session = FakeSession()
    resolver = FakeResolver(fail_message="database unavailable")
    service = StrategySignalService(
        snapshot_resolver=resolver,  # type: ignore[arg-type]
        input_builder=FakeInputBuilder(),
        runner=FakeRunner(),
        result_repository=StrategySignalResultRepository(),
    )

    result = service.run_strategy_signals(
        session,
        request=StrategySignalRunRequest(
            ensure_latest_snapshot=True,
            trigger_source="cli",
            dry_run=True,
            confirm_write=False,
            trace_id="trace-test",
            current_time_ms=CURRENT_TIME_MS,
        ),
    )

    assert result.status == StrategyRunStatus.FAILED
    assert result.error_message == "database unavailable"
    assert session.rollbacks == 1
    assert session.added == []


def test_cli_only_parses_arguments_and_calls_app_service(monkeypatch: Any) -> None:
    fake_session = object()
    captured: dict[str, Any] = {}

    @contextmanager
    def fake_session_scope(*, commit_on_success: bool = False) -> Any:
        captured["commit_on_success"] = commit_on_success
        yield fake_session

    def fake_run_strategy_signals(*, db_session: Any, request: StrategySignalRunRequest) -> StrategySignalRunResult:
        captured["db_session"] = db_session
        captured["request"] = request
        return StrategySignalRunResult(
            status=StrategyRunStatus.SUCCESS,
            exit_code=0,
            run_id="SSR-test",
            trace_id="trace-test",
            snapshot_id="MCS-test",
            message="ok",
        )

    monkeypatch.setattr("app.storage.mysql.session.session_scope", fake_session_scope)
    monkeypatch.setattr(strategy_cli, "run_strategy_signals", fake_run_strategy_signals)

    exit_code = strategy_cli.main(
        [
            "--symbol",
            "btcusdt",
            "--base-interval",
            "4h",
            "--higher-interval",
            "1d",
            "--ensure-latest-snapshot",
            "--lookback-base",
            "200",
            "--lookback-higher",
            "400",
            "--trigger-source",
            "cli",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert captured["db_session"] is fake_session
    request = captured["request"]
    assert request.symbol == "BTCUSDT"
    assert request.ensure_latest_snapshot is True
    assert request.lookback_base_count == 200
    assert request.lookback_higher_count == 400
    assert request.dry_run is True


def test_strategy_signal_result_run_id_has_model_foreign_key() -> None:
    from app.storage.mysql.models.strategy_signal import StrategySignalResult

    foreign_keys = tuple(StrategySignalResult.__table__.c.run_id.foreign_keys)

    assert any(foreign_key.target_fullname == "strategy_signal_run.run_id" for foreign_key in foreign_keys)


def test_strategy_signal_migration_declares_run_id_foreign_key_without_cascade() -> None:
    migration_text = Path("migrations/versions/20260518_16_create_strategy_signal_tables.py").read_text(
        encoding="utf-8"
    )

    assert "ForeignKeyConstraint" in migration_text
    assert "fk_strategy_signal_result_run_id" in migration_text
    assert "strategy_signal_run.run_id" in migration_text
    assert "ondelete" not in migration_text.lower()


def test_strategy_modules_do_not_import_exchange_alerting_or_kline_write_apis() -> None:
    modules = (
        input_builder_module,
        result_repository_module,
        runner_module,
        signal_service_module,
        snapshot_resolver_module,
    )
    source = "\n".join(inspect.getsource(module) for module in modules)

    assert "app.exchange" not in source
    assert "app.alerting" not in source
    assert "BinanceRestClient" not in source
    assert "DeepSeekClient" not in source
    assert "bulk_upsert" not in source
    assert "/fapi/v1" not in source
