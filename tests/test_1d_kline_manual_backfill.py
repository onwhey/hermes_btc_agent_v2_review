from __future__ import annotations

import inspect
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Iterable

from app.alerting.service import format_alert_message
from app.alerting.types import AlertSendResult, AlertSendStatus, AlertType
from app.core.exceptions import RedisError
from app.market_data.backfill.kline_1d_backfill_service import run_manual_1d_backfill
from app.market_data.backfill.kline_1d_pipeline import (
    build_1d_binance_kline_request_ranges,
    validate_1d_backfill_request,
)
from app.market_data.backfill.kline_1d_types import (
    BACKFILL_1D_EVENT_TYPE,
    EXIT_ALERT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_BLOCKED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    Kline1dBackfillStatus,
    ManualKline1dBackfillRequest,
)
from app.market_data.kline_constants import KLINE_1D_INTERVAL_MS, KLINE_1D_INTERVAL_VALUE, TRIGGER_SOURCE_CLI
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_parser import parse_binance_kline
from app.market_data.kline_quality.types import KlineQualityIssueType
from app.storage.mysql.models.market_kline_1d import MarketKline1d
from app.storage.mysql.models.market_kline_4h import MarketKline4h
from app.storage.mysql.repositories.market_kline_1d_repository import find_conflicting_1d_core_fields
from scripts import backfill_1d_klines as backfill_script


BASE_OPEN_TIME_MS = 1_700_006_400_000
BASE_RAW_1D_KLINE = [
    BASE_OPEN_TIME_MS,
    "35000.10000000",
    "36000.20000000",
    "34000.30000000",
    "35500.40000000",
    "123.45600000",
    BASE_OPEN_TIME_MS + KLINE_1D_INTERVAL_MS - 1,
    "4567890.12300000",
    9876,
    "66.70000000",
    "2345678.90000000",
    "0",
]


def build_raw(
    offset: int = 0,
    *,
    close_price: str = "35500.40000000",
    open_time_shift_ms: int = 0,
    close_time_shift_ms: int = 0,
    volume: str = "123.45600000",
) -> list[Any]:
    raw = list(BASE_RAW_1D_KLINE)
    shift_ms = offset * KLINE_1D_INTERVAL_MS + open_time_shift_ms
    raw[0] = int(raw[0]) + shift_ms
    raw[6] = int(raw[0]) + KLINE_1D_INTERVAL_MS - 1 + close_time_shift_ms
    raw[1] = str(Decimal(str(raw[1])) + Decimal(offset))
    raw[2] = str(Decimal(str(raw[2])) + Decimal(offset))
    raw[3] = str(Decimal(str(raw[3])) + Decimal(offset))
    raw[4] = close_price
    raw[5] = volume
    return raw


def build_dto(offset: int = 0, *, close_price: str = "35500.40000000") -> MarketKlineDTO:
    return parse_binance_kline(
        build_raw(offset, close_price=close_price),
        symbol="BTCUSDT",
        interval_value=KLINE_1D_INTERVAL_VALUE,
        trigger_source=TRIGGER_SOURCE_CLI,
    )


