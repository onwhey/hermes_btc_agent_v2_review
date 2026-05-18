from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.config import AppSettings
from app.scheduler.config import SchedulerRuntimeConfig
from app.scheduler.runner import SchedulerRunner
from app.scheduler.slot_state import (
    KLINE_4H_INCREMENTAL_JOB_NAME,
    SchedulerSlotAction,
    SchedulerSlotDecision,
    SchedulerSlotStatus,
)
from app.scheduler.strategy_signal_scheduler_types import (
    StrategySignalSchedulerResult,
    StrategySignalSchedulerStatus,
)
from app.strategy.aggregation.types import AnalysisHypothesisDirection, StrategyAggregationResult, StrategyAggregationStatus
from app.market_data.collector.types import EXIT_SUCCESS, IncrementalKlineCollectResult, KlineCollectStatus


def utc_at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 5, day, hour, minute, tzinfo=timezone.utc)


def runtime_config(**overrides: Any) -> SchedulerRuntimeConfig:
    data = {
        "enabled": True,
        "poll_interval_seconds": 30,
        "running_lock_ttl_seconds": 1800,
        "completed_marker_ttl_seconds": 259200,
        "status_marker_ttl_seconds": 86400,
        "slot_log_cooldown_seconds": 300,
        "kline_4h_incremental_collect_enabled": True,
        "kline_4h_incremental_collect_symbol": "BTCUSDT",
        "kline_4h_incremental_collect_interval": "4h",
        "kline_4h_incremental_collect_limit": 6,
        "kline_4h_incremental_collect_utc_minutes_after_close": 5,
        "kline_1d_incremental_collect_enabled": False,
        "kline_1d_incremental_collect_symbol": "BTCUSDT",
        "kline_1d_incremental_collect_interval": "1d",
        "kline_1d_incremental_collect_max_closed_count": 30,
        "kline_1d_incremental_collect_lock_ttl_seconds": 300,
        "kline_1d_incremental_collect_utc_time": utc_at(18, 0, 10).time(),
        "daily_kline_integrity_enabled": False,
        "daily_kline_integrity_symbol": "BTCUSDT",
        "daily_kline_integrity_interval": "4h",
        "daily_kline_integrity_limit": 100,
        "daily_kline_integrity_utc_time": utc_at(18, 0, 30).time(),
        "daily_kline_1d_integrity_enabled": False,
        "daily_kline_1d_integrity_symbol": "BTCUSDT",
        "daily_kline_1d_integrity_interval": "1d",
        "daily_kline_1d_integrity_limit": 500,
        "daily_kline_1d_integrity_notify_success": True,
        "daily_kline_1d_integrity_lock_ttl_seconds": 1800,
        "daily_kline_1d_integrity_utc_time": utc_at(18, 0, 20).time(),
        "strategy_signal_scheduler_enabled": True,
        "strategy_signal_symbol": "BTCUSDT",
        "strategy_signal_base_interval": "4h",
        "strategy_signal_higher_interval": "1d",
        "strategy_signal_hermes_enabled": False,
        "strategy_signal_hermes_notify_success": True,
        "strategy_signal_hermes_notify_partial_success": True,
        "strategy_signal_hermes_notify_blocked": True,
        "strategy_signal_hermes_notify_failed": True,
        "strategy_signal_hermes_notify_skipped": False,
        "strategy_signal_scheduler_running_timeout_seconds": 900,
        "strategy_aggregation_auto_run_enabled": True,
    }
    data.update(overrides)
    return SchedulerRuntimeConfig(**data)


