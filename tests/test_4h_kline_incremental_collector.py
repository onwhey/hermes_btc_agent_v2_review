from __future__ import annotations

import inspect
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.exceptions import RedisError
from app.market_data.collector.kline_4h_collector_service import run_incremental_4h_collection
from app.market_data.collector.types import (
    EXIT_ALERT_FAILED,
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_BLOCKED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    KlineCollectStatus,
    IncrementalKlineCollectRequest,
)
from app.market_data.kline_constants import KLINE_4H_INTERVAL_VALUE, TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER
from app.market_data.kline_quality.types import KlineQualityIssueType
from scripts import collect_4h_klines as collect_script
from tests.test_4h_kline_manual_backfill import (
    FakeAlertSender,
    FakeCollectorEventRepository,
    FakeKlineRepository,
    FakeQualityRepository,
    FakeSession,
    FakeTaskLock,
    build_dto,
    build_raw,
    model_from_dto,
)


class FakeIncrementalBinanceClient:
    def __init__(self, raw_klines: list[list[Any]], *, server_time_ms: int) -> None:
        self.raw_klines = raw_klines
        self.server_time_ms = server_time_ms
        self.get_klines_calls: list[dict[str, Any]] = []
        self.get_server_time_calls = 0

    def get_server_time(self) -> dict[str, int]:
        self.get_server_time_calls += 1
        return {"serverTime": self.server_time_ms}

    def get_klines(self, **kwargs: Any) -> list[list[Any]]:
        self.get_klines_calls.append(kwargs)
        return list(self.raw_klines)


def collect_request(
    *,
    limit: int = 4,
    notify_success: bool = False,
    trigger_source: str = TRIGGER_SOURCE_CLI,
) -> IncrementalKlineCollectRequest:
    return IncrementalKlineCollectRequest(
        symbol="BTCUSDT",
        interval_value=KLINE_4H_INTERVAL_VALUE,
        trigger_source=trigger_source,
        limit=limit,
        confirm_write=True,
        notify_success=notify_success,
    )


def run_collect_with_fakes(
    request: IncrementalKlineCollectRequest,
    raw_klines: list[list[Any]],
    *,
    existing: Iterable[Any] = (),
    server_time_ms: int | None = None,
    repository: FakeKlineRepository | None = None,
    task_lock: FakeTaskLock | None = None,
    alert_sender: FakeAlertSender | None = None,
    collector_repository: FakeCollectorEventRepository | None = None,
) -> tuple[Any, FakeKlineRepository, FakeTaskLock, FakeAlertSender, FakeQualityRepository, FakeSession, FakeIncrementalBinanceClient]:
    dto = build_dto(0)
    fake_client = FakeIncrementalBinanceClient(
        raw_klines,
        server_time_ms=server_time_ms if server_time_ms is not None else dto.close_time_ms + 100 * 14_400_000,
    )
    fake_repository = repository or FakeKlineRepository(existing)
    fake_task_lock = task_lock or FakeTaskLock()
    fake_alert_sender = alert_sender or FakeAlertSender()
    fake_quality_repository = FakeQualityRepository()
    fake_session = FakeSession()

    result = run_incremental_4h_collection(
        request,
        db_session=fake_session,
        binance_client=fake_client,
        task_lock=fake_task_lock,
        kline_repository=fake_repository,
        data_quality_repository=fake_quality_repository,
        collector_event_repository=collector_repository or FakeCollectorEventRepository(),
        alert_sender=fake_alert_sender,
        alert_repository=object(),
    )
    return (
        result,
        fake_repository,
        fake_task_lock,
        fake_alert_sender,
        fake_quality_repository,
        fake_session,
        fake_client,
    )


