from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.config import AppSettings
from app.market_data.collector.types import EXIT_SUCCESS, IncrementalKlineCollectResult, KlineCollectStatus
from app.scheduler.config import SchedulerRuntimeConfig
from app.scheduler.jobs import strategy_pipeline_job
from app.scheduler.runner import SchedulerRunner
from app.scheduler.slot_state import SchedulerSlotAction, SchedulerSlotDecision, SchedulerSlotStatus
from app.strategy_pipeline.types import StrategyPipelineRequest, StrategyPipelineResult, StrategyPipelineStatus


def utc_at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 5, day, hour, minute, tzinfo=timezone.utc)


def expected_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


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
        "kline_1d_incremental_collect_utc_time": utc_at(30, 0, 10).time(),
        "daily_kline_integrity_enabled": False,
        "daily_kline_integrity_symbol": "BTCUSDT",
        "daily_kline_integrity_interval": "4h",
        "daily_kline_integrity_limit": 100,
        "daily_kline_integrity_utc_time": utc_at(30, 0, 30).time(),
        "daily_kline_1d_integrity_enabled": False,
        "daily_kline_1d_integrity_symbol": "BTCUSDT",
        "daily_kline_1d_integrity_interval": "1d",
        "daily_kline_1d_integrity_limit": 500,
        "daily_kline_1d_integrity_notify_success": True,
        "daily_kline_1d_integrity_lock_ttl_seconds": 1800,
        "daily_kline_1d_integrity_utc_time": utc_at(30, 0, 20).time(),
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
        "model_review_step_running_timeout_seconds": 300,
        "strategy_advice_scheduler_enabled": True,
        "strategy_advice_notification_send_enabled": False,
        "strategy_pipeline_scheduler_enabled": False,
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
            running_key=f"running:{kwargs['job']}:{kwargs['slot']}",
            completed_key=f"completed:{kwargs['job']}:{kwargs['slot']}",
            status_key=f"status:{kwargs['job']}:{kwargs['slot']}",
            owner=kwargs["owner"],
            reason="acquired",
            ttl_seconds=kwargs["running_ttl_seconds"],
            running_value="running-value",
        )

    def mark_slot_completed(self, **_kwargs: Any) -> None:
        return None

    def mark_slot_status(self, **_kwargs: Any) -> None:
        raise AssertionError("successful collector should not mark non-completed status")

    def release_running_lock(self, **_kwargs: Any) -> bool:
        return True


class FakeAlertSender:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, event: Any, **kwargs: Any) -> None:
        self.calls.append({"event": event, "kwargs": kwargs})


class FakePipelineService:
    def __init__(self) -> None:
        self.requests: list[StrategyPipelineRequest] = []

    def run_strategy_pipeline(self, _db_session: Any, *, request: StrategyPipelineRequest) -> StrategyPipelineResult:
        self.requests.append(request)
        return StrategyPipelineResult(
            status=StrategyPipelineStatus.SUCCESS,
            exit_code=0,
            pipeline_run_id="SP-job-test",
            trace_id=request.trace_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=request.kline_slot_utc,
            kline_slot_source="scheduler_upstream_collect",
            strategy_signal_run_id="SSR-job-test",
            strategy_evidence_aggregation_id="SEA-job-test",
            current_step="21a_21b_advice_notification",
            message="ok",
        )


class FakeSession:
    def close(self) -> None:
        return None

    def rollback(self) -> None:
        return None


@contextmanager
def fake_session_scope(**_kwargs: Any) -> Any:
    yield FakeSession()


def collect_result(*, slot: datetime | None = utc_at(30, 4, 0)) -> IncrementalKlineCollectResult:
    details: dict[str, Any] = {}
    if slot is not None:
        details["actual_end_open_time_ms"] = expected_ms(slot)
    return IncrementalKlineCollectResult(
        status=KlineCollectStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        trace_id="collect-trace",
        message="ok",
        event_log_id=701,
        details=details,
    )


