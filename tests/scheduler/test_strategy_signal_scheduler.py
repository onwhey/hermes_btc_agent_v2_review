from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.config import AppSettings
from app.scheduler.config import SchedulerRuntimeConfig
from app.scheduler.runner import SchedulerRunner
from app.scheduler.slot_state import (
    KLINE_1D_INCREMENTAL_JOB_NAME,
    KLINE_4H_INCREMENTAL_JOB_NAME,
    SchedulerSlotAction,
    SchedulerSlotDecision,
    SchedulerSlotStatus,
)
from app.scheduler.strategy_signal_scheduler_service import StrategySignalSchedulerService
from app.scheduler.strategy_signal_scheduler_types import (
    StrategySignalSchedulerHermesStatus,
    StrategySignalSchedulerRequest,
    StrategySignalSchedulerResult,
    StrategySignalSchedulerStatus,
)
from app.strategy.types import (
    DirectionBias,
    RiskLevel,
    StrategyRunStatus,
    StrategySignal,
    StrategySignalRunRequest,
    StrategySignalRunResult,
    StrategySignalStatus,
)
from app.market_data.collector.types import EXIT_SUCCESS, IncrementalKlineCollectResult, KlineCollectStatus


def utc_at(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 5, day, hour, minute, second, tzinfo=timezone.utc)


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
        "kline_1d_incremental_collect_utc_time": utc_at(16, 0, 10).time(),
        "daily_kline_integrity_enabled": False,
        "daily_kline_integrity_symbol": "BTCUSDT",
        "daily_kline_integrity_interval": "4h",
        "daily_kline_integrity_limit": 100,
        "daily_kline_integrity_utc_time": utc_at(16, 0, 30).time(),
        "daily_kline_1d_integrity_enabled": False,
        "daily_kline_1d_integrity_symbol": "BTCUSDT",
        "daily_kline_1d_integrity_interval": "1d",
        "daily_kline_1d_integrity_limit": 500,
        "daily_kline_1d_integrity_notify_success": True,
        "daily_kline_1d_integrity_lock_ttl_seconds": 1800,
        "daily_kline_1d_integrity_utc_time": utc_at(16, 0, 20).time(),
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
    }
    data.update(overrides)
    return SchedulerRuntimeConfig(**data)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeSchedulerEventRepository:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    def get_event_by_target(self, _db_session: Any, **kwargs: Any) -> Any | None:
        for row in self.rows:
            if (
                row.symbol == kwargs["symbol"]
                and row.base_interval == kwargs["base_interval"]
                and row.higher_interval == kwargs["higher_interval"]
                and row.target_base_open_time_ms == kwargs["target_base_open_time_ms"]
            ):
                return row
        return None

    def create_scheduler_event(self, _db_session: Any, *, payload: Any) -> Any:
        row = SimpleNamespace(**payload.__dict__)
        row.id = len(self.rows) + 1
        row.run_id = None
        row.snapshot_id = None
        row.strategy_count = 0
        row.success_count = 0
        row.failed_count = 0
        row.invalid_count = 0
        row.not_implemented_count = 0
        row.skip_count = 0
        row.last_skipped_at_utc = None
        row.last_skip_reason = None
        row.hermes_message = None
        row.hermes_error = None
        row.hermes_sent_at_utc = None
        self.rows.append(row)
        return row

    def mark_event_running(self, _db_session: Any, event: Any, **kwargs: Any) -> Any:
        event.status = StrategySignalSchedulerStatus.RUNNING.value
        event.message = kwargs["message"]
        event.upstream_1d_collector_event_id = kwargs.get("upstream_1d_collector_event_id")
        return event

    def mark_event_completed_from_strategy_result(self, _db_session: Any, event: Any, **kwargs: Any) -> Any:
        for key, value in kwargs.items():
            setattr(event, key, value)
        return event

    def mark_duplicate_skipped(self, _db_session: Any, event: Any, *, reason: str) -> Any:
        event.skip_count += 1
        event.last_skip_reason = reason
        return event

    def record_hermes_result(self, _db_session: Any, event: Any, **kwargs: Any) -> Any:
        for key, value in kwargs.items():
            setattr(event, key, value)
        return event


class FakeStrategySignalService:
    def __init__(self, result: StrategySignalRunResult | None = None, fail_message: str | None = None) -> None:
        self.result = result or strategy_result(status=StrategyRunStatus.SUCCESS)
        self.fail_message = fail_message
        self.calls: list[StrategySignalRunRequest] = []

    def run_strategy_signals(self, _db_session: Any, *, request: StrategySignalRunRequest) -> StrategySignalRunResult:
        self.calls.append(request)
        if self.fail_message:
            raise RuntimeError(self.fail_message)
        return self.result


