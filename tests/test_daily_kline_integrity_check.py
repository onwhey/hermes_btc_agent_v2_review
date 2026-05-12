from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import app.storage.mysql.session as mysql_session
from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.config import AppSettings
from app.market_data.kline_constants import (
    DATA_SOURCE_BINANCE_REST_BY_CLI,
    KLINE_4H_INTERVAL_MS,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)
from app.market_data.kline_integrity.kline_integrity_service import run_daily_kline_integrity_check
from app.market_data.kline_integrity.types import (
    EXIT_ALERT_FAILED,
    EXIT_QUALITY_FAILED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    DailyKlineIntegrityCheckRequest,
    DailyKlineIntegrityStatus,
)
from app.market_data.kline_quality.types import (
    CHECK_TRIGGER_SOURCE_CLI,
    CHECK_TRIGGER_SOURCE_SCHEDULER,
    CHECK_TYPE_DAILY_KLINE_INTEGRITY,
    KlineQualityIssueType,
)
from app.scheduler.jobs.daily_kline_integrity_check import run_daily_kline_integrity_check_job
from scripts import run_daily_kline_integrity_check as daily_script
from tests.test_4h_kline_manual_backfill import (
    FakeAlertSender,
    FakeSession,
    build_dto,
    build_raw,
    model_from_dto,
)


class FakeDailyBinanceClient:
    def __init__(self, raw_klines: list[list[Any]], *, server_time_ms: int, fail_on: str = "") -> None:
        self.raw_klines = raw_klines
        self.server_time_ms = server_time_ms
        self.fail_on = fail_on
        self.get_server_time_calls = 0
        self.get_klines_calls: list[dict[str, Any]] = []

    def get_server_time(self) -> dict[str, int]:
        self.get_server_time_calls += 1
        if self.fail_on == "server_time":
            raise RuntimeError("Binance server time unavailable")
        return {"serverTime": self.server_time_ms}

    def get_klines(self, **kwargs: Any) -> list[list[Any]]:
        self.get_klines_calls.append(kwargs)
        if self.fail_on == "klines":
            raise RuntimeError("Binance Kline request unavailable")
        limit = kwargs.get("limit") or len(self.raw_klines)
        return self.raw_klines[:limit]


class FakeDailyKlineRepository:
    def __init__(self, existing: Iterable[Any] = (), *, fail_on_read: bool = False) -> None:
        self.rows = list(existing)
        self.fail_on_read = fail_on_read
        self.bulk_write_called = False
        self.delete_called = False

    def list_by_time_range(
        self,
        _db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        start_open_time_ms: int,
        end_open_time_ms: int,
    ) -> list[Any]:
        if self.fail_on_read:
            raise RuntimeError("market_kline_4h read failed")
        return [
            row
            for row in sorted(self.rows, key=lambda item: int(item.open_time_ms))
            if start_open_time_ms <= int(row.open_time_ms) <= end_open_time_ms
        ]

    def bulk_upsert(self, *_args: Any, **_kwargs: Any) -> None:
        self.bulk_write_called = True

    def delete(self, *_args: Any, **_kwargs: Any) -> None:
        self.delete_called = True


class FakeQualityRepository:
    def __init__(self, *, fail_on_create: bool = False) -> None:
        self.fail_on_create = fail_on_create
        self.records: list[Any] = []
        self.mark_alert_sent_calls = 0

    def create_quality_check_record(self, _db_session: Any, report: Any) -> Any:
        if self.fail_on_create:
            raise RuntimeError("data_quality_check write failed")
        record = SimpleNamespace(id=len(self.records) + 1, report=report, alert_sent=False)
        self.records.append(record)
        return record

    def mark_quality_check_alert_sent(self, _db_session: Any, record: Any, **_kwargs: Any) -> Any:
        self.mark_alert_sent_calls += 1
        record.alert_sent = True
        return record


def make_raw_klines(count: int) -> list[list[Any]]:
    return [build_raw(offset) for offset in range(count)]


def official_model(offset: int, *, close_price: str = "35500.40000000") -> Any:
    dto = build_dto(offset, close_price=close_price)
    row = model_from_dto(dto)
    row.data_source = DATA_SOURCE_BINANCE_REST_BY_CLI
    row.trigger_source = TRIGGER_SOURCE_CLI
    return row