def pipeline_result(
    *,
    status: StrategyPipelineStatus = StrategyPipelineStatus.SUCCESS,
    slot: datetime = utc_at(30, 4, 0),
) -> StrategyPipelineResult:
    return StrategyPipelineResult(
        status=status,
        exit_code=0 if status == StrategyPipelineStatus.SUCCESS else 2,
        pipeline_run_id="SP-runner-test",
        trace_id="pipeline-trace",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        kline_slot_utc=slot,
        kline_slot_source="scheduler_upstream_collect",
        strategy_signal_run_id="SSR-runner-test",
        strategy_evidence_aggregation_id="SEA-runner-test" if status == StrategyPipelineStatus.SUCCESS else None,
        material_pack_id="AMP-runner-test",
        review_aggregation_run_id="MRAG-runner-test",
        advice_id="ADV-runner-test",
        review_id="ADVR-runner-test",
        notification_status="recorded",
        model_review_invoked=True,
        model_review_reused=True,
        real_model_called=False,
        hermes_real_sent=False,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        current_step="24a_23f_evidence_aggregation" if status != StrategyPipelineStatus.SUCCESS else "21a_21b_advice_notification",
        message="pipeline result",
        error_code="stage23f_failed" if status != StrategyPipelineStatus.SUCCESS else None,
        error_message="23F failed" if status != StrategyPipelineStatus.SUCCESS else None,
    )


def test_scheduler_switch_false_does_not_trigger_stage25_pipeline() -> None:
    pipeline_calls: list[Any] = []

    runner = SchedulerRunner(
        config=runtime_config(strategy_pipeline_scheduler_enabled=False, strategy_signal_scheduler_enabled=False),
        slot_store=FakeSlotStore(),
        settings=AppSettings(strategy_pipeline_scheduler_enabled=False),
        kline_4h_job=lambda: collect_result(),
        strategy_pipeline_after_collect_job=lambda **kwargs: pipeline_calls.append(kwargs),
    )

    records = runner.run_once(current_time_utc=utc_at(30, 8, 6))

    assert pipeline_calls == []
    assert records[0].details["strategy_signal_scheduler"]["status"] == "disabled"


def test_scheduler_enabled_triggers_stage25_and_bypasses_legacy_stage17() -> None:
    pipeline_calls: list[dict[str, Any]] = []

    def pipeline_hook(**kwargs: Any) -> StrategyPipelineResult:
        pipeline_calls.append(kwargs)
        return pipeline_result(slot=kwargs["kline_slot_utc"])

    def legacy_stage17_hook(**_kwargs: Any) -> None:
        raise AssertionError("legacy stage 17 must be skipped when stage 25 scheduler is enabled")

    runner = SchedulerRunner(
        config=runtime_config(strategy_pipeline_scheduler_enabled=True, strategy_signal_scheduler_enabled=True),
        slot_store=FakeSlotStore(),
        settings=AppSettings(strategy_pipeline_scheduler_enabled=True),
        kline_4h_job=lambda: collect_result(slot=utc_at(30, 4, 0)),
        strategy_pipeline_after_collect_job=pipeline_hook,
        strategy_signal_after_collect_job=legacy_stage17_hook,
    )

    records = runner.run_once(current_time_utc=utc_at(30, 8, 6))

    assert len(pipeline_calls) == 1
    assert pipeline_calls[0]["kline_slot_utc"] == utc_at(30, 4, 0)
    details = records[0].details["strategy_pipeline"]
    assert details["status"] == "success"
    assert details["strategy_evidence_aggregation_id"] == "SEA-runner-test"
    assert details["old_stage17_auto_trigger_skipped_due_to_pipeline_enabled"] is True
    assert "strategy_signal_scheduler" not in records[0].details
    assert "strategy_aggregation" not in records[0].details
    assert details["real_model_called"] is False
    assert details["hermes_real_sent"] is False
    assert details["is_final_trading_advice"] is False
    assert details["is_trading_signal"] is False
    assert details["is_executable"] is False
    assert details["auto_trading_allowed"] is False


def test_scheduler_blocks_stage25_when_collector_slot_is_missing() -> None:
    alerts = FakeAlertSender()

    runner = SchedulerRunner(
        config=runtime_config(strategy_pipeline_scheduler_enabled=True, strategy_signal_scheduler_enabled=True),
        slot_store=FakeSlotStore(),
        settings=AppSettings(strategy_pipeline_scheduler_enabled=True),
        kline_4h_job=lambda: collect_result(slot=None),
        strategy_pipeline_after_collect_job=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("pipeline must not run without an explicit 09 slot")
        ),
        alert_sender=alerts,
    )

    records = runner.run_once(current_time_utc=utc_at(30, 8, 6))

    details = records[0].details["strategy_pipeline"]
    assert details["status"] == "blocked"
    assert details["error_code"] == "pipeline_kline_slot_missing"
    assert details["old_stage17_auto_trigger_skipped_due_to_pipeline_enabled"] is True
    assert len(alerts.calls) == 1
    alert_details = alerts.calls[0]["event"].details
    assert alert_details["upstream_09_success"] is True
    assert alert_details["not_trading_advice"] is True
    assert alert_details["auto_trading_allowed"] is False