class FakeAlertSender:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[Any] = []

    def __call__(self, event: Any, **_kwargs: Any) -> AlertSendResult:
        self.calls.append(event)
        if self.fail:
            raise RuntimeError("Hermes failed")
        return AlertSendResult(
            status=AlertSendStatus.SUBMITTED_TO_HERMES,
            message="submitted",
            submitted_at_utc=utc_at(16, 8, 7),
            attempted_real_send=True,
        )


class FakeSlotStore:
    def __init__(self) -> None:
        self.completed_calls: list[dict[str, Any]] = []
        self.release_calls = 0

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

    def mark_slot_completed(self, **kwargs: Any) -> None:
        self.completed_calls.append(kwargs)

    def mark_slot_status(self, **_kwargs: Any) -> None:
        raise AssertionError("successful collector should not mark non-completed status")

    def release_running_lock(self, **_kwargs: Any) -> bool:
        self.release_calls += 1
        return True


def success_collect_result() -> IncrementalKlineCollectResult:
    return IncrementalKlineCollectResult(
        status=KlineCollectStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        trace_id="collect-trace",
        message="ok",
        event_log_id=501,
    )


def strategy_signal(name: str = "trend_structure") -> StrategySignal:
    return StrategySignal(
        strategy_name=name,
        strategy_version="v1",
        strategy_status=StrategySignalStatus.SUCCESS,
        direction_bias=DirectionBias.NEUTRAL,
        risk_level=RiskLevel.MEDIUM,
        signal_strength=0.5,
        reason_codes=("stable",),
        reason_text="独立策略信号摘要，不包含交易操作指令。",
        metrics={"sample": "1"},
        debug_info={"scope": "test"},
        trace_id="strategy-trace",
    )


def strategy_result(
    *,
    status: StrategyRunStatus,
    run_id: str = "SSR-test",
    snapshot_id: str | None = "MCS-test",
    error_message: str | None = None,
    blocked_reason: str | None = None,
) -> StrategySignalRunResult:
    signals = (strategy_signal(),) if status in {StrategyRunStatus.SUCCESS, StrategyRunStatus.PARTIAL_SUCCESS} else ()
    return StrategySignalRunResult(
        status=status,
        exit_code=0,
        run_id=run_id,
        trace_id="strategy-trace",
        snapshot_id=snapshot_id,
        message="strategy service done",
        blocked_reason=blocked_reason,
        error_message=error_message,
        strategy_count=len(signals),
        success_count=len(signals),
        failed_count=0,
        invalid_count=0,
        not_implemented_count=1 if status == StrategyRunStatus.PARTIAL_SUCCESS else 0,
        signals=signals,
    )


def service_with_fakes(
    *,
    config: SchedulerRuntimeConfig | None = None,
    strategy_service: FakeStrategySignalService | None = None,
    repository: FakeSchedulerEventRepository | None = None,
    alert_sender: FakeAlertSender | None = None,
) -> tuple[StrategySignalSchedulerService, FakeSchedulerEventRepository, FakeStrategySignalService, FakeAlertSender]:
    repo = repository or FakeSchedulerEventRepository()
    strategy = strategy_service or FakeStrategySignalService()
    alert = alert_sender or FakeAlertSender()
    service = StrategySignalSchedulerService(
        config=config or runtime_config(),
        settings=AppSettings(),
        event_repository=repo,
        strategy_signal_service=strategy,
        alert_sender=alert,
    )
    return service, repo, strategy, alert


def test_4h_collect_success_triggers_strategy_signal_service_with_scheduler_request() -> None:
    service, repo, strategy, _alert = service_with_fakes()
    session = FakeSession()

    result = service.run_after_collector_success(
        session,
        request=StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
            current_time_utc=utc_at(16, 8, 6),
            upstream_trace_id="collect-trace",
            upstream_collector_event_id=501,
            trace_id="trace-17",
        ),
    )

    assert result.status == StrategySignalSchedulerStatus.SUCCESS
    assert len(repo.rows) == 1
    assert repo.rows[0].status == StrategySignalSchedulerStatus.SUCCESS.value
    assert repo.rows[0].target_base_open_time_ms == expected_ms(utc_at(16, 4, 0))
    assert strategy.calls[0].trigger_source == "scheduler"
    assert strategy.calls[0].ensure_latest_snapshot is True
    assert strategy.calls[0].dry_run is False
    assert strategy.calls[0].confirm_write is True
    assert strategy.calls[0].created_by == "strategy_signal_scheduler"
    assert repo.rows[0].hermes_status == StrategySignalSchedulerHermesStatus.DISABLED.value


