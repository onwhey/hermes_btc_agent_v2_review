from __future__ import annotations

import inspect
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from app.alerting.service import format_alert_message
from app.alerting.types import AlertSendResult, AlertSendStatus, AlertType
from app.core.exceptions import RedisError
from app.core.task_lock import RedisTaskLock
from app.market_data.backfill.kline_4h_backfill_service import run_manual_4h_backfill
from app.market_data.backfill.types import (
    EXIT_ALERT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_BLOCKED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    KlineBackfillStatus,
    ManualKlineBackfillRequest,
)
from app.market_data.kline_constants import KLINE_4H_INTERVAL_MS, KLINE_4H_INTERVAL_VALUE, TRIGGER_SOURCE_CLI
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_parser import parse_binance_kline
from app.market_data.kline_quality.types import KlineQualityIssueType
from app.storage.mysql.models.market_kline_4h import MarketKline4h
from app.storage.mysql.repositories.market_kline_4h_repository import find_conflicting_core_fields
from scripts import backfill_4h_klines as backfill_script


BASE_RAW_KLINE = [
    1_700_006_400_000,
    "35000.10000000",
    "36000.20000000",
    "34000.30000000",
    "35500.40000000",
    "123.45600000",
    1_700_020_799_999,
    "4567890.12300000",
    9876,
    "66.70000000",
    "2345678.90000000",
    "0",
]


def build_raw(offset: int = 0, *, close_price: str = "35500.40000000") -> list[Any]:
    raw = list(BASE_RAW_KLINE)
    shift_ms = offset * KLINE_4H_INTERVAL_MS
    raw[0] = int(raw[0]) + shift_ms
    raw[6] = int(raw[6]) + shift_ms
    raw[1] = str(Decimal(str(raw[1])) + Decimal(offset))
    raw[2] = str(Decimal(str(raw[2])) + Decimal(offset))
    raw[3] = str(Decimal(str(raw[3])) + Decimal(offset))
    raw[4] = close_price
    return raw


def build_dto(offset: int = 0, *, close_price: str = "35500.40000000") -> MarketKlineDTO:
    return parse_binance_kline(
        build_raw(offset, close_price=close_price),
        symbol="BTCUSDT",
        interval_value=KLINE_4H_INTERVAL_VALUE,
        trigger_source=TRIGGER_SOURCE_CLI,
    )


def model_from_dto(dto: MarketKlineDTO) -> MarketKline4h:
    return MarketKline4h(
        symbol=dto.symbol,
        interval_value=dto.interval_value,
        open_time_ms=dto.open_time_ms,
        open_price=dto.open_price,
        high_price=dto.high_price,
        low_price=dto.low_price,
        close_price=dto.close_price,
        volume=dto.volume,
        quote_volume=dto.quote_volume,
        trade_count=dto.trade_count,
        taker_buy_base_volume=dto.taker_buy_base_volume,
        taker_buy_quote_volume=dto.taker_buy_quote_volume,
        close_time_ms=dto.close_time_ms,
    )


def request_for_offsets(start_offset: int, end_offset: int, *, notify_success: bool = False) -> ManualKlineBackfillRequest:
    start = build_dto(start_offset).open_time_ms
    end = build_dto(end_offset).open_time_ms
    return ManualKlineBackfillRequest(
        symbol="BTCUSDT",
        interval_value=KLINE_4H_INTERVAL_VALUE,
        start_open_time_ms=start,
        end_open_time_ms=end,
        trigger_source=TRIGGER_SOURCE_CLI,
        confirm_write=True,
        notify_success=notify_success,
        limit_per_request=10,
    )


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushed = 0
        self.nested_rollbacks = 0

    def add(self, record: Any) -> None:
        self.added.append(record)

    def flush(self) -> None:
        self.flushed += 1

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def begin_nested(self) -> Any:
        session = self

        class Nested:
            def __enter__(self) -> "Nested":
                return self

            def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
                if exc_type is not None:
                    session.nested_rollbacks += 1
                return False

        return Nested()


