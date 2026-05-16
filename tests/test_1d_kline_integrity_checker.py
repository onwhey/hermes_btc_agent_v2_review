from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Iterable

from app.alerting.service import format_alert_message
from app.market_data.kline_constants import KLINE_1D_INTERVAL_VALUE, TRIGGER_SOURCE_SCHEDULER
from app.market_data.kline_integrity.kline_1d_integrity_service import run_daily_1d_kline_integrity_check
from app.market_data.kline_integrity.kline_1d_integrity_types import (
    CHECK_TYPE_DAILY_KLINE_1D_INTEGRITY,
    DailyKline1dIntegrityCheckRequest,
    DailyKline1dIntegrityStatus,
    KLINE_1D_INTEGRITY_EVENT_TYPE,
)
from app.market_data.kline_quality.types import KlineQualityIssueType
from app.core.time_utils import timestamp_ms_to_utc_datetime
from tests.test_1d_kline_manual_backfill import (
    FakeAlertSender,
    FakeQualityRepository,
    FakeSession,
    FakeTaskLock,
    build_dto,
    model_from_dto,
)


class FakeIntegrityKline1dRepository:
    def __init__(self, rows: Iterable[Any]) -> None:
        self.rows = list(rows)
        self.bulk_write_called = False

    def list_recent(self, _db_session: Any, *, symbol: str, limit: int, ascending: bool = True) -> list[Any]:
        rows = sorted(self.rows, key=lambda row: int(row.open_time_ms), reverse=not ascending)
        return rows[:limit] if not ascending else rows[-limit:]

    def bulk_upsert(self, *_args: Any, **_kwargs: Any) -> None:
        self.bulk_write_called = True
        raise AssertionError("1d integrity check must not write formal Klines")


class FakeIntegrityCollectorEventRepository:
    def __init__(self) -> None:
        self.records: list[Any] = []
        self.status_calls: list[dict[str, Any]] = []

    def create_running_event(self, _db_session: Any, **kwargs: Any) -> Any:
        record = SimpleNamespace(id=len(self.records) + 1, kwargs=kwargs, status="running")
        self.records.append(record)
        return record

    def create_skipped_event(self, _db_session: Any, **kwargs: Any) -> Any:
        record = self.create_running_event(_db_session, **kwargs)
        record.status = "skipped"
        self.status_calls.append({"status": "skipped", "values": kwargs})
        return record

    def mark_event_status(self, _db_session: Any, event: Any, **values: Any) -> Any:
        event.status = values["status"]
        self.status_calls.append({"status": values["status"], "values": values})
        return event


def integrity_request(*, notify_success: bool = True) -> DailyKline1dIntegrityCheckRequest:
    return DailyKline1dIntegrityCheckRequest(
        symbol="BTCUSDT",
        interval_value=KLINE_1D_INTERVAL_VALUE,
        lookback_count=500,
        check_trigger=TRIGGER_SOURCE_SCHEDULER,
        notify_success=notify_success,
    )


def current_time_during_day(offset: int) -> Any:
    return timestamp_ms_to_utc_datetime(build_dto(offset).open_time_ms + 12 * 60 * 60 * 1000)


def run_integrity_with_fakes(
    rows: Iterable[Any],
    *,
    request: DailyKline1dIntegrityCheckRequest | None = None,
    current_offset: int = 3,
    task_lock: FakeTaskLock | None = None,
) -> tuple[Any, FakeIntegrityKline1dRepository, FakeIntegrityCollectorEventRepository, FakeQualityRepository, FakeAlertSender, FakeSession]:
    fake_repository = FakeIntegrityKline1dRepository(rows)
    fake_collector = FakeIntegrityCollectorEventRepository()
    fake_quality = FakeQualityRepository()
    fake_alert = FakeAlertSender()
    fake_session = FakeSession()
    result = run_daily_1d_kline_integrity_check(
        request or integrity_request(),
        db_session=fake_session,
        kline_repository=fake_repository,
        data_quality_repository=fake_quality,
        collector_event_repository=fake_collector,
        alert_sender=fake_alert,
        alert_repository=object(),
        task_lock=task_lock or FakeTaskLock(),
        current_time_utc=current_time_during_day(current_offset),
    )
    return result, fake_repository, fake_collector, fake_quality, fake_alert, fake_session