def model_from_dto(dto: MarketKlineDTO) -> MarketKline1d:
    return MarketKline1d(
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


def request_for_offsets(
    start_offset: int,
    end_offset: int,
    *,
    notify_success: bool = False,
    dry_run: bool = False,
    confirm_write: bool = True,
    limit_per_request: int = 10,
) -> ManualKline1dBackfillRequest:
    start = build_dto(start_offset).open_time_ms
    end = build_dto(end_offset).open_time_ms
    return ManualKline1dBackfillRequest(
        symbol="BTCUSDT",
        interval_value=KLINE_1D_INTERVAL_VALUE,
        start_open_time_ms=start,
        end_open_time_ms=end,
        trigger_source=TRIGGER_SOURCE_CLI,
        dry_run=dry_run,
        confirm_write=confirm_write,
        notify_success=notify_success,
        limit_per_request=limit_per_request,
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


class FakeKline1dRepository:
    def __init__(self, existing: Iterable[MarketKline1d] = (), *, fail_on_bulk: bool = False) -> None:
        self.rows = {int(row.open_time_ms): row for row in existing}
        self.fail_on_bulk = fail_on_bulk
        self.bulk_write_called = False
        self.wrote_4h = False

    def get_latest(
        self,
        _db_session: Any,
        *,
        symbol: str,
    ) -> MarketKline1d | None:
        if not self.rows:
            return None
        return self.rows[max(self.rows)]

    def list_by_open_times(
        self,
        _db_session: Any,
        *,
        symbol: str,
        open_time_ms_list: Iterable[int],
    ) -> list[MarketKline1d]:
        return [self.rows[open_time_ms] for open_time_ms in open_time_ms_list if open_time_ms in self.rows]

    def list_by_time_range(
        self,
        _db_session: Any,
        *,
        symbol: str,
        start_open_time_ms: int,
        end_open_time_ms: int,
    ) -> list[MarketKline1d]:
        return [
            row
            for key, row in sorted(self.rows.items())
            if start_open_time_ms <= key <= end_open_time_ms
        ]

    def get_previous_before(
        self,
        _db_session: Any,
        *,
        symbol: str,
        open_time_ms: int,
    ) -> MarketKline1d | None:
        previous = [key for key in self.rows if key < open_time_ms]
        return self.rows[max(previous)] if previous else None

    def get_next_after(
        self,
        _db_session: Any,
        *,
        symbol: str,
        open_time_ms: int,
    ) -> MarketKline1d | None:
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
            if kline.interval_value != KLINE_1D_INTERVAL_VALUE:
                self.wrote_4h = True
            existing = self.rows.get(kline.open_time_ms)
            if existing is not None:
                if find_conflicting_1d_core_fields(existing, kline):
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


def run_1d_backfill_with_fakes(
    request: ManualKline1dBackfillRequest,
    raw_klines: list[list[Any]],
    *,
    existing: Iterable[MarketKline1d] = (),
    server_time_ms: int | None = None,
    repository: FakeKline1dRepository | None = None,
    task_lock: FakeTaskLock | None = None,
    alert_sender: FakeAlertSender | None = None,
    collector_repository: FakeCollectorEventRepository | None = None,
) -> tuple[Any, FakeKline1dRepository, FakeTaskLock, FakeAlertSender, FakeQualityRepository, FakeSession, FakeBinanceClient, FakeCollectorEventRepository]:
    dto = build_dto(0)
    fake_client = FakeBinanceClient(
        raw_klines,
        server_time_ms=server_time_ms if server_time_ms is not None else dto.close_time_ms + 100 * KLINE_1D_INTERVAL_MS,
    )
    fake_repository = repository or FakeKline1dRepository(existing)
    fake_task_lock = task_lock or FakeTaskLock()
    fake_alert_sender = alert_sender or FakeAlertSender()
    fake_quality_repository = FakeQualityRepository()
    fake_session = FakeSession()
    fake_collector_repository = collector_repository or FakeCollectorEventRepository()

    result = run_manual_1d_backfill(
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


def test_cli_rejects_non_1d_scheduler_trigger_misaligned_time_and_reversed_range() -> None:
    assert backfill_script.main(
        [
            "--interval",
            "4h",
            "--start-utc",
            "2023-11-15T00:00:00Z",
            "--end-utc",
            "2023-11-16T00:00:00Z",
            "--trigger-source",
            "cli",
            "--dry-run",
        ]
    ) == EXIT_PARAMETER_ERROR
    assert backfill_script.main(
        [
            "--start-utc",
            "2023-11-15T00:00:00Z",
            "--end-utc",
            "2023-11-16T00:00:00Z",
            "--trigger-source",
            "scheduler",
            "--dry-run",
        ]
    ) == EXIT_PARAMETER_ERROR
    assert backfill_script.main(
        [
            "--start-utc",
            "2023-11-15T01:00:00Z",
            "--end-utc",
            "2023-11-16T00:00:00Z",
            "--trigger-source",
            "cli",
            "--dry-run",
        ]
    ) == EXIT_PARAMETER_ERROR
    assert backfill_script.main(
        [
            "--start-utc",
            "2023-11-16T00:00:00Z",
            "--end-utc",
            "2023-11-15T00:00:00Z",
            "--trigger-source",
            "cli",
            "--dry-run",
        ]
    ) == EXIT_PARAMETER_ERROR


def test_single_day_inclusive_range_is_valid_and_builds_one_binance_request() -> None:
    request = request_for_offsets(0, 0)

    validate_1d_backfill_request(request)
    ranges = build_1d_binance_kline_request_ranges(request)

    assert request.requested_count == 1
    assert len(ranges) == 1
    assert ranges[0].limit == 1
    assert ranges[0].start_open_time_ms == ranges[0].end_open_time_ms
    assert ranges[0].end_time_ms_for_binance == request.start_open_time_ms + KLINE_1D_INTERVAL_MS - 1


def test_parameter_validation_requires_confirm_write_without_external_access() -> None:
    request = request_for_offsets(0, 1, confirm_write=False)

    result, repository, _lock, _alert, _quality_repo, _session, client, _collector = run_1d_backfill_with_fakes(
        request,
        [build_raw(0), build_raw(1)],
    )

    assert result.exit_code == EXIT_PARAMETER_ERROR
    assert client.get_server_time_calls == 0
    assert repository.bulk_write_called is False


def test_success_inserts_missing_1d_klines_only_into_1d_repository() -> None:
    result, repository, lock, alert_sender, _quality_repo, _session, client, collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1),
        [build_raw(0), build_raw(1)],
    )

    assert result.status == Kline1dBackfillStatus.SUCCESS
    assert result.exit_code == EXIT_SUCCESS
    assert result.inserted_count == 2
    assert result.skipped_existing_count == 0
    assert repository.bulk_write_called is True
    assert repository.wrote_4h is False
    assert all(isinstance(row, MarketKline1d) for row in repository.rows.values())
    assert not any(isinstance(row, MarketKline4h) for row in repository.rows.values())
    assert lock.acquire_calls[0]["key"] == "kline_write:BTCUSDT:1d"
    assert client.get_server_time_calls == 1
    assert len(client.get_klines_calls) == 1
    assert alert_sender.calls == []
    assert collector.records[0].kwargs["event_type"] == BACKFILL_1D_EVENT_TYPE
    assert collector.records[0].kwargs["interval_value"] == KLINE_1D_INTERVAL_VALUE
    assert collector.records[0].kwargs["trigger_source"] == TRIGGER_SOURCE_CLI
    assert collector.status_calls[-1]["values"]["inserted_count"] == 2


def test_idempotent_existing_1d_klines_are_skipped_without_duplicate_write() -> None:
    existing = [model_from_dto(build_dto(0)), model_from_dto(build_dto(1))]

    result, repository, _lock, _alert, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1),
        [build_raw(0), build_raw(1)],
        existing=existing,
    )

    assert result.status == Kline1dBackfillStatus.SUCCESS
    assert result.inserted_count == 0
    assert result.skipped_existing_count == 2
    assert repository.bulk_write_called is False
    assert len(repository.rows) == 2