def test_utc_midnight_4h_waits_for_1d_then_runs_once() -> None:
    repo = FakeSchedulerEventRepository()
    strategy = FakeStrategySignalService()
    service, _repo, _strategy, _alert = service_with_fakes(repository=repo, strategy_service=strategy)
    session = FakeSession()

    first = service.run_after_collector_success(
        session,
        request=StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
            current_time_utc=utc_at(16, 0, 6),
            upstream_collector_event_id=601,
            trace_id="trace-midnight",
        ),
    )
    second = service.run_after_collector_success(
        session,
        request=StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_1D_INCREMENTAL_JOB_NAME,
            current_time_utc=utc_at(16, 0, 12),
            upstream_collector_event_id=701,
            trace_id="trace-midnight",
        ),
    )

    assert first.status == StrategySignalSchedulerStatus.WAITING_UPSTREAM
    assert second.status == StrategySignalSchedulerStatus.SUCCESS
    assert len(repo.rows) == 1
    assert repo.rows[0].upstream_4h_collector_event_id == 601
    assert repo.rows[0].upstream_1d_collector_event_id == 701
    assert repo.rows[0].target_base_open_time_ms == expected_ms(utc_at(15, 20, 0))
    assert len(strategy.calls) == 1


def test_same_target_does_not_create_duplicate_scheduler_event() -> None:
    service, repo, strategy, _alert = service_with_fakes()
    session = FakeSession()
    request = StrategySignalSchedulerRequest(
        upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
        current_time_utc=utc_at(16, 8, 6),
        trace_id="trace-dup",
    )

    first = service.run_after_collector_success(session, request=request)
    second = service.run_after_collector_success(session, request=request)

    assert first.status == StrategySignalSchedulerStatus.SUCCESS
    assert second.status == StrategySignalSchedulerStatus.SKIPPED
    assert len(repo.rows) == 1
    assert repo.rows[0].skip_count == 1
    assert len(strategy.calls) == 1


def test_blocked_failed_partial_and_skipped_statuses_are_recorded() -> None:
    blocked_service, blocked_repo, _blocked_strategy, _alert = service_with_fakes(
        strategy_service=FakeStrategySignalService(
            strategy_result(
                status=StrategyRunStatus.BLOCKED,
                snapshot_id=None,
                blocked_reason="snapshot_not_ready",
            )
        )
    )
    blocked = blocked_service.run_after_collector_success(
        FakeSession(),
        request=StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
            current_time_utc=utc_at(16, 8, 6),
            trace_id="trace-blocked",
        ),
    )

    failed_service, failed_repo, _failed_strategy, _alert2 = service_with_fakes(
        strategy_service=FakeStrategySignalService(fail_message="boom")
    )
    failed = failed_service.run_after_collector_success(
        FakeSession(),
        request=StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
            current_time_utc=utc_at(16, 12, 6),
            trace_id="trace-failed",
        ),
    )

    partial_service, partial_repo, _partial_strategy, _alert3 = service_with_fakes(
        strategy_service=FakeStrategySignalService(strategy_result(status=StrategyRunStatus.PARTIAL_SUCCESS))
    )
    partial = partial_service.run_after_collector_success(
        FakeSession(),
        request=StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
            current_time_utc=utc_at(16, 16, 6),
            trace_id="trace-partial",
        ),
    )

    skipped_service, skipped_repo, _skipped_strategy, _alert4 = service_with_fakes(
        config=runtime_config(strategy_signal_scheduler_enabled=False)
    )
    skipped = skipped_service.run_after_collector_success(
        FakeSession(),
        request=StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
            current_time_utc=utc_at(16, 20, 6),
            trace_id="trace-skipped",
        ),
    )

    assert blocked.status == StrategySignalSchedulerStatus.BLOCKED
    assert blocked_repo.rows[0].status == StrategySignalSchedulerStatus.BLOCKED.value
    assert failed.status == StrategySignalSchedulerStatus.FAILED
    assert failed_repo.rows[0].status == StrategySignalSchedulerStatus.FAILED.value
    assert partial.status == StrategySignalSchedulerStatus.PARTIAL_SUCCESS
    assert partial_repo.rows[0].status == StrategySignalSchedulerStatus.PARTIAL_SUCCESS.value
    assert skipped.status == StrategySignalSchedulerStatus.SKIPPED
    assert skipped_repo.rows[0].status == StrategySignalSchedulerStatus.SKIPPED.value