def test_incremental_writes_missing_12_and_16_skips_existing_04_and_08() -> None:
    existing = [model_from_dto(build_dto(0)), model_from_dto(build_dto(1))]

    result, repository, _lock, alert_sender, _quality_repo, _session, client = run_collect_with_fakes(
        collect_request(limit=4),
        [build_raw(0), build_raw(1), build_raw(2), build_raw(3)],
        existing=existing,
    )

    assert result.status == KlineCollectStatus.SUCCESS
    assert result.exit_code == EXIT_SUCCESS
    assert result.inserted_count == 2
    assert result.skipped_existing_count == 2
    assert build_dto(2).open_time_ms in repository.rows
    assert build_dto(3).open_time_ms in repository.rows
    assert client.get_klines_calls[0]["limit"] == 5
    assert alert_sender.calls == []


def test_incremental_latest_only_missing_12_blocks_and_alerts() -> None:
    existing = [model_from_dto(build_dto(0)), model_from_dto(build_dto(1))]

    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_collect_with_fakes(
        collect_request(limit=4),
        [build_raw(3)],
        existing=existing,
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.DATABASE_NOT_CONTINUOUS.value
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_incremental_overlapping_recent_window_writes_only_new_16() -> None:
    existing = [model_from_dto(build_dto(0)), model_from_dto(build_dto(1)), model_from_dto(build_dto(2))]

    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_collect_with_fakes(
        collect_request(limit=3),
        [build_raw(1), build_raw(2), build_raw(3)],
        existing=existing,
    )

    assert result.status == KlineCollectStatus.SUCCESS
    assert result.inserted_count == 1
    assert result.skipped_existing_count == 2
    assert build_dto(3).open_time_ms in repository.rows
    assert alert_sender.calls == []


def test_incremental_existing_conflict_blocks_without_overwrite_and_alerts() -> None:
    conflicting = model_from_dto(replace(build_dto(1), close_price=Decimal("1")))
    existing = [model_from_dto(build_dto(0)), conflicting]

    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_collect_with_fakes(
        collect_request(limit=2),
        [build_raw(0), build_raw(1)],
        existing=existing,
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.DATABASE_CONFLICT.value
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_incremental_filters_unclosed_kline_without_writing_it() -> None:
    unclosed = build_raw(3)
    server_time_ms = build_dto(3).close_time_ms

    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_collect_with_fakes(
        collect_request(limit=4),
        [build_raw(0), build_raw(1), build_raw(2), unclosed],
        server_time_ms=server_time_ms,
    )

    assert result.status == KlineCollectStatus.SUCCESS
    assert result.inserted_count == 3
    assert result.filtered_unclosed_count == 1
    assert build_dto(3).open_time_ms not in repository.rows
    assert alert_sender.calls == []


def test_incremental_batch_internal_gap_blocks_without_write_and_alerts() -> None:
    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_collect_with_fakes(
        collect_request(limit=3),
        [build_raw(0), build_raw(1), build_raw(3)],
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.BATCH_NOT_CONTINUOUS.value
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_incremental_task_lock_already_exists_skips_without_binance_or_write() -> None:
    task_lock = FakeTaskLock(acquired=False)

    result, repository, _lock, alert_sender, _quality_repo, _session, client = run_collect_with_fakes(
        collect_request(limit=4),
        [build_raw(0)],
        task_lock=task_lock,
    )

    assert result.status == KlineCollectStatus.SKIPPED
    assert result.exit_code == EXIT_QUALITY_BLOCKED
    assert client.get_server_time_calls == 0
    assert client.get_klines_calls == []
    assert repository.bulk_write_called is False
    assert alert_sender.calls == []


def test_incremental_redis_exception_fails_without_binance_or_write_and_alerts() -> None:
    task_lock = FakeTaskLock(raise_on_acquire=True)

    result, repository, _lock, alert_sender, _quality_repo, _session, client = run_collect_with_fakes(
        collect_request(limit=4),
        [build_raw(0)],
        task_lock=task_lock,
    )

    assert result.status == KlineCollectStatus.FAILED
    assert result.exit_code == EXIT_TASK_FAILED
    assert client.get_server_time_calls == 0
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_incremental_bulk_upsert_exception_rolls_back_without_partial_write() -> None:
    repository = FakeKlineRepository([model_from_dto(build_dto(0))], fail_on_bulk=True)

    result, repository, _lock, alert_sender, _quality_repo, session, _client = run_collect_with_fakes(
        collect_request(limit=2),
        [build_raw(0), build_raw(1)],
        repository=repository,
    )

    assert result.status == KlineCollectStatus.FAILED
    assert result.exit_code == EXIT_PERSIST_FAILED
    assert build_dto(1).open_time_ms not in repository.rows
    assert session.rollbacks >= 1
    assert session.nested_rollbacks >= 1
    assert len(alert_sender.calls) == 1


def test_incremental_collector_event_log_failure_still_alerts() -> None:
    collector_repository = FakeCollectorEventRepository(raise_on_create=True)

    result, repository, _lock, alert_sender, _quality_repo, session, client = run_collect_with_fakes(
        collect_request(limit=4),
        [build_raw(0)],
        collector_repository=collector_repository,
    )

    assert result.status == KlineCollectStatus.FAILED
    assert result.details["event_log_record_failed"] is True
    assert client.get_server_time_calls == 0
    assert repository.bulk_write_called is False
    assert session.rollbacks >= 1
    assert len(alert_sender.calls) == 1


def test_incremental_success_notify_success_flag_controls_success_alert() -> None:
    result, _repo, _lock, alert_sender, _quality_repo, _session, _client = run_collect_with_fakes(
        collect_request(limit=1, notify_success=False),
        [build_raw(0)],
    )

    assert result.status == KlineCollectStatus.SUCCESS
    assert alert_sender.calls == []

    result, _repo, _lock, alert_sender, _quality_repo, _session, _client = run_collect_with_fakes(
        collect_request(limit=1, notify_success=True),
        [build_raw(0)],
    )

    assert result.status == KlineCollectStatus.SUCCESS
    assert len(alert_sender.calls) == 1
    assert alert_sender.calls[0]["event"].severity.value == "info"


def test_incremental_hermes_submission_failure_returns_alert_failed_exit_code() -> None:
    failed_alert = FakeAlertSender(
        AlertSendResult(status=AlertSendStatus.SUBMIT_FAILED, error_message="Hermes unavailable")
    )

    result, _repo, _lock, _alert, _quality_repo, _session, _client = run_collect_with_fakes(
        collect_request(limit=3),
        [build_raw(0), build_raw(2)],
        alert_sender=failed_alert,
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert result.exit_code == EXIT_ALERT_FAILED
    assert result.alert_status == AlertSendStatus.SUBMIT_FAILED.value


def test_incremental_cli_allows_only_cli_trigger_and_no_send_alert() -> None:
    legacy_alert_flag = "--send" "-alert"
    source = Path("scripts/collect_4h_klines.py").read_text(encoding="utf-8")

    assert legacy_alert_flag not in source
    assert collect_script.main([legacy_alert_flag]) != EXIT_SUCCESS
    assert collect_script.main(["--trigger-source", "scheduler", "--dry-run"]) != EXIT_SUCCESS


def test_incremental_sources_do_not_use_forbidden_capabilities() -> None:
    from app.market_data.collector import kline_4h_collector_service

    source = inspect.getsource(kline_4h_collector_service) + Path("scripts/collect_4h_klines.py").read_text(
        encoding="utf-8"
    )
    forbidden_terms = [
        "--send" "-alert",
        "get_" "account",
        "get_" "position",
        "create_" "order",
        "listen" "Key",
        "/fapi/v1/" "ticker",
    ]

    for term in forbidden_terms:
        assert term not in source


def test_scheduler_can_call_service_directly_with_scheduler_trigger_source() -> None:
    result, _repo, _lock, _alert, _quality_repo, _session, _client = run_collect_with_fakes(
        collect_request(limit=1, trigger_source=TRIGGER_SOURCE_SCHEDULER),
        [build_raw(0)],
    )

    assert result.status == KlineCollectStatus.SUCCESS