def test_1d_integrity_healthy_continuous_rows_pass_and_send_compact_summary() -> None:
    rows = [model_from_dto(build_dto(offset)) for offset in range(3)]

    result, repository, collector, quality_repo, alert_sender, _session = run_integrity_with_fakes(rows)

    assert result.status == DailyKline1dIntegrityStatus.HEALTHY
    assert result.exit_code == 0
    assert result.checked_count == 3
    assert result.issue_count == 0
    assert repository.bulk_write_called is False
    assert collector.records[0].kwargs["event_type"] == KLINE_1D_INTEGRITY_EVENT_TYPE
    assert collector.records[0].kwargs["interval_value"] == KLINE_1D_INTERVAL_VALUE
    assert collector.records[0].kwargs["trigger_source"] == TRIGGER_SOURCE_SCHEDULER
    assert collector.status_calls[-1]["status"] == "success"
    assert quality_repo.records[0].report.check_type == CHECK_TYPE_DAILY_KLINE_1D_INTEGRITY
    assert len(alert_sender.calls) == 1
    message = format_alert_message(alert_sender.calls[0]["event"])
    assert "1d" in message
    assert "market_kline_4h" not in message
    assert "微信发送成功" not in message
    assert "微信已送达" not in message
    assert "delivered" not in message
    assert "weixin_success" not in message


def test_1d_integrity_empty_table_is_blocked_without_auto_initialization() -> None:
    result, repository, collector, quality_repo, alert_sender, _session = run_integrity_with_fakes([])

    assert result.status == DailyKline1dIntegrityStatus.BLOCKED
    assert result.first_issue_type == KlineQualityIssueType.EMPTY_BATCH.value
    assert repository.bulk_write_called is False
    assert collector.status_calls[-1]["status"] == "blocked"
    assert quality_repo.records[0].report.checked_count == 0
    assert len(alert_sender.calls) == 1


def test_1d_integrity_gap_duplicate_future_unclosed_and_invalid_fields_fail() -> None:
    gap_rows = [model_from_dto(build_dto(0)), model_from_dto(build_dto(2))]
    duplicate_rows = [model_from_dto(build_dto(0)), model_from_dto(build_dto(0)), model_from_dto(build_dto(1))]
    future_rows = [model_from_dto(build_dto(0)), model_from_dto(build_dto(1)), model_from_dto(build_dto(2))]
    invalid_row = model_from_dto(replace(build_dto(1), close_price=Decimal("0")))
    cases = [
        (gap_rows, 3, KlineQualityIssueType.BATCH_NOT_CONTINUOUS.value),
        (duplicate_rows, 2, KlineQualityIssueType.DUPLICATE_OPEN_TIME.value),
        (future_rows, 2, KlineQualityIssueType.UNCLOSED_KLINE.value),
        ([model_from_dto(build_dto(0)), invalid_row], 2, KlineQualityIssueType.INVALID_KLINE.value),
    ]

    for rows, current_offset, issue_type in cases:
        result, repository, _collector, _quality, alert_sender, _session = run_integrity_with_fakes(
            rows,
            current_offset=current_offset,
        )
        assert result.status == DailyKline1dIntegrityStatus.FAILED
        assert result.first_issue_type == issue_type
        assert repository.bulk_write_called is False
        assert len(alert_sender.calls) == 1


def test_1d_integrity_one_day_stale_is_warning_not_healthy() -> None:
    rows = [model_from_dto(build_dto(0))]

    result, repository, collector, _quality, alert_sender, _session = run_integrity_with_fakes(
        rows,
        current_offset=2,
    )

    assert result.status == DailyKline1dIntegrityStatus.WARNING
    assert result.first_issue_type == KlineQualityIssueType.MISSING_IN_DATABASE.value
    assert repository.bulk_write_called is False
    assert collector.status_calls[-1]["status"] == "warning"
    assert len(alert_sender.calls) == 1


def test_1d_integrity_lock_already_exists_skips_without_alert_or_formal_write() -> None:
    lock = FakeTaskLock(acquired=False)

    result, repository, collector, _quality, alert_sender, _session = run_integrity_with_fakes(
        [model_from_dto(build_dto(0))],
        task_lock=lock,
    )

    assert result.status == DailyKline1dIntegrityStatus.SKIPPED
    assert result.exit_code == 0
    assert repository.bulk_write_called is False
    assert collector.status_calls[-1]["status"] == "skipped"
    assert alert_sender.calls == []
