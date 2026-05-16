from __future__ import annotations

import inspect
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from app.alerting.service import format_alert_message
from app.alerting.types import AlertSendResult, AlertSendStatus
from app.market_data.collector.kline_1d_incremental_collector import (
    build_incremental_1d_request_range,
    expected_latest_closed_1d_open_time,
    run_incremental_1d_collection,
)
from app.market_data.collector.kline_1d_incremental_types import (
    EXIT_ALERT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_BLOCKED,
    EXIT_SKIPPED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    IncrementalKline1dCollectRequest,
    KLINE_1D_INCREMENTAL_EVENT_TYPE,
    KlineCollectStatus,
)
from app.market_data.kline_constants import (
    KLINE_1D_INTERVAL_MS,
    KLINE_1D_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
    TRIGGER_SOURCE_SCHEDULER,
)
from app.market_data.kline_quality.types import KlineQualityIssueType
from scripts import collect_1d_klines as collect_script
from tests.test_1d_kline_manual_backfill import (
    FakeAlertSender,
    FakeBinanceClient,
    FakeCollectorEventRepository,
    FakeKline1dRepository,
    FakeQualityRepository,
    FakeSession,
    FakeTaskLock,
    build_dto,
    build_raw,
    model_from_dto,
)


HALF_DAY_MS = KLINE_1D_INTERVAL_MS // 2


def collect_request(
    *,
    notify_success: bool = False,
    dry_run: bool = False,
    confirm_write: bool = True,
    trigger_source: str = TRIGGER_SOURCE_CLI,
    max_closed_count: int = 30,
) -> IncrementalKline1dCollectRequest:
    return IncrementalKline1dCollectRequest(
        symbol="BTCUSDT",
        interval_value=KLINE_1D_INTERVAL_VALUE,
        trigger_source=trigger_source,
        dry_run=dry_run,
        confirm_write=confirm_write,
        notify_success=notify_success,
        max_closed_count=max_closed_count,
    )


def server_time_during_day(offset: int) -> int:
    return build_dto(offset).open_time_ms + HALF_DAY_MS


def run_collect_with_fakes(
    request: IncrementalKline1dCollectRequest,
    raw_klines: list[list[Any]],
    *,
    existing: Iterable[Any] = (),
    server_time_ms: int | None = None,
    repository: FakeKline1dRepository | None = None,
    task_lock: FakeTaskLock | None = None,
    alert_sender: FakeAlertSender | None = None,
    collector_repository: FakeCollectorEventRepository | None = None,
) -> tuple[
    Any,
    FakeKline1dRepository,
    FakeTaskLock,
    FakeAlertSender,
    FakeQualityRepository,
    FakeSession,
    FakeBinanceClient,
    FakeCollectorEventRepository,
]:
    fake_client = FakeBinanceClient(
        raw_klines,
        server_time_ms=server_time_ms if server_time_ms is not None else server_time_during_day(4),
    )
    fake_repository = repository or FakeKline1dRepository(existing)
    fake_task_lock = task_lock or FakeTaskLock()
    fake_alert_sender = alert_sender or FakeAlertSender()
    fake_quality_repository = FakeQualityRepository()
    fake_session = FakeSession()
    fake_collector_repository = collector_repository or FakeCollectorEventRepository()

    result = run_incremental_1d_collection(
        request,
        db_session=fake_session,
        binance_client=fake_client,
        task_lock=fake_task_lock,
        kline_repository=fake_repository,
        data_quality_repository=fake_quality_repository,
        collector_event_repository=fake_collector_repository,
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
        fake_collector_repository,
    )


