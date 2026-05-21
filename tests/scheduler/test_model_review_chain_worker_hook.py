from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.config import AppSettings
from app.market_data.collector.types import EXIT_SUCCESS, IncrementalKlineCollectResult, KlineCollectStatus
from app.model_review_chain.worker_schema import ModelReviewChainWorkerResult
from app.scheduler.config import SchedulerRuntimeConfig
from app.scheduler.runner import SchedulerRunner
from app.scheduler.slot_state import SchedulerSlotAction, SchedulerSlotDecision, SchedulerSlotStatus
from app.scheduler.strategy_signal_scheduler_types import StrategySignalSchedulerResult, StrategySignalSchedulerStatus
from app.strategy.aggregation.types import AnalysisHypothesisDirection, StrategyAggregationResult, StrategyAggregationStatus


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
        "kline_1d_incremental_collect_utc_time": utc_at(22, 0, 10).time(),
        "daily_kline_integrity_enabled": False,
        "daily_kline_integrity_symbol": "BTCUSDT",
        "daily_kline_integrity_interval": "4h",
        "daily_kline_integrity_limit": 100,
        "daily_kline_integrity_utc_time": utc_at(22, 0, 30).time(),
        "daily_kline_1d_integrity_enabled": False,
        "daily_kline_1d_integrity_symbol": "BTCUSDT",
        "daily_kline_1d_integrity_interval": "1d",
        "daily_kline_1d_integrity_limit": 500,
        "daily_kline_1d_integrity_notify_success": True,
        "daily_kline_1d_integrity_lock_ttl_seconds": 1800,
        "daily_kline_1d_integrity_utc_time": utc_at(22, 0, 20).time(),
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
        "model_review_auto_run_enabled": True,
        "model_review_scheduler_enabled": True,
        "model_review_max_runs_per_4h": 2,
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
        return None

    def release_running_lock(self, **_kwargs: Any) -> bool:
        return True


def test_scheduler_triggers_20c_worker_after_stage18_success_without_direct_stage19() -> None:
    worker_calls: list[Any] = []

    def collect_job() -> IncrementalKlineCollectResult:
        return IncrementalKlineCollectResult(
            status=KlineCollectStatus.SUCCESS,
            exit_code=EXIT_SUCCESS,
            trace_id="collect-trace",
            message="ok",
            event_log_id=501,
        )

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

    def stage18_hook(**_kwargs: Any) -> StrategyAggregationResult:
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

    def worker_hook(**kwargs: Any) -> ModelReviewChainWorkerResult:
        worker_calls.append(kwargs)
        return ModelReviewChainWorkerResult(
            status="skipped",
            exit_code=0,
            trace_id="trace-18",
            material_pack_id="AMP-test",
            model_review_invoked=False,
            model_review_skip_reason="本轮未调用大模型；test worker skipped.",
            summary_text="本轮未调用大模型；test worker skipped.",
        )

    runner = SchedulerRunner(
        config=runtime_config(),
        slot_store=FakeSlotStore(),
        settings=AppSettings(model_review_auto_run_enabled=True, model_review_scheduler_enabled=True),
        kline_4h_job=collect_job,
        strategy_signal_after_collect_job=stage17_hook,
        strategy_aggregation_after_signal_job=stage18_hook,
        model_review_chain_worker_after_aggregation_job=worker_hook,
    )

    records = runner.run_once(current_time_utc=utc_at(22, 8, 6))

    assert len(worker_calls) == 1
    assert worker_calls[0]["aggregation_result"].material_pack_id == "AMP-test"
    worker_details = records[0].details["strategy_aggregation"]["model_review_chain_worker"]
    assert worker_details["status"] == "skipped"
    assert worker_details["model_review_invoked"] is False
    assert worker_details["is_final_trading_advice"] is False
    assert worker_details["is_trading_signal"] is False
    assert worker_details["is_executable"] is False
    assert worker_details["auto_trading_allowed"] is False


def test_scheduler_does_not_trigger_20c_worker_when_config_disabled() -> None:
    worker_calls: list[Any] = []

    def worker_hook(**kwargs: Any) -> ModelReviewChainWorkerResult:
        worker_calls.append(kwargs)
        raise AssertionError("20C worker must not run when scheduler gate is disabled")

    runner = SchedulerRunner(
        config=runtime_config(model_review_scheduler_enabled=False),
        slot_store=FakeSlotStore(),
        settings=AppSettings(model_review_auto_run_enabled=True, model_review_scheduler_enabled=False),
        kline_4h_job=lambda: IncrementalKlineCollectResult(
            status=KlineCollectStatus.SUCCESS,
            exit_code=EXIT_SUCCESS,
            trace_id="collect-trace",
            message="ok",
            event_log_id=501,
        ),
        strategy_signal_after_collect_job=lambda **_kwargs: StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SUCCESS,
            event_id="SSS-test",
            run_id="SSR-test",
            snapshot_id="MCS-test",
            trace_id="trace-17",
            message="stage17 ok",
            target_base_open_time_ms=1,
        ),
        strategy_aggregation_after_signal_job=lambda **_kwargs: StrategyAggregationResult(
            status=StrategyAggregationStatus.SUCCESS,
            exit_code=0,
            aggregation_run_id="SAR-test",
            material_pack_id="AMP-test",
            strategy_signal_run_id="SSR-test",
            snapshot_id="MCS-test",
            trace_id="trace-18",
            analysis_hypothesis_direction=AnalysisHypothesisDirection.LONG,
            message="stage18 ok",
        ),
        model_review_chain_worker_after_aggregation_job=worker_hook,
    )

    records = runner.run_once(current_time_utc=utc_at(22, 8, 6))

    assert worker_calls == []
    worker_details = records[0].details["strategy_aggregation"]["model_review_chain_worker"]
    assert worker_details["status"] == "disabled"
    assert worker_details["reason"] == "MODEL_REVIEW_SCHEDULER_ENABLED=false"