def test_scheduler_records_and_alerts_stage25_failure() -> None:
    alerts = FakeAlertSender()

    runner = SchedulerRunner(
        config=runtime_config(strategy_pipeline_scheduler_enabled=True),
        slot_store=FakeSlotStore(),
        settings=AppSettings(strategy_pipeline_scheduler_enabled=True),
        kline_4h_job=lambda: collect_result(slot=utc_at(30, 4, 0)),
        strategy_pipeline_after_collect_job=lambda **kwargs: pipeline_result(
            status=StrategyPipelineStatus.BLOCKED,
            slot=kwargs["kline_slot_utc"],
        ),
        alert_sender=alerts,
    )

    records = runner.run_once(current_time_utc=utc_at(30, 8, 6))

    details = records[0].details["strategy_pipeline"]
    assert details["status"] == "blocked"
    assert details["pipeline_run_id"] == "SP-runner-test"
    assert details["current_step"] == "24a_23f_evidence_aggregation"
    assert details["error_code"] == "stage23f_failed"
    assert details["trace_id"] == "pipeline-trace"
    assert len(alerts.calls) == 1
    alert_details = alerts.calls[0]["event"].details
    assert alert_details["pipeline_run_id"] == "SP-runner-test"
    assert alert_details["current_step"] == "24a_23f_evidence_aggregation"
    assert alert_details["error_code"] == "stage23f_failed"
    assert alert_details["pipeline_trace_id"] == "pipeline-trace"
    assert alert_details["is_final_trading_advice"] is False


def test_scheduler_preserves_stage25_lock_conflict_without_falling_back_to_legacy_stage17() -> None:
    alerts = FakeAlertSender()

    def legacy_stage17_hook(**_kwargs: Any) -> None:
        raise AssertionError("legacy stage 17 must not run after a pipeline lock conflict")

    runner = SchedulerRunner(
        config=runtime_config(strategy_pipeline_scheduler_enabled=True, strategy_signal_scheduler_enabled=True),
        slot_store=FakeSlotStore(),
        settings=AppSettings(strategy_pipeline_scheduler_enabled=True),
        kline_4h_job=lambda: collect_result(slot=utc_at(30, 4, 0)),
        strategy_pipeline_after_collect_job=lambda **kwargs: StrategyPipelineResult(
            status=StrategyPipelineStatus.SKIPPED,
            exit_code=2,
            pipeline_run_id="SP-locked",
            trace_id="pipeline-trace",
            symbol="BTCUSDT",
            base_interval="4h",
            higher_interval="1d",
            kline_slot_utc=kwargs["kline_slot_utc"],
            kline_slot_source="scheduler_upstream_collect",
            current_step="preflight",
            error_code="pipeline_lock_already_held",
            message="locked",
            is_final_trading_advice=False,
            is_trading_signal=False,
            is_executable=False,
            auto_trading_allowed=False,
        ),
        strategy_signal_after_collect_job=legacy_stage17_hook,
        alert_sender=alerts,
    )

    records = runner.run_once(current_time_utc=utc_at(30, 8, 6))

    details = records[0].details["strategy_pipeline"]
    assert details["status"] == "skipped"
    assert details["error_code"] == "pipeline_lock_already_held"
    assert details["old_stage17_auto_trigger_skipped_due_to_pipeline_enabled"] is True
    assert alerts.calls == []


def run_scheduler_pipeline_job_for_request(
    monkeypatch: Any,
    *,
    settings: AppSettings,
) -> StrategyPipelineRequest:
    service = FakePipelineService()
    monkeypatch.setattr(strategy_pipeline_job.mysql_session, "session_scope", fake_session_scope)

    result = strategy_pipeline_job.run_strategy_pipeline_after_collect_job(
        upstream_job_name="kline_4h_incremental_collect",
        upstream_result=collect_result(slot=utc_at(30, 4, 0)),
        upstream_slot_time_utc=utc_at(30, 8, 5),
        kline_slot_utc=utc_at(30, 4, 0),
        current_time_utc=utc_at(30, 8, 6),
        settings=settings,
        config=runtime_config(strategy_pipeline_scheduler_enabled=True),
        service=service,
    )

    assert result.status == StrategyPipelineStatus.SUCCESS
    assert len(service.requests) == 1
    return service.requests[0]