class FakeBinanceClient:
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
        start = kwargs["start_time_ms"]
        end = kwargs["end_time_ms"]
        return [row for row in self.raw_klines if start <= int(row[0]) and int(row[6]) <= end]


class FakeTaskLock:
    def __init__(self, *, acquired: bool = True, raise_on_acquire: bool = False) -> None:
        self.acquired = acquired
        self.raise_on_acquire = raise_on_acquire
        self.acquire_calls: list[dict[str, Any]] = []
        self.release_calls: list[dict[str, Any]] = []

    def acquire_lock(self, **kwargs: Any) -> bool:
        self.acquire_calls.append(kwargs)
        if self.raise_on_acquire:
            raise RedisError("redis unavailable")
        return self.acquired

    def release_lock(self, **kwargs: Any) -> bool:
        self.release_calls.append(kwargs)
        return True


class FakeKlineRepository:
    def __init__(self, existing: Iterable[MarketKline4h] = (), *, fail_on_bulk: bool = False) -> None:
        self.rows = {int(row.open_time_ms): row for row in existing}
        self.fail_on_bulk = fail_on_bulk
        self.bulk_write_called = False

    def list_by_open_times(
        self,
        _db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        open_time_ms_list: Iterable[int],
    ) -> list[MarketKline4h]:
        return [self.rows[open_time_ms] for open_time_ms in open_time_ms_list if open_time_ms in self.rows]

    def get_previous_before(
        self,
        _db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        open_time_ms: int,
    ) -> MarketKline4h | None:
        previous = [key for key in self.rows if key < open_time_ms]
        return self.rows[max(previous)] if previous else None

    def get_next_after(
        self,
        _db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        open_time_ms: int,
    ) -> MarketKline4h | None:
        next_values = [key for key in self.rows if key > open_time_ms]
        return self.rows[min(next_values)] if next_values else None

    def bulk_upsert(self, _db_session: Any, klines: Iterable[MarketKlineDTO]) -> Any:
        self.bulk_write_called = True
        incoming = tuple(klines)
        if self.fail_on_bulk:
            raise RuntimeError("database write failed in the middle")
        inserted = 0
        skipped = 0
        for kline in incoming:
            existing = self.rows.get(kline.open_time_ms)
            if existing is not None:
                if find_conflicting_core_fields(existing, kline):
                    raise RuntimeError("conflict should have been blocked before write")
                skipped += 1
                continue
            inserted += 1
        for kline in incoming:
            self.rows.setdefault(kline.open_time_ms, model_from_dto(kline))
        return SimpleNamespace(input_count=len(incoming), inserted_count=inserted, skipped_count=skipped)


class FakeQualityRepository:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def create_quality_check_record(self, _db_session: Any, report: Any) -> Any:
        record = SimpleNamespace(id=len(self.records) + 1, report=report)
        self.records.append(record)
        return record


class FakeCollectorEventRepository:
    def __init__(self, *, raise_on_create: bool = False, raise_on_mark_failed: bool = False) -> None:
        self.raise_on_create = raise_on_create
        self.raise_on_mark_failed = raise_on_mark_failed
        self.records: list[Any] = []
        self.status_calls: list[dict[str, Any]] = []

    def create_running_event(self, _db_session: Any, **kwargs: Any) -> Any:
        if self.raise_on_create:
            raise RuntimeError("collector_event_log create failed")
        record = SimpleNamespace(id=len(self.records) + 1, status="running", kwargs=kwargs)
        self.records.append(record)
        return record

    def create_skipped_event(self, _db_session: Any, **kwargs: Any) -> Any:
        record = self.create_running_event(_db_session, **kwargs)
        record.status = "skipped"
        self.status_calls.append({"status": "skipped", "values": kwargs})
        return record

    def mark_success(self, _db_session: Any, event: Any, **values: Any) -> Any:
        event.status = "success"
        self.status_calls.append({"status": "success", "values": values})
        return event

    def mark_blocked(self, _db_session: Any, event: Any, **values: Any) -> Any:
        event.status = "blocked"
        self.status_calls.append({"status": "blocked", "values": values})
        return event

    def mark_failed(self, _db_session: Any, event: Any, **values: Any) -> Any:
        if self.raise_on_mark_failed:
            raise RuntimeError("collector_event_log mark_failed failed")
        event.status = "failed"
        self.status_calls.append({"status": "failed", "values": values})
        return event