def test_repeating_same_1d_backfill_does_not_create_duplicates() -> None:
    repository = FakeKline1dRepository()

    first, repository, *_ = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1),
        [build_raw(0), build_raw(1)],
        repository=repository,
    )
    second, repository, *_ = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1),
        [build_raw(0), build_raw(1)],
        repository=repository,
    )

    assert first.inserted_count == 2
    assert second.inserted_count == 0
    assert second.skipped_existing_count == 2
    assert len(repository.rows) == 2


def test_current_unclosed_daily_kline_is_filtered_as_notice_not_error() -> None:
    server_time_ms = build_dto(0).close_time_ms + 1

    result, repository, _lock, alert_sender, _quality_repo, _session, _client, collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1),
        [build_raw(0), build_raw(1)],
        server_time_ms=server_time_ms,
    )

    assert result.status == Kline1dBackfillStatus.SUCCESS
    assert result.exit_code == EXIT_SUCCESS
    assert result.inserted_count == 1
    assert result.filtered_unclosed_count == 1
    assert result.issue_count == 0
    assert build_dto(1).open_time_ms not in repository.rows
    assert alert_sender.calls == []
    assert collector.status_calls[-1]["values"]["filtered_unclosed_count"] == 1


def test_unexpected_middle_unclosed_kline_blocks_without_formal_write() -> None:
    server_time_ms = build_dto(0).close_time_ms

    result, repository, _lock, alert_sender, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 2),
        [build_raw(0), build_raw(1), build_raw(2)],
        server_time_ms=server_time_ms,
    )

    assert result.status == Kline1dBackfillStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.UNCLOSED_KLINE.value
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_binance_batch_gap_blocks_without_formal_write_and_alerts() -> None:
    result, repository, _lock, alert_sender, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 2),
        [build_raw(0), build_raw(2)],
    )

    assert result.status == Kline1dBackfillStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.BATCH_NOT_CONTINUOUS.value
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    message = format_alert_message(event)
    assert event.alert_type == AlertType.KLINE_DATA_QUALITY_ERROR
    assert event.severity.value == "error"
    assert "1d" in message
    assert "微信发送成功" not in message
    assert "微信已送达" not in message