def test_strategy_pipeline_scheduler_job_default_request_disables_real_model_and_hermes(
    monkeypatch: Any,
) -> None:
    request = run_scheduler_pipeline_job_for_request(
        monkeypatch,
        settings=AppSettings(strategy_pipeline_enabled=True, strategy_pipeline_scheduler_enabled=True),
    )

    assert request.trigger_source == "scheduler"
    assert request.dry_run is False
    assert request.confirm_write is True
    assert request.kline_slot_utc == utc_at(30, 4, 0)
    assert request.use_real_model is False
    assert request.confirm_real_model_cost is False
    assert request.send_real_hermes is False
    assert request.retry_failed_stage17 is False


def test_strategy_pipeline_scheduler_job_enables_real_model_only_when_all_model_switches_true(
    monkeypatch: Any,
) -> None:
    request = run_scheduler_pipeline_job_for_request(
        monkeypatch,
        settings=AppSettings(
            strategy_pipeline_enabled=True,
            strategy_pipeline_scheduler_enabled=True,
            strategy_pipeline_real_model_enabled=True,
            model_review_real_model_enabled=True,
            strategy_pipeline_confirm_real_model_cost=True,
        ),
    )

    assert request.use_real_model is True
    assert request.confirm_real_model_cost is True


@pytest.mark.parametrize(
    "settings",
    [
        AppSettings(
            strategy_pipeline_enabled=True,
            strategy_pipeline_scheduler_enabled=True,
            strategy_pipeline_real_model_enabled=False,
            model_review_real_model_enabled=True,
            strategy_pipeline_confirm_real_model_cost=True,
        ),
        AppSettings(
            strategy_pipeline_enabled=True,
            strategy_pipeline_scheduler_enabled=True,
            strategy_pipeline_real_model_enabled=True,
            model_review_real_model_enabled=False,
            strategy_pipeline_confirm_real_model_cost=True,
        ),
        AppSettings(
            strategy_pipeline_enabled=True,
            strategy_pipeline_scheduler_enabled=True,
            strategy_pipeline_real_model_enabled=True,
            model_review_real_model_enabled=True,
            strategy_pipeline_confirm_real_model_cost=False,
        ),
    ],
)
def test_strategy_pipeline_scheduler_job_disables_real_model_when_any_model_switch_false(
    monkeypatch: Any,
    settings: AppSettings,
) -> None:
    request = run_scheduler_pipeline_job_for_request(monkeypatch, settings=settings)

    assert request.use_real_model is False
    assert request.confirm_real_model_cost is False


def test_strategy_pipeline_scheduler_job_enables_real_hermes_only_when_both_notification_switches_true(
    monkeypatch: Any,
) -> None:
    request = run_scheduler_pipeline_job_for_request(
        monkeypatch,
        settings=AppSettings(
            strategy_pipeline_enabled=True,
            strategy_pipeline_scheduler_enabled=True,
            strategy_pipeline_notification_send_enabled=True,
            strategy_advice_notification_send_enabled=True,
        ),
    )

    assert request.send_real_hermes is True


@pytest.mark.parametrize(
    "settings",
    [
        AppSettings(
            strategy_pipeline_enabled=True,
            strategy_pipeline_scheduler_enabled=True,
            strategy_pipeline_notification_send_enabled=False,
            strategy_advice_notification_send_enabled=True,
        ),
        AppSettings(
            strategy_pipeline_enabled=True,
            strategy_pipeline_scheduler_enabled=True,
            strategy_pipeline_notification_send_enabled=True,
            strategy_advice_notification_send_enabled=False,
        ),
    ],
)
def test_strategy_pipeline_scheduler_job_disables_real_hermes_when_any_notification_switch_false(
    monkeypatch: Any,
    settings: AppSettings,
) -> None:
    request = run_scheduler_pipeline_job_for_request(monkeypatch, settings=settings)

    assert request.send_real_hermes is False