def test_empty_1d_table_blocks_without_auto_initialization_or_binance_request() -> None:
    result, repository, lock, alert_sender, _quality, _session, client, collector = run_collect_with_fakes(
        collect_request(),
        [build_raw(0), build_raw(1), build_raw(2)],
        existing=[],
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert result.exit_code == EXIT_QUALITY_BLOCKED
    assert "1d 数据尚未初始化" in result.message
    assert client.get_server_time_calls == 0
    assert client.get_klines_calls == []
    assert repository.bulk_write_called is False
    assert lock.acquire_calls[0]["key"] == "kline_write:BTCUSDT:1d"
    assert collector.records[0].kwargs["event_type"] == KLINE_1D_INCREMENTAL_EVENT_TYPE
    assert collector.records[0].kwargs["interval_value"] == KLINE_1D_INTERVAL_VALUE
    assert collector.status_calls[-1]["status"] == "blocked"
    assert len(alert_sender.calls) == 1


def test_expected_latest_closed_1d_is_based_on_server_time_not_current_day() -> None:
    current_day_open = build_dto(3).open_time_ms

    assert expected_latest_closed_1d_open_time(current_day_open) == build_dto(2).open_time_ms
    assert expected_latest_closed_1d_open_time(current_day_open + HALF_DAY_MS) == build_dto(2).open_time_ms


def test_incremental_overlap_fetch_skips_existing_boundary_and_writes_new_days() -> None:
    existing = [model_from_dto(build_dto(1))]

    result, repository, _lock, alert_sender, _quality, _session, client, collector = run_collect_with_fakes(
        collect_request(),
        [build_raw(1), build_raw(2), build_raw(3)],
        existing=existing,
        server_time_ms=server_time_during_day(4),
    )

    assert result.status == KlineCollectStatus.SUCCESS
    assert result.exit_code == EXIT_SUCCESS
    assert result.requested_count == 3
    assert result.inserted_count == 2
    assert result.skipped_existing_count == 1
    assert build_dto(2).open_time_ms in repository.rows
    assert build_dto(3).open_time_ms in repository.rows
    assert repository.bulk_write_called is True
    assert repository.wrote_4h is False
    assert client.get_klines_calls[0]["start_time_ms"] == build_dto(1).open_time_ms
    assert client.get_klines_calls[0]["limit"] == 4
    assert collector.status_calls[-1]["values"]["inserted_count"] == 2
    assert collector.status_calls[-1]["values"]["skipped_count"] == 1
    assert alert_sender.calls == []


def test_request_range_includes_current_unclosed_probe_but_closed_count_remains_bounded() -> None:
    request_range = build_incremental_1d_request_range(
        latest_open_time_ms=build_dto(1).open_time_ms,
        expected_latest_open_time_ms=build_dto(3).open_time_ms,
    )

    assert request_range.requested_closed_count == 3
    assert request_range.limit == 4
    assert request_range.end_time_ms_for_binance == build_dto(4).open_time_ms + KLINE_1D_INTERVAL_MS - 1


def test_current_unclosed_daily_kline_is_filtered_without_error() -> None:
    existing = [model_from_dto(build_dto(1))]

    result, repository, _lock, alert_sender, _quality, _session, _client, collector = run_collect_with_fakes(
        collect_request(),
        [build_raw(1), build_raw(2), build_raw(3)],
        existing=existing,
        server_time_ms=server_time_during_day(3),
    )

    assert result.status == KlineCollectStatus.SUCCESS
    assert result.inserted_count == 1
    assert result.filtered_unclosed_count == 1
    assert result.issue_count == 0
    assert build_dto(2).open_time_ms in repository.rows
    assert build_dto(3).open_time_ms not in repository.rows
    assert collector.status_calls[-1]["values"]["filtered_unclosed_count"] == 1
    assert alert_sender.calls == []


def test_formal_table_future_or_unclosed_1d_row_blocks_without_rest_kline_fetch() -> None:
    existing = [model_from_dto(build_dto(3))]

    result, repository, _lock, alert_sender, _quality, _session, client, _collector = run_collect_with_fakes(
        collect_request(),
        [build_raw(1), build_raw(2), build_raw(3)],
        existing=existing,
        server_time_ms=server_time_during_day(3),
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert "未收盘 K线误写正式表" in result.message
    assert client.get_server_time_calls == 1
    assert client.get_klines_calls == []
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_incremental_rest_batch_gap_blocks_without_writing_later_days() -> None:
    existing = [model_from_dto(build_dto(0))]

    result, repository, _lock, alert_sender, _quality, _session, _client, _collector = run_collect_with_fakes(
        collect_request(),
        [build_raw(0), build_raw(2)],
        existing=existing,
        server_time_ms=server_time_during_day(3),
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.BATCH_NOT_CONTINUOUS.value
    assert repository.bulk_write_called is False
    assert build_dto(2).open_time_ms not in repository.rows
    assert len(alert_sender.calls) == 1


def test_incremental_requires_rest_overlap_with_latest_database_boundary() -> None:
    existing = [model_from_dto(build_dto(1))]

    result, repository, *_ = run_collect_with_fakes(
        collect_request(),
        [build_raw(2), build_raw(3)],
        existing=existing,
        server_time_ms=server_time_during_day(4),
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.BATCH_NOT_CONTINUOUS.value
    assert repository.bulk_write_called is False
    assert build_dto(2).open_time_ms not in repository.rows


def test_invalid_1d_fields_block_incremental_without_formal_write() -> None:
    existing = [model_from_dto(build_dto(0))]
    bad_cases = [
        [build_raw(0, close_price="0"), build_raw(1)],
        [build_raw(0, volume="-1"), build_raw(1)],
        [build_raw(0, open_time_shift_ms=1), build_raw(1)],
        [build_raw(0, close_time_shift_ms=1), build_raw(1)],
    ]

    for raw_klines in bad_cases:
        result, repository, *_ = run_collect_with_fakes(
            collect_request(),
            raw_klines,
            existing=existing,
            server_time_ms=server_time_during_day(2),
        )
        assert result.status == KlineCollectStatus.BLOCKED
        assert repository.bulk_write_called is False


def test_existing_conflicting_1d_row_blocks_without_overwrite() -> None:
    dto1 = build_dto(1)
    conflicting = model_from_dto(replace(dto1, close_price=Decimal("1")))
    existing = [model_from_dto(build_dto(0)), conflicting]

    result, repository, *_ = run_collect_with_fakes(
        collect_request(),
        [build_raw(1), build_raw(2)],
        existing=existing,
        server_time_ms=server_time_during_day(3),
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.DATABASE_CONFLICT.value
    assert repository.bulk_write_called is False
    assert repository.rows[dto1.open_time_ms].close_price == Decimal("1")


def test_repeating_incremental_collection_is_idempotent_and_does_not_duplicate() -> None:
    repository = FakeKline1dRepository([model_from_dto(build_dto(0))])

    first, repository, *_ = run_collect_with_fakes(
        collect_request(),
        [build_raw(0), build_raw(1)],
        repository=repository,
        server_time_ms=server_time_during_day(2),
    )
    second, repository, *_ = run_collect_with_fakes(
        collect_request(),
        [build_raw(0), build_raw(1)],
        repository=repository,
        server_time_ms=server_time_during_day(2),
    )

    assert first.status == KlineCollectStatus.SUCCESS
    assert first.inserted_count == 1
    assert second.status == KlineCollectStatus.SUCCESS
    assert second.inserted_count == 0
    assert len(repository.rows) == 2


def test_dry_run_does_not_write_formal_1d_table_or_send_failure_alert() -> None:
    existing = [model_from_dto(build_dto(0))]

    result, repository, _lock, alert_sender, *_ = run_collect_with_fakes(
        collect_request(dry_run=True, confirm_write=False),
        [build_raw(0), build_raw(1)],
        existing=existing,
        server_time_ms=server_time_during_day(2),
    )

    assert result.status == KlineCollectStatus.SUCCESS
    assert result.details["dry_run"] is True
    assert result.details["formal_write_performed"] is False
    assert repository.bulk_write_called is False
    assert build_dto(1).open_time_ms not in repository.rows
    assert alert_sender.calls == []


def test_incremental_records_event_type_trigger_source_counts_and_trace_id() -> None:
    existing = [model_from_dto(build_dto(0))]

    result, _repository, _lock, _alert, _quality, _session, _client, collector = run_collect_with_fakes(
        collect_request(trigger_source=TRIGGER_SOURCE_SCHEDULER),
        [build_raw(0), build_raw(1)],
        existing=existing,
        server_time_ms=server_time_during_day(2),
    )

    assert result.status == KlineCollectStatus.SUCCESS
    record = collector.records[0]
    assert record.kwargs["event_type"] == KLINE_1D_INCREMENTAL_EVENT_TYPE
    assert record.kwargs["interval_value"] == KLINE_1D_INTERVAL_VALUE
    assert record.kwargs["trigger_source"] == TRIGGER_SOURCE_SCHEDULER
    assert record.kwargs["trace_id"]
    assert collector.status_calls[-1]["values"]["inserted_count"] == 1
    assert collector.status_calls[-1]["values"]["filtered_unclosed_count"] == 0


def test_incremental_success_notify_success_sends_compact_chinese_alert_without_delivery_claim() -> None:
    existing = [model_from_dto(build_dto(0))]

    result, _repo, _lock, alert_sender, *_ = run_collect_with_fakes(
        collect_request(notify_success=True),
        [build_raw(0), build_raw(1)],
        existing=existing,
        server_time_ms=server_time_during_day(2),
    )

    assert result.status == KlineCollectStatus.SUCCESS
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    message = format_alert_message(event)
    assert event.severity.value == "info"
    assert "1d" in message
    assert "追踪ID" in message
    assert "无法确认 UTC" not in message
    assert "鏃犳硶纭" not in message
    assert "UTC" in message
    assert "微信发送成功" not in message
    assert "微信已送达" not in message
    assert "delivered" not in message
    assert "weixin_success" not in message


def test_incremental_blocked_alert_is_chinese_and_does_not_claim_delivery() -> None:
    existing = [model_from_dto(build_dto(0))]

    result, _repo, _lock, alert_sender, *_ = run_collect_with_fakes(
        collect_request(),
        [build_raw(0), build_raw(2)],
        existing=existing,
        server_time_ms=server_time_during_day(3),
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert len(alert_sender.calls) == 1
    message = format_alert_message(alert_sender.calls[0]["event"])
    assert "1d" in message
    assert "不要人工改数" in message or "没有人工改数" in message
    assert "微信发送成功" not in message
    assert "微信已送达" not in message
    assert "delivered" not in message


def test_incremental_hermes_submission_failure_returns_alert_failed_exit_code() -> None:
    existing = [model_from_dto(build_dto(0))]
    failed_alert = FakeAlertSender(
        AlertSendResult(status=AlertSendStatus.SUBMIT_FAILED, error_message="Hermes unavailable")
    )

    result, *_ = run_collect_with_fakes(
        collect_request(),
        [build_raw(0), build_raw(2)],
        existing=existing,
        server_time_ms=server_time_during_day(3),
        alert_sender=failed_alert,
    )

    assert result.status == KlineCollectStatus.BLOCKED
    assert result.exit_code == EXIT_ALERT_FAILED
    assert result.alert_status == AlertSendStatus.SUBMIT_FAILED.value


def test_incremental_bulk_upsert_exception_rolls_back_without_partial_write() -> None:
    repository = FakeKline1dRepository([model_from_dto(build_dto(0))], fail_on_bulk=True)

    result, repository, _lock, alert_sender, _quality, session, *_ = run_collect_with_fakes(
        collect_request(),
        [build_raw(0), build_raw(1)],
        repository=repository,
        server_time_ms=server_time_during_day(2),
    )

    assert result.status == KlineCollectStatus.FAILED
    assert result.exit_code == EXIT_PERSIST_FAILED
    assert build_dto(1).open_time_ms not in repository.rows
    assert session.rollbacks >= 1
    assert session.nested_rollbacks >= 1
    assert len(alert_sender.calls) == 1


def test_incremental_task_lock_already_exists_skips_without_binance_or_write() -> None:
    task_lock = FakeTaskLock(acquired=False)

    result, repository, _lock, alert_sender, _quality, _session, client, _collector = run_collect_with_fakes(
        collect_request(),
        [build_raw(0)],
        existing=[model_from_dto(build_dto(0))],
        task_lock=task_lock,
    )

    assert result.status == KlineCollectStatus.SKIPPED
    assert result.exit_code == EXIT_SKIPPED
    assert client.get_server_time_calls == 0
    assert client.get_klines_calls == []
    assert repository.bulk_write_called is False
    assert alert_sender.calls == []


def test_incremental_redis_exception_fails_without_binance_or_write_and_alerts() -> None:
    task_lock = FakeTaskLock(raise_on_acquire=True)

    result, repository, _lock, alert_sender, _quality, _session, client, _collector = run_collect_with_fakes(
        collect_request(),
        [build_raw(0)],
        existing=[model_from_dto(build_dto(0))],
        task_lock=task_lock,
    )

    assert result.status == KlineCollectStatus.FAILED
    assert result.exit_code == EXIT_TASK_FAILED
    assert client.get_server_time_calls == 0
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_parameter_validation_requires_confirm_write_before_external_access() -> None:
    result, repository, _lock, _alert, _quality, _session, client, _collector = run_collect_with_fakes(
        collect_request(confirm_write=False),
        [build_raw(0), build_raw(1)],
        existing=[model_from_dto(build_dto(0))],
    )

    assert result.exit_code == EXIT_PARAMETER_ERROR
    assert client.get_server_time_calls == 0
    assert repository.bulk_write_called is False


def test_collect_1d_cli_rejects_scheduler_trigger_and_non_1d_interval_before_service() -> None:
    assert collect_script.main(["--trigger-source", "scheduler", "--dry-run"]) == EXIT_PARAMETER_ERROR
    assert collect_script.main(["--interval", "4h", "--trigger-source", "cli", "--dry-run"]) == EXIT_PARAMETER_ERROR


def test_incremental_sources_do_not_use_4h_repository_deepseek_trading_or_private_interfaces() -> None:
    from app.market_data.collector import (
        kline_1d_incremental_alerts,
        kline_1d_incremental_collector,
        kline_1d_incremental_flow,
        kline_1d_incremental_quality,
    )

    source = (
        inspect.getsource(kline_1d_incremental_alerts)
        + inspect.getsource(kline_1d_incremental_collector)
        + inspect.getsource(kline_1d_incremental_flow)
        + inspect.getsource(kline_1d_incremental_quality)
        + Path("scripts/collect_1d_klines.py").read_text(encoding="utf-8")
    )
    forbidden_terms = [
        "MarketKline4hRepository",
        "market_kline_4h",
        "get_" "account",
        "get_" "position",
        "create_" "order",
        "listen" "Key",
        "/fapi/v1/" "ticker",
        "DeepSeekClient",
        "deepseek_client",
        "from app.llm",
        "from app.ai",
    ]

    for term in forbidden_terms:
        assert term not in source