def extra_model_inside_range() -> Any:
    row = official_model(0)
    row.open_time_ms = build_dto(0).open_time_ms + (KLINE_4H_INTERVAL_MS // 2)
    row.close_time_ms = row.open_time_ms + KLINE_4H_INTERVAL_MS - 1
    return row


def run_daily_with_fakes(
    *,
    raw_klines: list[list[Any]],
    existing: Iterable[Any],
    request: DailyKlineIntegrityCheckRequest | None = None,
    server_time_ms: int | None = None,
    kline_repository: FakeDailyKlineRepository | None = None,
    quality_repository: FakeQualityRepository | None = None,
    alert_sender: FakeAlertSender | None = None,
    fail_binance_on: str = "",
) -> tuple[Any, FakeDailyKlineRepository, FakeQualityRepository, FakeAlertSender, FakeSession, FakeDailyBinanceClient]:
    active_request = request or DailyKlineIntegrityCheckRequest()
    last_offset = max(0, len(raw_klines) - 1)
    fake_client = FakeDailyBinanceClient(
        raw_klines,
        server_time_ms=server_time_ms
        if server_time_ms is not None
        else build_dto(last_offset).close_time_ms,
        fail_on=fail_binance_on,
    )
    fake_repository = kline_repository or FakeDailyKlineRepository(existing)
    fake_quality_repository = quality_repository or FakeQualityRepository()
    fake_alert_sender = alert_sender or FakeAlertSender()
    fake_session = FakeSession()

    result = run_daily_kline_integrity_check(
        active_request,
        db_session=fake_session,
        binance_client=fake_client,
        kline_repository=fake_repository,
        data_quality_repository=fake_quality_repository,
        alert_sender=fake_alert_sender,
        alert_repository=object(),
    )
    return result, fake_repository, fake_quality_repository, fake_alert_sender, fake_session, fake_client


def test_daily_recent_100_pass_records_quality_and_sends_success_alert_without_formal_write() -> None:
    result, repository, quality_repository, alert_sender, _session, client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
    )

    assert result.status == DailyKlineIntegrityStatus.HEALTHY
    assert result.exit_code == EXIT_SUCCESS
    assert result.checked_count == 100
    assert result.quality_check_id == 1
    assert client.get_klines_calls[0]["limit"] == 101
    assert quality_repository.records[0].report.check_type == CHECK_TYPE_DAILY_KLINE_INTEGRITY
    assert quality_repository.records[0].report.status.value == "passed"
    assert len(alert_sender.calls) == 1
    assert alert_sender.calls[0]["event"].details["source"] == "Binance REST official klines"
    assert repository.bulk_write_called is False
    assert repository.delete_called is False


def test_daily_missing_database_kline_fails_alerts_and_does_not_backfill() -> None:
    existing = [official_model(offset) for offset in range(100) if offset != 5]

    result, repository, quality_repository, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=existing,
    )

    assert result.status == DailyKlineIntegrityStatus.FAILED
    assert result.exit_code == EXIT_QUALITY_FAILED
    assert result.first_issue_type == KlineQualityIssueType.MISSING_IN_DATABASE.value
    assert quality_repository.records[0].report.status.value == "failed"
    assert len(alert_sender.calls) == 1
    assert repository.bulk_write_called is False


def test_daily_database_field_mismatch_fails_alerts_and_does_not_overwrite() -> None:
    existing = [official_model(offset) for offset in range(100)]
    existing[7] = official_model(7, close_price="1.00000000")

    result, repository, _quality, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=existing,
    )

    assert result.status == DailyKlineIntegrityStatus.FAILED
    assert result.first_issue_type == KlineQualityIssueType.DATABASE_FIELD_MISMATCH.value
    assert len(alert_sender.calls) == 1
    assert repository.bulk_write_called is False


def test_daily_database_extra_kline_fails_alerts_and_does_not_delete() -> None:
    existing = [official_model(offset) for offset in range(100)]
    existing.append(extra_model_inside_range())

    result, repository, _quality, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=existing,
    )

    assert result.status == DailyKlineIntegrityStatus.FAILED
    assert result.first_issue_type == KlineQualityIssueType.EXTRA_IN_DATABASE.value
    assert len(alert_sender.calls) == 1
    assert repository.delete_called is False


def test_daily_binance_rest_failure_alerts_task_error_without_formal_write() -> None:
    result, repository, quality_repository, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
        fail_binance_on="klines",
    )

    assert result.status == DailyKlineIntegrityStatus.ERROR
    assert result.exit_code == EXIT_TASK_FAILED
    assert result.first_issue_type == KlineQualityIssueType.TASK_ERROR.value
    assert quality_repository.records == []
    assert len(alert_sender.calls) == 1
    assert repository.bulk_write_called is False


def test_daily_database_read_failure_records_error_when_possible_and_alerts() -> None:
    result, _repository, quality_repository, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[],
        kline_repository=FakeDailyKlineRepository(fail_on_read=True),
    )

    assert result.status == DailyKlineIntegrityStatus.ERROR
    assert result.exit_code == EXIT_TASK_FAILED
    assert quality_repository.records[0].report.status.value == "error"
    assert len(alert_sender.calls) == 1


def test_daily_data_quality_check_write_failure_is_not_silent_and_alerts() -> None:
    result, repository, quality_repository, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
        quality_repository=FakeQualityRepository(fail_on_create=True),
    )

    assert result.status == DailyKlineIntegrityStatus.ERROR
    assert result.exit_code == EXIT_TASK_FAILED
    assert result.details["data_quality_check_record_failed"] is True
    assert quality_repository.records == []
    assert len(alert_sender.calls) == 1
    assert repository.bulk_write_called is False