def test_hermes_config_off_does_not_send_and_on_records_sent_message() -> None:
    off_service, off_repo, _strategy, off_alert = service_with_fakes(
        config=runtime_config(strategy_signal_hermes_enabled=False)
    )
    off_service.run_after_collector_success(
        FakeSession(),
        request=StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
            current_time_utc=utc_at(16, 8, 6),
            trace_id="trace-hermes-off",
        ),
    )

    on_alert = FakeAlertSender()
    on_service, on_repo, _strategy2, _alert2 = service_with_fakes(
        config=runtime_config(strategy_signal_hermes_enabled=True),
        strategy_service=FakeStrategySignalService(strategy_result(status=StrategyRunStatus.PARTIAL_SUCCESS)),
        alert_sender=on_alert,
    )
    on_service.run_after_collector_success(
        FakeSession(),
        request=StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
            current_time_utc=utc_at(16, 12, 6),
            trace_id="trace-hermes-on",
        ),
    )

    assert off_alert.calls == []
    assert off_repo.rows[0].hermes_status == StrategySignalSchedulerHermesStatus.DISABLED.value
    assert len(on_alert.calls) == 1
    assert on_repo.rows[0].hermes_status == StrategySignalSchedulerHermesStatus.SENT.value
    body = on_repo.rows[0].hermes_message
    assert "独立策略信号" in body
    assert "不是最终交易建议" in body
    assert "未调用大模型" in body
    assert "未自动交易" in body
    assert "未实现：1" in body


def test_skipped_default_does_not_send_hermes_when_hermes_enabled() -> None:
    alert = FakeAlertSender()
    service, repo, _strategy, _alert = service_with_fakes(
        config=runtime_config(
            strategy_signal_scheduler_enabled=False,
            strategy_signal_hermes_enabled=True,
            strategy_signal_hermes_notify_skipped=False,
        ),
        alert_sender=alert,
    )

    service.run_after_collector_success(
        FakeSession(),
        request=StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
            current_time_utc=utc_at(16, 8, 6),
            trace_id="trace-skip-hermes",
        ),
    )

    assert alert.calls == []
    assert repo.rows[0].hermes_status == StrategySignalSchedulerHermesStatus.NOT_REQUIRED.value


def test_runner_calls_stage17_hook_after_4h_collector_success() -> None:
    calls: list[dict[str, Any]] = []

    def collect_job() -> IncrementalKlineCollectResult:
        return success_collect_result()

    def post_hook(**kwargs: Any) -> StrategySignalSchedulerResult:
        calls.append(kwargs)
        return StrategySignalSchedulerResult(
            status=StrategySignalSchedulerStatus.SUCCESS,
            event_id="SSS-test",
            trace_id="trace-17",
            message="ok",
            target_base_open_time_ms=expected_ms(utc_at(16, 4, 0)),
        )

    runner = SchedulerRunner(
        config=runtime_config(daily_kline_integrity_enabled=False),
        slot_store=FakeSlotStore(),
        settings=AppSettings(),
        kline_4h_job=collect_job,
        strategy_signal_after_collect_job=post_hook,
    )

    records = runner.run_once(current_time_utc=utc_at(16, 8, 6))

    assert records[0].status == SchedulerSlotStatus.COMPLETED.value
    assert len(calls) == 1
    assert calls[0]["upstream_job_name"] == KLINE_4H_INCREMENTAL_JOB_NAME
    assert calls[0]["upstream_result"].trace_id == "collect-trace"
    assert records[0].details["strategy_signal_scheduler"]["status"] == "success"


def test_scheduler_source_does_not_call_stage15_or_strategy_cli() -> None:
    import app.scheduler.runner as runner_module
    import app.scheduler.strategy_signal_scheduler_service as service_module
    import app.scheduler.jobs.strategy_signal_scheduler_job as job_module

    source = "\n".join(inspect.getsource(module) for module in (runner_module, service_module, job_module))

    assert "scripts.run_strategy_signals" not in source
    assert "run_strategy_signals.py" not in source
    assert "build_market_context_snapshot" not in source
    assert "MarketContextSnapshotService" not in source


def test_event_log_model_and_migration_have_unique_target_constraint() -> None:
    from app.storage.mysql.models.strategy_signal_scheduler_event import StrategySignalSchedulerEventLog

    table = StrategySignalSchedulerEventLog.__table__
    unique_names = {constraint.name for constraint in table.constraints}
    migration_text = Path(
        "migrations/versions/20260518_17_create_strategy_signal_scheduler_event_log.py"
    ).read_text(encoding="utf-8")

    assert "uk_strategy_signal_scheduler_target" in unique_names
    assert "strategy_signal_scheduler_event_log" in migration_text
    assert "uk_strategy_signal_scheduler_target" in migration_text
    assert "target_base_open_time_ms" in migration_text