class FakeSlotStore:
    def acquire_slot_for_run(self, **kwargs: Any) -> SchedulerSlotDecision:
        return SchedulerSlotDecision(
            job=kwargs["job"],
            slot=kwargs["slot"],
            action=SchedulerSlotAction.ACQUIRED,
            status=SchedulerSlotStatus.RUNNING,
            running_key="running",
            completed_key="completed",
            status_key="status",
            owner=kwargs["owner"],
            reason="acquired",
            ttl_seconds=kwargs["running_ttl_seconds"],
            running_value="running-value",
        )

    def mark_slot_completed(self, **_kwargs: Any) -> None:
        return None

    def mark_slot_status(self, **_kwargs: Any) -> None:
        raise AssertionError("collector success should be completed")

    def release_running_lock(self, **_kwargs: Any) -> bool:
        return True


def success_collect_result() -> IncrementalKlineCollectResult:
    return IncrementalKlineCollectResult(
        status=KlineCollectStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        trace_id="collect-trace",
        message="ok",
        event_log_id=501,
    )


def test_scheduler_auto_runs_stage18_only_after_stage17_success() -> None:
    stage18_calls: list[Any] = []

    def collect_job() -> IncrementalKlineCollectResult:
        return success_collect_result()

    def stage17_hook(**_kwargs: Any) -> StrategySignalSchedulerResult:
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SUCCESS,
            event_id="SSS-test",
            run_id="SSR-test",
            snapshot_id="MCS-test",
            trace_id="trace-17",
            message="stage17 ok",
            target_base_open_time_ms=1,
        )

    def stage18_hook(**kwargs: Any) -> StrategyAggregationResult:
        stage18_calls.append(kwargs)
        return StrategyAggregationResult(
            status=StrategyAggregationStatus.SUCCESS,
            exit_code=0,
            aggregation_run_id="SAR-test",
            material_pack_id="AMP-test",
            strategy_signal_run_id="SSR-test",
            snapshot_id="MCS-test",
            trace_id="trace-18",
            analysis_hypothesis_direction=AnalysisHypothesisDirection.LONG,
            message="stage18 ok",
        )

    runner = SchedulerRunner(
        config=runtime_config(),
        slot_store=FakeSlotStore(),
        settings=AppSettings(strategy_aggregation_auto_run_enabled=True),
        kline_4h_job=collect_job,
        strategy_signal_after_collect_job=stage17_hook,
        strategy_aggregation_after_signal_job=stage18_hook,
    )

    records = runner.run_once(current_time_utc=utc_at(18, 8, 6))

    assert len(stage18_calls) == 1
    assert stage18_calls[0]["strategy_signal_scheduler_result"].run_id == "SSR-test"
    assert records[0].details["strategy_signal_scheduler"]["status"] == "success"
    assert records[0].details["strategy_aggregation"]["status"] == "success"
    assert records[0].details["strategy_aggregation"]["aggregation_run_id"] == "SAR-test"


def test_scheduler_auto_run_disabled_does_not_call_stage18_job() -> None:
    stage18_calls: list[Any] = []

    def collect_job() -> IncrementalKlineCollectResult:
        return success_collect_result()

    def stage17_hook(**_kwargs: Any) -> StrategySignalSchedulerResult:
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SUCCESS,
            event_id="SSS-test",
            run_id="SSR-test",
            snapshot_id="MCS-test",
            trace_id="trace-17",
            message="stage17 ok",
            target_base_open_time_ms=1,
        )

    def stage18_hook(**kwargs: Any) -> StrategyAggregationResult:
        stage18_calls.append(kwargs)
        raise AssertionError("stage18 must not run when auto-run is disabled")

    runner = SchedulerRunner(
        config=runtime_config(strategy_aggregation_auto_run_enabled=False),
        slot_store=FakeSlotStore(),
        settings=AppSettings(strategy_aggregation_auto_run_enabled=False),
        kline_4h_job=collect_job,
        strategy_signal_after_collect_job=stage17_hook,
        strategy_aggregation_after_signal_job=stage18_hook,
    )

    records = runner.run_once(current_time_utc=utc_at(18, 8, 6))

    assert stage18_calls == []
    assert records[0].details["strategy_aggregation"]["status"] == "disabled"