def test_daily_success_hermes_failure_keeps_check_fact_but_returns_alert_failed() -> None:
    failed_alert = FakeAlertSender(
        AlertSendResult(status=AlertSendStatus.FAILED, error_message="Hermes unavailable")
    )

    result, _repository, quality_repository, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
        alert_sender=failed_alert,
    )

    assert result.status == DailyKlineIntegrityStatus.HEALTHY
    assert result.exit_code == EXIT_ALERT_FAILED
    assert result.alert_status == AlertSendStatus.FAILED.value
    assert quality_repository.records[0].report.status.value == "passed"
    assert len(alert_sender.calls) == 1


def test_daily_notify_success_false_skips_success_alert_but_failure_still_alerts() -> None:
    success_request = DailyKlineIntegrityCheckRequest(notify_success=False)
    result, _repo, _quality, alert_sender, _session, _client = run_daily_with_fakes(
        request=success_request,
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
    )

    assert result.status == DailyKlineIntegrityStatus.HEALTHY
    assert alert_sender.calls == []

    failed_request = DailyKlineIntegrityCheckRequest(notify_success=False)
    result, _repo, _quality, alert_sender, _session, _client = run_daily_with_fakes(
        request=failed_request,
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(99)],
    )

    assert result.status == DailyKlineIntegrityStatus.FAILED
    assert len(alert_sender.calls) == 1


def test_daily_scheduler_job_calls_service_directly_with_scheduler_trigger_source() -> None:
    called: dict[str, Any] = {}
    expected_result = SimpleNamespace(
        status=DailyKlineIntegrityStatus.HEALTHY,
        exit_code=EXIT_SUCCESS,
        trace_id="trace",
        message="ok",
    )

    def fake_runner(request: DailyKlineIntegrityCheckRequest, **kwargs: Any) -> Any:
        called["request"] = request
        called["kwargs"] = kwargs
        return expected_result

    db_session = object()
    result = run_daily_kline_integrity_check_job(
        db_session=db_session,
        settings=AppSettings(),
        service_runner=fake_runner,
    )

    assert result is expected_result
    assert called["request"].check_trigger_source == CHECK_TRIGGER_SOURCE_SCHEDULER
    assert called["kwargs"]["db_session"] is db_session


def test_daily_cli_allows_only_cli_trigger_and_does_not_restore_send_alert(monkeypatch: Any) -> None:
    source = Path("scripts/run_daily_kline_integrity_check.py").read_text(encoding="utf-8")
    legacy_alert_flag = "--send" "-alert"

    assert legacy_alert_flag not in source
    assert daily_script.main([legacy_alert_flag]) != EXIT_SUCCESS
    assert daily_script.main(["--trigger-source", "scheduler"]) != EXIT_SUCCESS

    called: dict[str, Any] = {}
    @contextmanager
    def fake_session_scope(*, commit_on_success: bool = False):
        called["commit_on_success"] = commit_on_success
        yield object()

    def fake_service(request: DailyKlineIntegrityCheckRequest, **kwargs: Any) -> Any:
        called["request"] = request
        called["kwargs"] = kwargs
        return SimpleNamespace(
            status=DailyKlineIntegrityStatus.HEALTHY,
            exit_code=EXIT_SUCCESS,
            trace_id=request.trace_id,
            message="ok",
            requested_count=request.requested_count,
            checked_count=1,
            issue_count=0,
            alert_status=None,
            quality_check_id=1,
            checked_start_time=None,
            checked_end_time=None,
            first_issue_type=None,
            first_issue_message=None,
        )

    monkeypatch.setattr(mysql_session, "session_scope", fake_session_scope)
    monkeypatch.setattr(daily_script, "run_daily_kline_integrity_check", fake_service)

    exit_code = daily_script.main(["--trigger-source", "cli", "--limit", "1", "--no-notify-success"])

    assert exit_code == EXIT_SUCCESS
    assert called["commit_on_success"] is True
    assert called["request"].check_trigger_source == CHECK_TRIGGER_SOURCE_CLI
    assert called["request"].notify_success is False


def test_daily_review_sources_do_not_use_forbidden_private_or_repair_capabilities() -> None:
    source = (
        Path("app/market_data/kline_integrity/kline_integrity_service.py").read_text(encoding="utf-8")
        + Path("scripts/run_daily_kline_integrity_check.py").read_text(encoding="utf-8")
    )
    forbidden_terms = [
        "--send" "-alert",
        "get_" "account",
        "get_" "position",
        "create_" "order",
        "listen" "Key",
        "/fapi/v1/" "ticker",
        "manual_" "repair",
        "human_" "edit",
        "manual_" "input",
        "system_" "repair",
    ]

    for term in forbidden_terms:
        assert term not in source