def test_backfill_not_connected_to_previous_database_kline_blocks() -> None:
    existing = [model_from_dto(build_dto(0))]

    result, repository, _lock, alert_sender, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(2, 3),
        [build_raw(2), build_raw(3)],
        existing=existing,
    )

    assert result.status == Kline1dBackfillStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.DATABASE_NOT_CONTINUOUS.value
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_existing_conflicting_1d_kline_blocks_without_overwrite() -> None:
    dto1 = build_dto(1)
    conflicting = model_from_dto(replace(dto1, close_price=Decimal("1")))

    result, repository, _lock, alert_sender, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(1, 2),
        [build_raw(1), build_raw(2)],
        existing=[conflicting],
    )

    assert result.status == Kline1dBackfillStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.DATABASE_CONFLICT.value
    assert repository.bulk_write_called is False
    assert repository.rows[dto1.open_time_ms].close_price == Decimal("1")
    assert len(alert_sender.calls) == 1


def test_invalid_ohlc_volume_open_time_and_close_time_are_blocked() -> None:
    bad_cases = [
        [build_raw(0, close_price="0"), build_raw(1)],
        [build_raw(0, volume="-1"), build_raw(1)],
        [build_raw(0, open_time_shift_ms=1), build_raw(1)],
        [build_raw(0, close_time_shift_ms=1), build_raw(1)],
    ]

    for raw_klines in bad_cases:
        result, repository, *_ = run_1d_backfill_with_fakes(
            request_for_offsets(0, 1),
            raw_klines,
        )
        assert result.status == Kline1dBackfillStatus.BLOCKED
        assert repository.bulk_write_called is False


def test_formal_table_unclosed_1d_row_blocks_without_auto_repair() -> None:
    dto1 = build_dto(1)
    unclosed_existing = model_from_dto(dto1)
    server_time_ms = dto1.close_time_ms

    result, repository, _lock, alert_sender, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1),
        [build_raw(0), build_raw(1)],
        existing=[unclosed_existing],
        server_time_ms=server_time_ms,
    )

    assert result.status == Kline1dBackfillStatus.BLOCKED
    assert "未收盘 K线误写正式表" in (result.first_issue_message or "")
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_dry_run_does_not_write_formal_1d_table() -> None:
    result, repository, _lock, _alert, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1, dry_run=True, confirm_write=False),
        [build_raw(0), build_raw(1)],
    )

    assert result.status == Kline1dBackfillStatus.SUCCESS
    assert result.details["dry_run"] is True
    assert result.details["formal_write_performed"] is False
    assert repository.bulk_write_called is False
    assert repository.rows == {}


def test_dry_run_quality_blocked_does_not_submit_real_hermes() -> None:
    result, repository, _lock, alert_sender, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 2, dry_run=True, confirm_write=False),
        [build_raw(0), build_raw(2)],
    )

    assert result.status == Kline1dBackfillStatus.BLOCKED
    assert result.details["alert_skipped_reason"] == "dry_run"
    assert repository.bulk_write_called is False
    assert alert_sender.calls == []


def test_notify_success_sends_compact_1d_success_alert_without_delivery_claim() -> None:
    result, _repository, _lock, alert_sender, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1, notify_success=True),
        [build_raw(0), build_raw(1)],
    )

    assert result.status == Kline1dBackfillStatus.SUCCESS
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    message = format_alert_message(event)
    assert event.title == "手动 1d 日 K 回补完成"
    assert "BTCUSDT 1d" in message
    assert "追踪ID" in message
    assert "微信发送成功" not in message
    assert "微信已送达" not in message
    assert "delivered" not in message
    assert "weixin_success" not in message