class FakeAlertSender:
    def __init__(self, result: AlertSendResult | None = None) -> None:
        self.result = result or AlertSendResult(
            status=AlertSendStatus.SUBMITTED_TO_HERMES,
            attempted_real_send=True,
        )
        self.calls: list[dict[str, Any]] = []

    def __call__(self, event: Any, **kwargs: Any) -> AlertSendResult:
        self.calls.append({"event": event, "kwargs": kwargs})
        return self.result


def run_backfill_with_fakes(
    request: ManualKlineBackfillRequest,
    raw_klines: list[list[Any]],
    *,
    existing: Iterable[MarketKline4h] = (),
    server_time_ms: int | None = None,
    repository: FakeKlineRepository | None = None,
    task_lock: FakeTaskLock | None = None,
    alert_sender: FakeAlertSender | None = None,
    collector_repository: FakeCollectorEventRepository | None = None,
) -> tuple[Any, FakeKlineRepository, FakeTaskLock, FakeAlertSender, FakeQualityRepository, FakeSession, FakeBinanceClient]:
    dto = build_dto(0)
    fake_client = FakeBinanceClient(
        raw_klines,
        server_time_ms=server_time_ms if server_time_ms is not None else dto.close_time_ms + 100 * KLINE_4H_INTERVAL_MS,
    )
    fake_repository = repository or FakeKlineRepository(existing)
    fake_task_lock = task_lock or FakeTaskLock()
    fake_alert_sender = alert_sender or FakeAlertSender()
    fake_quality_repository = FakeQualityRepository()
    fake_session = FakeSession()

    result = run_manual_4h_backfill(
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


def test_backfill_middle_single_gap_inserts_between_existing_neighbors() -> None:
    existing = [model_from_dto(build_dto(0)), model_from_dto(build_dto(1)), model_from_dto(build_dto(3))]

    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_backfill_with_fakes(
        request_for_offsets(2, 2),
        [build_raw(2)],
        existing=existing,
    )

    assert result.status == KlineBackfillStatus.SUCCESS
    assert result.exit_code == EXIT_SUCCESS
    assert result.inserted_count == 1
    assert build_dto(2).open_time_ms in repository.rows
    assert alert_sender.calls == []


def test_backfill_two_missing_klines_connects_previous_and_next_neighbors() -> None:
    existing = [model_from_dto(build_dto(0)), model_from_dto(build_dto(3))]

    result, repository, _lock, _alert, _quality_repo, _session, _client = run_backfill_with_fakes(
        request_for_offsets(1, 2),
        [build_raw(1), build_raw(2)],
        existing=existing,
    )

    assert result.status == KlineBackfillStatus.SUCCESS
    assert result.inserted_count == 2
    assert build_dto(1).open_time_ms in repository.rows
    assert build_dto(2).open_time_ms in repository.rows


def test_existing_conflicting_kline_blocks_without_formal_write_and_alerts() -> None:
    dto2 = build_dto(2)
    conflicting = model_from_dto(replace(dto2, close_price=Decimal("1")))
    existing = [model_from_dto(build_dto(1)), conflicting, model_from_dto(build_dto(3))]

    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_backfill_with_fakes(
        request_for_offsets(2, 2),
        [build_raw(2)],
        existing=existing,
    )

    assert result.status == KlineBackfillStatus.BLOCKED
    assert result.exit_code == EXIT_QUALITY_BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.DATABASE_CONFLICT.value
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_binance_batch_gap_blocks_without_formal_write_and_alerts() -> None:
    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_backfill_with_fakes(
        request_for_offsets(0, 3),
        [build_raw(0), build_raw(1), build_raw(3)],
    )

    assert result.status == KlineBackfillStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.BATCH_NOT_CONTINUOUS.value
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    message = format_alert_message(event)
    assert event.alert_type == AlertType.KLINE_DATA_QUALITY_ERROR
    assert event.severity.value == "error"
    assert event.title == "手动补 K 被质量检查阻断"
    assert "Binance 返回的历史 K线区间存在缺失、断档或不连续。" in message
    assert "采集事件日志 collector_event_log" in message
    assert "数据质量记录 data_quality_check" in message
    assert "Binance REST 官方返回" in message
    assert "formal_write_performed" not in message
    assert "requested_start_open_time_ms" not in message
    assert "quality_summary" not in message
    assert "action" not in message


def test_backfill_not_connected_to_previous_database_kline_blocks_and_alerts() -> None:
    existing = [model_from_dto(build_dto(0))]

    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_backfill_with_fakes(
        request_for_offsets(2, 2),
        [build_raw(2)],
        existing=existing,
    )

    assert result.status == KlineBackfillStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.DATABASE_NOT_CONTINUOUS.value
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_backfill_not_connected_to_next_database_kline_blocks_and_alerts() -> None:
    existing = [model_from_dto(build_dto(1)), model_from_dto(build_dto(4))]

    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_backfill_with_fakes(
        request_for_offsets(2, 2),
        [build_raw(2)],
        existing=existing,
    )

    assert result.status == KlineBackfillStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.DATABASE_NOT_CONTINUOUS.value
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_unclosed_kline_blocks_without_formal_write_and_sends_notice_alert() -> None:
    dto = build_dto(0)

    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_backfill_with_fakes(
        request_for_offsets(0, 0),
        [build_raw(0)],
        server_time_ms=dto.close_time_ms,
    )

    assert result.status == KlineBackfillStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.UNCLOSED_KLINE.value
    assert result.inserted_count == 0
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    message = format_alert_message(event)

    assert event.alert_type == AlertType.MANUAL_BACKFILL_NOTICE
    assert event.severity.value == "notice"
    assert event.title == "手动补 K 已安全阻断"
    assert "【手动补 K 已安全阻断】" in message
    assert "级别：提醒" in message
    assert "原因：" in message
    assert "请求区间包含尚未收盘的 4h K线" in message
    assert "结果：" in message
    assert "系统已阻断写入，正式 K线表未被修改。" in message
    assert "建议：" in message
    assert "结束时间参数 end-utc" in message
    assert "追踪ID：" in message
    assert result.trace_id in message
    assert "formal_write_performed" not in message
    assert "requested_start_open_time_ms" not in message
    assert "quality_summary" not in message
    assert "action" not in message
    assert "Manual 4h Kline backfill did not complete" not in message
    assert "K 线数据质量异常提醒" not in message


def test_bulk_upsert_exception_does_not_leave_partial_formal_kline_write() -> None:
    repository = FakeKlineRepository([model_from_dto(build_dto(0))], fail_on_bulk=True)

    result, repository, _lock, alert_sender, _quality_repo, session, _client = run_backfill_with_fakes(
        request_for_offsets(1, 1),
        [build_raw(1)],
        repository=repository,
    )

    assert result.status == KlineBackfillStatus.FAILED
    assert result.exit_code == EXIT_PERSIST_FAILED
    assert build_dto(1).open_time_ms not in repository.rows
    assert session.rollbacks >= 1
    assert session.nested_rollbacks >= 1
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    assert event.severity.value == "critical"
    assert event.title == "手动补 K 执行失败"


def test_task_lock_already_exists_skips_without_binance_or_formal_write() -> None:
    task_lock = FakeTaskLock(acquired=False)

    result, repository, _lock, alert_sender, _quality_repo, _session, client = run_backfill_with_fakes(
        request_for_offsets(0, 0),
        [build_raw(0)],
        task_lock=task_lock,
    )

    assert result.status == KlineBackfillStatus.SKIPPED
    assert result.exit_code == EXIT_QUALITY_BLOCKED
    assert client.get_server_time_calls == 0
    assert client.get_klines_calls == []
    assert repository.bulk_write_called is False
    assert alert_sender.calls == []


def test_redis_exception_fails_without_binance_or_formal_write_and_alerts() -> None:
    task_lock = FakeTaskLock(raise_on_acquire=True)

    result, repository, _lock, alert_sender, _quality_repo, _session, client = run_backfill_with_fakes(
        request_for_offsets(0, 0),
        [build_raw(0)],
        task_lock=task_lock,
    )

    assert result.status == KlineBackfillStatus.FAILED
    assert result.exit_code == EXIT_TASK_FAILED
    assert client.get_server_time_calls == 0
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_collector_event_create_failure_still_alerts_without_binance_or_formal_write() -> None:
    collector_repository = FakeCollectorEventRepository(raise_on_create=True)

    result, repository, _lock, alert_sender, _quality_repo, session, client = run_backfill_with_fakes(
        request_for_offsets(0, 0),
        [build_raw(0)],
        collector_repository=collector_repository,
    )

    assert result.status == KlineBackfillStatus.FAILED
    assert result.exit_code == EXIT_TASK_FAILED
    assert result.details["event_log_record_failed"] is True
    assert client.get_server_time_calls == 0
    assert client.get_klines_calls == []
    assert repository.bulk_write_called is False
    assert session.rollbacks >= 1
    assert len(alert_sender.calls) == 1


def test_collector_event_mark_failed_failure_still_alerts_without_crashing() -> None:
    collector_repository = FakeCollectorEventRepository(raise_on_mark_failed=True)
    task_lock = FakeTaskLock(raise_on_acquire=True)

    result, repository, _lock, alert_sender, _quality_repo, session, client = run_backfill_with_fakes(
        request_for_offsets(0, 0),
        [build_raw(0)],
        task_lock=task_lock,
        collector_repository=collector_repository,
    )

    assert result.status == KlineBackfillStatus.FAILED
    assert result.exit_code == EXIT_TASK_FAILED
    assert result.details["event_log_record_failed"] is True
    assert client.get_server_time_calls == 0
    assert repository.bulk_write_called is False
    assert session.rollbacks >= 1
    assert len(alert_sender.calls) == 1


def test_redis_release_lock_does_not_delete_other_owner_lock() -> None:
    class FakeRedisClient:
        def __init__(self) -> None:
            self.eval_calls: list[tuple[Any, ...]] = []
            self.delete_calls = 0

        def eval(self, *args: Any) -> int:
            self.eval_calls.append(args)
            return 0

        def delete(self, _key: str) -> int:
            self.delete_calls += 1
            raise AssertionError("release_lock must not call delete outside Lua")

    redis_client = FakeRedisClient()
    task_lock = RedisTaskLock(redis_client=redis_client)

    released = task_lock.release_lock(key="kline_write:BTCUSDT:4h", owner="owner-a")

    assert released is False
    assert len(redis_client.eval_calls) == 1
    script, key_count, key, owner = redis_client.eval_calls[0]
    assert "GET" in script
    assert "DEL" in script
    assert key_count == 1
    assert key == "kline_write:BTCUSDT:4h"
    assert owner == "owner-a"
    assert redis_client.delete_calls == 0


def test_parameter_validation_requires_trigger_source_cli_and_confirm_write() -> None:
    missing_confirm = ManualKlineBackfillRequest(
        start_open_time_ms=build_dto(0).open_time_ms,
        end_open_time_ms=build_dto(0).open_time_ms,
        trigger_source=TRIGGER_SOURCE_CLI,
        confirm_write=False,
    )

    result, repository, _lock, _alert, _quality_repo, _session, client = run_backfill_with_fakes(
        missing_confirm,
        [build_raw(0)],
    )

    assert result.exit_code == EXIT_PARAMETER_ERROR
    assert client.get_server_time_calls == 0
    assert repository.bulk_write_called is False


def test_send_alert_parameter_is_not_reintroduced() -> None:
    source = Path("scripts/backfill_4h_klines.py").read_text(encoding="utf-8")
    legacy_alert_flag = "--send" "-alert"

    assert legacy_alert_flag not in source
    assert backfill_script.main([legacy_alert_flag]) == EXIT_PARAMETER_ERROR


def test_cli_missing_or_scheduler_trigger_source_is_rejected() -> None:
    start = str(build_dto(0).open_time_ms)
    end = str(build_dto(0).open_time_ms)

    assert backfill_script.main(["--start-open-time-ms", start, "--end-open-time-ms", end, "--dry-run"]) == (
        EXIT_PARAMETER_ERROR
    )
    assert backfill_script.main(
        [
            "--start-open-time-ms",
            start,
            "--end-open-time-ms",
            end,
            "--trigger-source",
            "scheduler",
            "--dry-run",
        ]
    ) == EXIT_PARAMETER_ERROR


def test_success_does_not_notify_without_notify_success_flag() -> None:
    result, _repo, _lock, alert_sender, _quality_repo, _session, _client = run_backfill_with_fakes(
        request_for_offsets(0, 0),
        [build_raw(0)],
    )

    assert result.status == KlineBackfillStatus.SUCCESS
    assert alert_sender.calls == []


def test_success_notify_success_sends_fixed_template_success_alert() -> None:
    result, _repo, _lock, alert_sender, _quality_repo, _session, _client = run_backfill_with_fakes(
        request_for_offsets(0, 0, notify_success=True),
        [build_raw(0)],
    )

    assert result.status == KlineBackfillStatus.SUCCESS
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    message = format_alert_message(event)

    assert event.severity.value == "info"
    assert event.title == "手动补 K 已完成"
    assert "采集事件日志 collector_event_log" in message
    assert "追踪ID" in message
    assert "trace_id" not in message


def test_dry_run_notify_success_alert_clearly_marks_no_formal_write() -> None:
    request = replace(
        request_for_offsets(0, 0, notify_success=True),
        dry_run=True,
        confirm_write=False,
    )

    result, repository, _lock, alert_sender, _quality_repo, _session, _client = run_backfill_with_fakes(
        request,
        [build_raw(0)],
    )

    assert result.status == KlineBackfillStatus.SUCCESS
    assert result.details["dry_run"] is True
    assert result.details["formal_write_performed"] is False
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    message = format_alert_message(event)

    assert event.title == "手动补 K 预演检查（dry-run）通过"
    assert "预演检查（dry-run）" in event.summary
    assert "预演模式（dry-run）只完成请求、解析和质量检查，正式 K线表未被修改。" in message
    assert "采集事件日志 collector_event_log" in message
    internal_context = event.details["_internal_context"]
    assert internal_context["dry_run"] is True
    assert internal_context["formal_write_performed"] is False


def test_hermes_submission_failure_returns_alert_failed_exit_code() -> None:
    failed_alert = FakeAlertSender(
        AlertSendResult(status=AlertSendStatus.SUBMIT_FAILED, error_message="Hermes unavailable")
    )

    result, _repo, _lock, _alert, _quality_repo, _session, _client = run_backfill_with_fakes(
        request_for_offsets(0, 1),
        [build_raw(0)],
        alert_sender=failed_alert,
    )

    assert result.status == KlineBackfillStatus.BLOCKED
    assert result.exit_code == EXIT_ALERT_FAILED
    assert result.alert_status == AlertSendStatus.SUBMIT_FAILED.value


def test_backfill_sources_do_not_use_deepseek_trading_or_private_binance_interfaces() -> None:
    from app.market_data.backfill import kline_4h_backfill_service

    source = inspect.getsource(kline_4h_backfill_service) + Path("scripts/backfill_4h_klines.py").read_text(
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


def test_collector_event_log_migration_only_creates_event_log_table() -> None:
    text = Path("migrations/versions/20260511_08_create_collector_event_log.py").read_text(
        encoding="utf-8"
    )

    assert '"collector_event_log"' in text
    assert '"market_kline_4h"' not in text
    assert '"strategy"' not in text
    assert '"trade_advice"' not in text
    assert "insert(" not in text
