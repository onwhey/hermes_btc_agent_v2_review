from __future__ import annotations

import inspect
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import app.storage.mysql.session as mysql_session
from app.alerting.types import AlertSendResult, AlertSendStatus
from app.market_data.kline_constants import KLINE_4H_INTERVAL_MS, KLINE_4H_INTERVAL_VALUE, TRIGGER_SOURCE_CLI
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_parser import parse_binance_kline
from app.market_data.kline_quality import batch_checker, db_checker, integrity_checker, service
from app.market_data.kline_quality.batch_checker import check_kline_batch_before_persist
from app.market_data.kline_quality.service import check_against_database
from app.market_data.kline_quality.types import (
    CHECK_TRIGGER_SOURCE_CLI,
    CHECK_TRIGGER_SOURCE_SERVICE,
    CHECK_TYPE_RECENT_KLINE_INTEGRITY,
    KlineQualityIssue,
    KlineQualityIssueType,
    KlineQualitySeverity,
    build_quality_report,
)
from app.storage.mysql.models.market_kline_4h import MarketKline4h
from app.storage.mysql.repositories.data_quality_check_repository import DataQualityCheckRepository
from scripts import check_kline_quality_4h as quality_script
from scripts.check_kline_quality_4h import collect_kline_quality_4h_errors


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
    raw = build_raw(offset, close_price=close_price)
    return parse_binance_kline(
        raw,
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


class FakeKlineRepository:
    def __init__(
        self,
        *,
        latest: Any | None = None,
        existing: Iterable[Any] = (),
    ) -> None:
        self.latest = latest
        self.existing_by_open_time = {int(row.open_time_ms): row for row in existing}
        self.formal_write_called = False

    def get_latest(self, _db_session: Any, *, symbol: str, interval_value: str) -> Any | None:
        return self.latest

    def list_by_open_times(
        self,
        _db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        open_time_ms_list: Iterable[int],
    ) -> list[Any]:
        return [
            self.existing_by_open_time[open_time_ms]
            for open_time_ms in open_time_ms_list
            if open_time_ms in self.existing_by_open_time
        ]

    def list_by_time_range(
        self,
        _db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        start_open_time_ms: int,
        end_open_time_ms: int,
    ) -> list[Any]:
        return [
            row
            for open_time_ms, row in sorted(self.existing_by_open_time.items())
            if start_open_time_ms <= open_time_ms <= end_open_time_ms
        ]

    def bulk_upsert(self, *_args: Any, **_kwargs: Any) -> None:
        self.formal_write_called = True


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushed = False

    def add(self, record: Any) -> None:
        self.added.append(record)

    def flush(self) -> None:
        self.flushed = True


class FakeBinanceClient:
    def __init__(self, raw_klines: list[list[Any]], *, server_time_ms: int) -> None:
        self.raw_klines = raw_klines
        self.server_time_ms = server_time_ms
        self.requested_limits: list[int | None] = []

    def get_server_time(self) -> dict[str, int]:
        return {"serverTime": self.server_time_ms}

    def get_klines(
        self,
        *,
        symbol: str | None = None,
        interval: str | None = None,
        limit: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[list[Any]]:
        self.requested_limits.append(limit)
        return self.raw_klines[: limit or len(self.raw_klines)]


def issue_types(report: Any) -> set[KlineQualityIssueType]:
    return {issue.issue_type for issue in report.issues}


def test_batch_continuous_klines_pass_quality_check() -> None:
    klines = [build_dto(0), build_dto(1), build_dto(2)]

    report = check_kline_batch_before_persist(
        klines,
        server_time_ms=klines[-1].close_time_ms + 1,
    )

    assert report.passed is True
    assert report.writable_klines == tuple(klines)


def test_batch_gap_is_rejected() -> None:
    klines = [build_dto(0), build_dto(2)]

    report = check_kline_batch_before_persist(
        klines,
        server_time_ms=klines[-1].close_time_ms + 1,
    )

    assert report.passed is False
    assert KlineQualityIssueType.BATCH_NOT_CONTINUOUS in issue_types(report)
    assert report.writable_klines == ()


def test_batch_duplicate_open_time_is_rejected() -> None:
    dto = build_dto(0)

    report = check_kline_batch_before_persist(
        [dto, dto],
        server_time_ms=dto.close_time_ms + 1,
    )

    assert report.passed is False
    assert KlineQualityIssueType.DUPLICATE_OPEN_TIME in issue_types(report)


def test_batch_must_be_ascending_by_open_time_ms() -> None:
    klines = [build_dto(1), build_dto(0)]

    report = check_kline_batch_before_persist(
        klines,
        server_time_ms=klines[0].close_time_ms + 1,
    )

    assert report.passed is False
    assert KlineQualityIssueType.BATCH_NOT_SORTED in issue_types(report)


def test_batch_symbol_mismatch_is_rejected() -> None:
    klines = [build_dto(0), replace(build_dto(1), symbol="ETHUSDT")]

    report = check_kline_batch_before_persist(
        klines,
        server_time_ms=klines[-1].close_time_ms + 1,
    )

    assert report.passed is False
    assert KlineQualityIssueType.BATCH_SYMBOL_MISMATCH in issue_types(report)
    assert "one symbol" in report.issues[0].message


def test_batch_interval_mismatch_is_rejected() -> None:
    klines = [build_dto(0), replace(build_dto(1), interval_value="1h")]

    report = check_kline_batch_before_persist(
        klines,
        server_time_ms=klines[-1].close_time_ms + 1,
    )

    assert report.passed is False
    assert KlineQualityIssueType.BATCH_INTERVAL_MISMATCH in issue_types(report)
    assert any("one interval_value" in issue.message for issue in report.issues)


def test_unclosed_kline_is_rejected_by_server_time() -> None:
    dto = build_dto(0)

    report = check_kline_batch_before_persist(
        [dto],
        server_time_ms=dto.close_time_ms,
    )

    assert report.passed is False
    assert KlineQualityIssueType.UNCLOSED_KLINE in issue_types(report)


def test_open_time_ms_discontinuity_is_rejected() -> None:
    klines = [build_dto(0), build_dto(3)]

    report = check_kline_batch_before_persist(
        klines,
        server_time_ms=klines[-1].close_time_ms + 1,
    )

    assert report.passed is False
    assert KlineQualityIssueType.BATCH_NOT_CONTINUOUS in issue_types(report)


def test_database_latest_continuous_with_first_new_kline() -> None:
    latest = model_from_dto(build_dto(0))
    incoming = [build_dto(1)]
    repository = FakeKlineRepository(latest=latest)

    report = check_against_database(
        FakeSession(),
        incoming,
        server_time_ms=incoming[-1].close_time_ms + 1,
        repository=repository,
    )

    assert report.passed is True
    assert report.writable_klines == tuple(incoming)


def test_database_latest_gap_is_rejected() -> None:
    latest = model_from_dto(build_dto(0))
    incoming = [build_dto(2)]
    repository = FakeKlineRepository(latest=latest)

    report = check_against_database(
        FakeSession(),
        incoming,
        server_time_ms=incoming[-1].close_time_ms + 1,
        repository=repository,
    )

    assert report.passed is False
    assert KlineQualityIssueType.DATABASE_NOT_CONTINUOUS in issue_types(report)
    assert report.writable_klines == ()


def test_existing_identical_kline_is_context_and_not_rewritten() -> None:
    dto0 = build_dto(0)
    dto1 = build_dto(1)
    existing0 = model_from_dto(dto0)
    repository = FakeKlineRepository(latest=existing0, existing=[existing0])

    report = check_against_database(
        FakeSession(),
        [dto0, dto1],
        server_time_ms=dto1.close_time_ms + 1,
        repository=repository,
    )

    assert report.passed is True
    assert report.existing_open_time_ms == (dto0.open_time_ms,)
    assert report.writable_klines == (dto1,)


def test_existing_conflicting_kline_blocks_quality_check() -> None:
    dto = build_dto(0)
    conflicting = model_from_dto(replace(dto, close_price=Decimal("1")))
    repository = FakeKlineRepository(existing=[conflicting])

    report = check_against_database(
        FakeSession(),
        [dto],
        server_time_ms=dto.close_time_ms + 1,
        repository=repository,
    )

    assert report.passed is False
    assert KlineQualityIssueType.DATABASE_CONFLICT in issue_types(report)
    assert report.writable_klines == ()


def test_quality_failure_does_not_call_formal_kline_write() -> None:
    klines = [build_dto(0), build_dto(2)]
    repository = FakeKlineRepository()

    report = check_against_database(
        FakeSession(),
        klines,
        server_time_ms=klines[-1].close_time_ms + 1,
        repository=repository,
    )

    assert report.passed is False
    assert repository.formal_write_called is False


def test_quality_modules_do_not_mutate_formal_kline_data() -> None:
    sources = "\n".join(
        inspect.getsource(module)
        for module in (batch_checker, db_checker, integrity_checker, service)
    )

    assert ".bulk_upsert(" not in sources
    assert ".delete(" not in sources
    assert "run_manual_4h_backfill" not in sources
    assert "run_scheduled_incremental_collection" not in sources


def test_data_quality_check_repository_records_report_without_external_services() -> None:
    klines = [build_dto(0)]
    report = check_kline_batch_before_persist(
        klines,
        server_time_ms=klines[-1].close_time_ms + 1,
        check_trigger_source=CHECK_TRIGGER_SOURCE_SERVICE,
    )
    session = FakeSession()

    record = DataQualityCheckRepository().create_quality_check_record(session, report)

    assert record.status == "passed"
    assert record.issue_count == 0
    assert '"status": "passed"' in record.report_json
    assert session.added == [record]
    assert session.flushed is True


def test_recent_integrity_check_uses_fake_client_and_repository_by_default_in_tests() -> None:
    dto0 = build_dto(0)
    dto1 = build_dto(1)
    fake_client = FakeBinanceClient(
        [build_raw(0), build_raw(1)],
        server_time_ms=dto1.close_time_ms + 1,
    )
    fake_repository = FakeKlineRepository(existing=[model_from_dto(dto0), model_from_dto(dto1)])

    report = service.run_recent_kline_integrity_check(
        FakeSession(),
        symbol="BTCUSDT",
        interval_value=KLINE_4H_INTERVAL_VALUE,
        limit=2,
        check_trigger_source=CHECK_TRIGGER_SOURCE_SERVICE,
        binance_client=fake_client,
        kline_repository=fake_repository,
        record_result=False,
    )

    assert report.passed is True
    assert report.checked_count == 2
    assert fake_client.requested_limits == [3]


def test_recent_integrity_filters_unclosed_last_kline_before_checking_database() -> None:
    dto0 = build_dto(0)
    dto1 = build_dto(1)
    dto2 = build_dto(2)
    fake_client = FakeBinanceClient(
        [build_raw(0), build_raw(1), build_raw(2)],
        server_time_ms=dto2.close_time_ms,
    )
    fake_repository = FakeKlineRepository(existing=[model_from_dto(dto0), model_from_dto(dto1)])

    report = service.run_recent_kline_integrity_check(
        FakeSession(),
        symbol="BTCUSDT",
        interval_value=KLINE_4H_INTERVAL_VALUE,
        limit=2,
        check_trigger_source=CHECK_TRIGGER_SOURCE_SERVICE,
        binance_client=fake_client,
        kline_repository=fake_repository,
        record_result=False,
    )

    assert report.passed is True
    assert report.checked_count == 2
    assert KlineQualityIssueType.UNCLOSED_KLINE not in issue_types(report)
    assert report.metadata["filtered_unclosed_count"] == 1


def test_recent_integrity_reports_not_enough_closed_klines_after_filtering() -> None:
    dto2 = build_dto(2)
    fake_client = FakeBinanceClient(
        [build_raw(0), build_raw(1), build_raw(2)],
        server_time_ms=dto2.close_time_ms,
    )

    report = service.run_recent_kline_integrity_check(
        FakeSession(),
        symbol="BTCUSDT",
        interval_value=KLINE_4H_INTERVAL_VALUE,
        limit=3,
        check_trigger_source=CHECK_TRIGGER_SOURCE_SERVICE,
        binance_client=fake_client,
        kline_repository=FakeKlineRepository(),
        record_result=False,
    )

    assert report.passed is False
    assert KlineQualityIssueType.INSUFFICIENT_CLOSED_KLINES in issue_types(report)
    assert KlineQualityIssueType.UNCLOSED_KLINE not in issue_types(report)
    assert report.metadata["closed_count"] == 2


def test_data_quality_migration_only_creates_quality_table() -> None:
    text = Path("migrations/versions/20260511_07_create_data_quality_check.py").read_text(
        encoding="utf-8"
    )

    assert '"data_quality_check"' in text
    assert '"market_kline_4h"' not in text
    assert '"collector_event_log"' not in text
    assert '"strategy"' not in text
    assert '"trade_advice"' not in text


def test_check_kline_quality_script_has_pure_local_smoke_check() -> None:
    assert collect_kline_quality_4h_errors() == []


def test_check_kline_quality_script_default_main_is_safe_local_only(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    def fail_real_check(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("real check must not run by default")

    monkeypatch.setattr(quality_script, "run_recent_kline_integrity_check", fail_real_check)

    exit_code = quality_script.main([])

    assert exit_code == 0
    assert "local_smoke_check=passed" in capsys.readouterr().out


def test_check_kline_quality_script_real_path_requires_explicit_flag(monkeypatch: Any) -> None:
    report = check_kline_batch_before_persist(
        [build_dto(0)],
        server_time_ms=build_dto(0).close_time_ms + 1,
    )
    called: dict[str, Any] = {}
    fake_db_session = object()

    @contextmanager
    def fake_session_scope(*, commit_on_success: bool = False):
        called["commit_on_success"] = commit_on_success
        yield fake_db_session

    def fake_real_check(db_session: Any, **kwargs: Any) -> Any:
        called["db_session"] = db_session
        called["kwargs"] = kwargs
        return report

    monkeypatch.setattr(mysql_session, "session_scope", fake_session_scope)
    monkeypatch.setattr(quality_script, "run_recent_kline_integrity_check", fake_real_check)

    exit_code = quality_script.main(["--run-real-check", "--limit", "1"])

    assert exit_code == 0
    assert called["commit_on_success"] is True
    assert called["db_session"] is fake_db_session
    assert called["kwargs"]["limit"] == 1
    assert "send_alert" not in called["kwargs"]


def build_passed_recent_report() -> Any:
    return build_quality_report(
        check_type=CHECK_TYPE_RECENT_KLINE_INTEGRITY,
        klines=[build_dto(0)],
        issues=(),
        check_trigger_source=CHECK_TRIGGER_SOURCE_CLI,
        writable_klines=(),
    )


def build_failed_recent_report(issue_type: KlineQualityIssueType) -> Any:
    dto = build_dto(0)
    issue = KlineQualityIssue(
        issue_type=issue_type,
        severity=KlineQualitySeverity.ERROR,
        message=f"test quality issue: {issue_type.value}",
        open_time_ms=dto.open_time_ms,
    )
    return build_quality_report(
        check_type=CHECK_TYPE_RECENT_KLINE_INTEGRITY,
        klines=[dto],
        issues=(issue,),
        check_trigger_source=CHECK_TRIGGER_SOURCE_CLI,
        writable_klines=(),
    )


def test_run_real_check_success_does_not_send_success_alert_by_default(monkeypatch: Any) -> None:
    report = build_passed_recent_report()
    fake_db_session = object()

    @contextmanager
    def fake_session_scope(*, commit_on_success: bool = False):
        yield fake_db_session

    def fake_real_check(db_session: Any, **kwargs: Any) -> Any:
        return report

    def fail_alert(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("successful real check must not alert by default")

    monkeypatch.setattr(mysql_session, "session_scope", fake_session_scope)
    monkeypatch.setattr(quality_script, "run_recent_kline_integrity_check", fake_real_check)
    monkeypatch.setattr(quality_script, "send_quality_alert_if_needed", fail_alert)

    exit_code = quality_script.main(["--run-real-check", "--limit", "1"])

    assert exit_code == 0


def test_send_success_alert_sends_success_alert_for_healthy_recent_klines(monkeypatch: Any) -> None:
    report = build_passed_recent_report()
    called: dict[str, Any] = {}
    fake_db_session = object()

    @contextmanager
    def fake_session_scope(*, commit_on_success: bool = False):
        yield fake_db_session

    def fake_real_check(db_session: Any, **kwargs: Any) -> Any:
        return report

    def fake_send_alert(report_arg: Any, **kwargs: Any) -> AlertSendResult:
        called["alert_report"] = report_arg
        called["alert_kwargs"] = kwargs
        return AlertSendResult(status=AlertSendStatus.SENT, attempted_real_send=True)

    monkeypatch.setattr(mysql_session, "session_scope", fake_session_scope)
    monkeypatch.setattr(quality_script, "run_recent_kline_integrity_check", fake_real_check)
    monkeypatch.setattr(quality_script, "send_quality_alert_if_needed", fake_send_alert)

    exit_code = quality_script.main(["--run-real-check", "--limit", "1", "--send-success-alert"])

    assert exit_code == 0
    assert called["alert_report"] is report
    assert called["alert_kwargs"]["send_success_alert"] is True
    assert called["alert_kwargs"]["send_real_alert"] is True


def test_daily_health_report_sends_success_alert_for_healthy_recent_klines(monkeypatch: Any) -> None:
    report = build_passed_recent_report()
    called: dict[str, Any] = {}
    fake_db_session = object()

    @contextmanager
    def fake_session_scope(*, commit_on_success: bool = False):
        called["commit_on_success"] = commit_on_success
        yield fake_db_session

    def fake_real_check(db_session: Any, **kwargs: Any) -> Any:
        called["db_session"] = db_session
        called["kwargs"] = kwargs
        return report

    def fake_send_alert(report_arg: Any, **kwargs: Any) -> AlertSendResult:
        called["alert_report"] = report_arg
        called["alert_kwargs"] = kwargs
        return AlertSendResult(status=AlertSendStatus.SENT, attempted_real_send=True)

    monkeypatch.setattr(mysql_session, "session_scope", fake_session_scope)
    monkeypatch.setattr(quality_script, "run_recent_kline_integrity_check", fake_real_check)
    monkeypatch.setattr(quality_script, "send_quality_alert_if_needed", fake_send_alert)

    exit_code = quality_script.main(["--run-real-check", "--limit", "1", "--daily-health-report"])

    assert exit_code == 0
    assert called["commit_on_success"] is True
    assert called["alert_report"] is report
    assert called["alert_kwargs"]["send_success_alert"] is True
    assert called["alert_kwargs"]["send_real_alert"] is True


def test_recent_missing_kline_default_real_check_sends_failure_alert(monkeypatch: Any) -> None:
    _assert_real_check_failure_sends_alert(monkeypatch, KlineQualityIssueType.MISSING_IN_DATABASE)


def test_batch_discontinuity_default_real_check_sends_failure_alert(monkeypatch: Any) -> None:
    _assert_real_check_failure_sends_alert(monkeypatch, KlineQualityIssueType.BATCH_NOT_CONTINUOUS)


def test_database_not_continuous_default_real_check_sends_failure_alert(monkeypatch: Any) -> None:
    _assert_real_check_failure_sends_alert(monkeypatch, KlineQualityIssueType.DATABASE_NOT_CONTINUOUS)


def test_daily_health_report_failure_sends_failure_alert(monkeypatch: Any) -> None:
    _assert_real_check_failure_sends_alert(
        monkeypatch,
        KlineQualityIssueType.MISSING_IN_DATABASE,
        argv=["--run-real-check", "--limit", "1", "--daily-health-report"],
        expected_send_success_alert=True,
    )


def test_hermes_failure_makes_real_check_exit_nonzero(monkeypatch: Any) -> None:
    report = build_failed_recent_report(KlineQualityIssueType.MISSING_IN_DATABASE)
    fake_db_session = object()

    @contextmanager
    def fake_session_scope(*, commit_on_success: bool = False):
        yield fake_db_session

    def fake_real_check(db_session: Any, **kwargs: Any) -> Any:
        return report

    def fake_send_alert(report_arg: Any, **kwargs: Any) -> AlertSendResult:
        return AlertSendResult(
            status=AlertSendStatus.FAILED,
            error_message="Hermes unavailable",
            attempted_real_send=True,
        )

    monkeypatch.setattr(mysql_session, "session_scope", fake_session_scope)
    monkeypatch.setattr(quality_script, "run_recent_kline_integrity_check", fake_real_check)
    monkeypatch.setattr(quality_script, "send_quality_alert_if_needed", fake_send_alert)

    exit_code = quality_script.main(["--run-real-check", "--limit", "1"])

    assert exit_code == 3


def test_smoke_check_does_not_send_wechat_alert(monkeypatch: Any) -> None:
    def fail_alert(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("smoke check must not send alerts")

    monkeypatch.setattr(quality_script, "send_quality_alert_if_needed", fail_alert)

    exit_code = quality_script.main([])

    assert exit_code == 0


def _assert_real_check_failure_sends_alert(
    monkeypatch: Any,
    issue_type: KlineQualityIssueType,
    *,
    argv: list[str] | None = None,
    expected_send_success_alert: bool = False,
) -> None:
    report = build_failed_recent_report(issue_type)
    called: dict[str, Any] = {}
    fake_db_session = object()

    @contextmanager
    def fake_session_scope(*, commit_on_success: bool = False):
        called["commit_on_success"] = commit_on_success
        yield fake_db_session

    def fake_real_check(db_session: Any, **kwargs: Any) -> Any:
        called["db_session"] = db_session
        called["kwargs"] = kwargs
        return report

    def fake_send_alert(report_arg: Any, **kwargs: Any) -> AlertSendResult:
        called["alert_report"] = report_arg
        called["alert_kwargs"] = kwargs
        return AlertSendResult(status=AlertSendStatus.SENT, attempted_real_send=True)

    monkeypatch.setattr(mysql_session, "session_scope", fake_session_scope)
    monkeypatch.setattr(quality_script, "run_recent_kline_integrity_check", fake_real_check)
    monkeypatch.setattr(quality_script, "send_quality_alert_if_needed", fake_send_alert)

    exit_code = quality_script.main(argv or ["--run-real-check", "--limit", "1"])

    assert exit_code == 2
    assert called["commit_on_success"] is True
    assert called["alert_report"] is report
    assert called["alert_kwargs"]["send_success_alert"] is expected_send_success_alert
    assert called["alert_kwargs"]["send_real_alert"] is True