def test_scheduler_does_not_auto_run_stage18_after_stage17_blocked() -> None:
    stage18_calls: list[Any] = []

    for blocked_status in (StrategySignalSchedulerStatus.BLOCKED, StrategySignalSchedulerStatus.FAILED):

        def collect_job() -> IncrementalKlineCollectResult:
            return success_collect_result()

        def stage17_hook(**_kwargs: Any) -> StrategySignalSchedulerResult:
            return StrategySignalSchedulerResult(
                status=blocked_status,
                event_id="SSS-blocked",
                run_id=None,
                snapshot_id=None,
                trace_id="trace-17",
                message="blocked",
                target_base_open_time_ms=1,
            )

        def stage18_hook(**kwargs: Any) -> StrategyAggregationResult:
            stage18_calls.append(kwargs)
            raise AssertionError("stage18 must not run after stage17 blocked/failed")

        runner = SchedulerRunner(
            config=runtime_config(),
            slot_store=FakeSlotStore(),
            settings=AppSettings(strategy_aggregation_auto_run_enabled=True),
            kline_4h_job=collect_job,
            strategy_signal_after_collect_job=stage17_hook,
            strategy_aggregation_after_signal_job=stage18_hook,
        )

        records = runner.run_once(current_time_utc=utc_at(18, 8, 6))

        assert records[0].details["strategy_signal_scheduler"]["status"] == blocked_status.value
        assert "strategy_aggregation" not in records[0].details


def test_scheduler_skips_stage18_when_stage17_success_has_no_run_id() -> None:
    stage18_calls: list[Any] = []

    def collect_job() -> IncrementalKlineCollectResult:
        return success_collect_result()

    def stage17_hook(**_kwargs: Any) -> StrategySignalSchedulerResult:
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SUCCESS,
            event_id="SSS-missing-run",
            run_id=None,
            snapshot_id="MCS-test",
            trace_id="trace-17",
            message="stage17 success but missing run id",
            target_base_open_time_ms=1,
        )

    def stage18_hook(**kwargs: Any) -> StrategyAggregationResult:
        stage18_calls.append(kwargs)
        raise AssertionError("stage18 must not run without strategy_signal_run_id")

    runner = SchedulerRunner(
        config=runtime_config(),
        slot_store=FakeSlotStore(),
        settings=AppSettings(strategy_aggregation_auto_run_enabled=True),
        kline_4h_job=collect_job,
        strategy_signal_after_collect_job=stage17_hook,
        strategy_aggregation_after_signal_job=stage18_hook,
    )

    records = runner.run_once(current_time_utc=utc_at(18, 8, 6))

    assert stage18_calls == []
    assert records[0].details["strategy_signal_scheduler"]["status"] == "success"
    assert records[0].details["strategy_aggregation"]["status"] == "skipped"
    assert "strategy_signal_run_id missing" in records[0].details["strategy_aggregation"]["message"]


def test_stage18_scheduler_job_missing_run_id_does_not_open_db_session(monkeypatch: Any) -> None:
    from app.scheduler.jobs import strategy_aggregation_job as job_module

    def forbidden_session_scope(**_kwargs: Any) -> Any:
        raise AssertionError("missing strategy_signal_run_id must not open a database session")

    monkeypatch.setattr(job_module.mysql_session, "session_scope", forbidden_session_scope)

    result = job_module.run_strategy_aggregation_after_signal_job(
        strategy_signal_scheduler_result=StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SUCCESS,
            event_id="SSS-missing-run",
            run_id=None,
            snapshot_id="MCS-test",
            trace_id="trace-17",
            message="stage17 success but missing run id",
            target_base_open_time_ms=1,
        ),
        current_time_utc=utc_at(18, 8, 6),
        settings=AppSettings(strategy_aggregation_auto_run_enabled=True),
        config=runtime_config(strategy_aggregation_auto_run_enabled=True),
    )

    assert result.status == StrategyAggregationStatus.SKIPPED
    assert "strategy_signal_run_id missing" in result.message