def test_dry_run_success_alert_uses_manual_backfill_notice_type() -> None:
    result, _repository, _lock, alert_sender, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1, dry_run=True, confirm_write=False, notify_success=True),
        [build_raw(0), build_raw(1)],
    )

    assert result.status == Kline1dBackfillStatus.SUCCESS
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    assert event.alert_type == AlertType.MANUAL_BACKFILL_NOTICE
    assert event.alert_type != AlertType.KLINE_INTEGRITY_CHECK_PASSED
    assert event.severity.value == "info"


def test_real_write_success_alert_uses_manual_backfill_notice_type() -> None:
    result, _repository, _lock, alert_sender, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1, notify_success=True),
        [build_raw(0), build_raw(1)],
    )

    assert result.status == Kline1dBackfillStatus.SUCCESS
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    assert event.alert_type == AlertType.MANUAL_BACKFILL_NOTICE
    assert event.alert_type != AlertType.KLINE_INTEGRITY_CHECK_PASSED
    assert event.severity.value == "info"


def test_hermes_submission_failure_returns_alert_failed_exit_code() -> None:
    failed_alert = FakeAlertSender(
        AlertSendResult(status=AlertSendStatus.SUBMIT_FAILED, error_message="Hermes unavailable")
    )

    result, _repo, _lock, _alert, _quality_repo, _session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 2),
        [build_raw(0), build_raw(2)],
        alert_sender=failed_alert,
    )

    assert result.status == Kline1dBackfillStatus.BLOCKED
    assert result.exit_code == EXIT_ALERT_FAILED
    assert result.alert_status == AlertSendStatus.SUBMIT_FAILED.value


def test_bulk_upsert_exception_does_not_leave_partial_formal_1d_write() -> None:
    repository = FakeKline1dRepository(fail_on_bulk=True)

    result, repository, _lock, alert_sender, _quality_repo, session, _client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1),
        [build_raw(0), build_raw(1)],
        repository=repository,
    )

    assert result.status == Kline1dBackfillStatus.FAILED
    assert result.exit_code == EXIT_PERSIST_FAILED
    assert repository.rows == {}
    assert session.rollbacks >= 1
    assert session.nested_rollbacks >= 1
    assert len(alert_sender.calls) == 1


def test_task_lock_already_exists_skips_without_binance_or_formal_write() -> None:
    task_lock = FakeTaskLock(acquired=False)

    result, repository, _lock, alert_sender, _quality_repo, _session, client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1),
        [build_raw(0), build_raw(1)],
        task_lock=task_lock,
    )

    assert result.status == Kline1dBackfillStatus.SKIPPED
    assert result.exit_code == EXIT_QUALITY_BLOCKED
    assert client.get_server_time_calls == 0
    assert client.get_klines_calls == []
    assert repository.bulk_write_called is False
    assert alert_sender.calls == []


def test_redis_exception_fails_without_binance_or_formal_write_and_alerts() -> None:
    task_lock = FakeTaskLock(raise_on_acquire=True)

    result, repository, _lock, alert_sender, _quality_repo, _session, client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 1),
        [build_raw(0), build_raw(1)],
        task_lock=task_lock,
    )

    assert result.status == Kline1dBackfillStatus.FAILED
    assert result.exit_code == EXIT_TASK_FAILED
    assert client.get_server_time_calls == 0
    assert repository.bulk_write_called is False
    assert len(alert_sender.calls) == 1


def test_request_pagination_keeps_whole_range_continuity() -> None:
    result, _repository, _lock, _alert, _quality_repo, _session, client, _collector = run_1d_backfill_with_fakes(
        request_for_offsets(0, 4, limit_per_request=2),
        [build_raw(0), build_raw(1), build_raw(2), build_raw(3), build_raw(4)],
    )

    assert result.status == Kline1dBackfillStatus.SUCCESS
    assert len(client.get_klines_calls) == 3
    assert [call["limit"] for call in client.get_klines_calls] == [2, 2, 1]


def test_1d_backfill_sources_do_not_use_4h_repository_deepseek_trading_or_private_interfaces() -> None:
    from app.market_data.backfill import kline_1d_backfill_service, kline_1d_persistence, kline_1d_quality

    source = (
        inspect.getsource(kline_1d_backfill_service)
        + inspect.getsource(kline_1d_persistence)
        + inspect.getsource(kline_1d_quality)
        + inspect.getsource(backfill_script)
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
