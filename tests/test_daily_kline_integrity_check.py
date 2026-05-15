from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import app.storage.mysql.session as mysql_session
from app.alerting.service import format_alert_message
from app.alerting.types import AlertSendResult, AlertSendStatus, AlertSeverity, AlertType
from app.core.config import AppSettings
from app.core.task_lock import build_kline_integrity_check_lock_key
from app.market_data.kline_constants import (
    DATA_SOURCE_BINANCE_REST_BY_CLI,
    KLINE_4H_INTERVAL_MS,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)
from app.market_data.kline_integrity.kline_integrity_service import run_daily_kline_integrity_check
from app.market_data.kline_integrity.types import (
    CHECK_MODE_DAILY_INTEGRITY_CHECK,
    CHECK_MODE_MANUAL_INTEGRITY_CHECK,
    EXIT_ALERT_FAILED,
    EXIT_PARAMETER_ERROR,
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
from scripts import check_kline_integrity as daily_script
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
        self.read_calls = 0
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
        self.read_calls += 1
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


class FakeIntegrityTaskLock:
    def __init__(
        self,
        *,
        acquired: bool = True,
        raise_on_acquire: bool = False,
        raise_on_release: bool = False,
    ) -> None:
        self.acquired = acquired
        self.raise_on_acquire = raise_on_acquire
        self.raise_on_release = raise_on_release
        self.acquire_calls: list[dict[str, Any]] = []
        self.release_calls: list[dict[str, Any]] = []

    def acquire_lock(self, *, key: str, owner: str, ttl_seconds: int) -> bool:
        self.acquire_calls.append({"key": key, "owner": owner, "ttl_seconds": ttl_seconds})
        if self.raise_on_acquire:
            raise RuntimeError("Redis lock acquire failed")
        return self.acquired

    def release_lock(self, *, key: str, owner: str) -> bool:
        self.release_calls.append({"key": key, "owner": owner})
        if self.raise_on_release:
            raise RuntimeError("Redis lock release failed")
        return True


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
    task_lock: FakeIntegrityTaskLock | None = None,
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
    fake_task_lock = task_lock or FakeIntegrityTaskLock()
    fake_session = FakeSession()

    result = run_daily_kline_integrity_check(
        active_request,
        db_session=fake_session,
        binance_client=fake_client,
        kline_repository=fake_repository,
        data_quality_repository=fake_quality_repository,
        alert_sender=fake_alert_sender,
        alert_repository=object(),
        task_lock=fake_task_lock,
    )
    return result, fake_repository, fake_quality_repository, fake_alert_sender, fake_session, fake_client


def assert_single_daily_notification(alert_sender: FakeAlertSender, report_status: str) -> None:
    assert len(alert_sender.calls) == 1
    event = alert_sender.calls[0]["event"]
    assert event.details["report_status"] == report_status
    assert event.details["symbol"] == "BTCUSDT"
    assert event.details["interval"]
    assert event.details["limit"] >= 1
    assert event.details["trigger_source"] in {"cli", "scheduler"}
    assert event.details["no_repair_performed"] is True
    expected_type = (
        AlertType.KLINE_INTEGRITY_CHECK_PASSED
        if report_status == "healthy"
        else AlertType.KLINE_INTEGRITY_CHECK_FAILED
    )
    assert event.alert_type == expected_type


def _format_single_daily_notification_message(alert_sender: FakeAlertSender) -> str:
    assert len(alert_sender.calls) == 1
    return format_alert_message(alert_sender.calls[0]["event"])


def _assert_daily_wechat_message_hides_internal_context(message: str) -> None:
    hidden_terms = (
        "report",
        "existing_open_time_ms",
        "writable_open_time_ms",
        "metadata",
        "lock_key",
        "requested_binance_limit",
        "enforce_database_source_rules",
        "Daily Kline integrity result",
        "Daily Kline health confirmed",
        "Binance REST official klines",
    )
    for hidden_term in hidden_terms:
        assert hidden_term not in message


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
    assert_single_daily_notification(alert_sender, "healthy")
    assert alert_sender.calls[0]["event"].details["source"] == "Binance REST official klines"
    assert repository.bulk_write_called is False
    assert repository.delete_called is False


def test_daily_healthy_notification_uses_compact_chinese_visible_body_without_internal_context() -> None:
    result, _repository, _quality_repository, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
    )

    event = alert_sender.calls[0]["event"]
    message = _format_single_daily_notification_message(alert_sender)

    assert event.title == "每日 K线健康检查通过"
    assert event.summary == "最近 100 根 4h K线检查通过"
    assert event.severity == AlertSeverity.INFO
    assert "【每日 K线健康检查通过】" in message
    assert "级别：信息" in message
    assert "币种周期：BTCUSDT 4h" in message
    assert "检查范围：" in message
    assert "检查数量：100 根" in message
    assert "问题数量：0" in message
    assert "最近 100 根 4h K线连续、无缺失、无重复、未发现数据质量异常。" in message
    assert "Binance REST 官方 K线" in message
    assert "不修复、不回补、不写入正式 K线表" in message
    assert "已过滤未收盘 K线 1 根，未写入数据库。" in message
    assert "本次为只读健康检查" in message
    assert "本提醒不是交易建议" in message
    assert f"追踪ID：{result.trace_id}" in message
    assert "action" not in message
    assert "check_mode" not in message
    assert "check_trigger" not in message
    assert "trigger_source" not in message
    _assert_daily_wechat_message_hides_internal_context(message)


def test_daily_notification_keeps_internal_context_structured_but_not_visible() -> None:
    _result, _repository, _quality_repository, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
    )

    event = alert_sender.calls[0]["event"]
    message = _format_single_daily_notification_message(alert_sender)
    internal_context = event.details["_internal_context"]

    assert event.details["action"] == "check_only_no_repair_no_backfill_no_market_kline_write"
    assert event.details["report"]["metadata"]["filtered_unclosed_count"] == 1
    assert len(event.details["report"]["existing_open_time_ms"]) == 100
    assert internal_context["report"] == event.details["report"]
    assert internal_context["lock_key"] == event.details["lock_key"]
    assert "check_only_no_repair_no_backfill_no_market_kline_write" not in message
    assert "filtered_unclosed_count" not in message


def test_daily_integrity_lock_acquire_success_uses_symbol_interval_key_and_releases() -> None:
    task_lock = FakeIntegrityTaskLock()
    expected_key = build_kline_integrity_check_lock_key(symbol="BTCUSDT", interval_value="4h")
    request = DailyKlineIntegrityCheckRequest(
        lookback_count=100,
        check_trigger=CHECK_TRIGGER_SOURCE_CLI,
        check_mode=CHECK_MODE_MANUAL_INTEGRITY_CHECK,
        lock_ttl_seconds=123,
    )

    result, _repository, _quality_repository, _alert_sender, _session, _client = run_daily_with_fakes(
        request=request,
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
        task_lock=task_lock,
    )

    assert result.status == DailyKlineIntegrityStatus.HEALTHY
    assert result.lock_key == expected_key
    assert task_lock.acquire_calls == [
        {"key": expected_key, "owner": request.trace_id, "ttl_seconds": 123}
    ]
    assert task_lock.release_calls == [{"key": expected_key, "owner": request.trace_id}]


def test_scheduler_daily_integrity_lock_occupied_sends_one_skipped_notification_without_data_work() -> None:
    task_lock = FakeIntegrityTaskLock(acquired=False)
    repository = FakeDailyKlineRepository([official_model(offset) for offset in range(100)])
    quality_repository = FakeQualityRepository()
    alert_sender = FakeAlertSender()

    result, repository, quality_repository, alert_sender, _session, client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[],
        kline_repository=repository,
        quality_repository=quality_repository,
        alert_sender=alert_sender,
        task_lock=task_lock,
    )

    assert result.status == DailyKlineIntegrityStatus.SKIPPED
    assert result.exit_code == EXIT_SUCCESS
    assert client.get_server_time_calls == 0
    assert client.get_klines_calls == []
    assert repository.read_calls == 0
    assert quality_repository.records == []
    assert_single_daily_notification(alert_sender, "skipped")
    assert alert_sender.calls[0]["event"].details["skip_reason"] == "integrity_check_lock_occupied"
    assert task_lock.release_calls == []


def test_manual_daily_integrity_lock_occupied_skips_without_forced_hermes() -> None:
    task_lock = FakeIntegrityTaskLock(acquired=False)
    repository = FakeDailyKlineRepository([official_model(offset) for offset in range(100)])
    quality_repository = FakeQualityRepository()
    alert_sender = FakeAlertSender()
    request = DailyKlineIntegrityCheckRequest(
        check_trigger=CHECK_TRIGGER_SOURCE_CLI,
        check_mode=CHECK_MODE_MANUAL_INTEGRITY_CHECK,
    )

    result, repository, quality_repository, alert_sender, _session, client = run_daily_with_fakes(
        request=request,
        raw_klines=make_raw_klines(101),
        existing=[],
        kline_repository=repository,
        quality_repository=quality_repository,
        alert_sender=alert_sender,
        task_lock=task_lock,
    )

    assert result.status == DailyKlineIntegrityStatus.SKIPPED
    assert client.get_server_time_calls == 0
    assert client.get_klines_calls == []
    assert repository.read_calls == 0
    assert quality_repository.records == []
    assert alert_sender.calls == []
    assert repository.bulk_write_called is False


def test_daily_integrity_lock_acquire_exception_returns_error_and_alerts() -> None:
    task_lock = FakeIntegrityTaskLock(raise_on_acquire=True)
    repository = FakeDailyKlineRepository([official_model(offset) for offset in range(100)])
    quality_repository = FakeQualityRepository()
    alert_sender = FakeAlertSender()

    result, repository, quality_repository, alert_sender, _session, client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[],
        kline_repository=repository,
        quality_repository=quality_repository,
        alert_sender=alert_sender,
        task_lock=task_lock,
    )

    assert result.status == DailyKlineIntegrityStatus.ERROR
    assert result.exit_code == EXIT_TASK_FAILED
    assert client.get_server_time_calls == 0
    assert client.get_klines_calls == []
    assert repository.read_calls == 0
    assert quality_repository.records[0].report.status.value == "error"
    assert_single_daily_notification(alert_sender, "unknown")
    assert task_lock.release_calls == []


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
    assert_single_daily_notification(alert_sender, "unhealthy")
    assert repository.bulk_write_called is False


def test_daily_unhealthy_notification_stays_error_and_shows_compact_issue_summary() -> None:
    existing = [official_model(offset) for offset in range(100) if offset not in {5, 6, 7, 8}]

    result, _repository, _quality_repository, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=existing,
    )

    event = alert_sender.calls[0]["event"]
    message = _format_single_daily_notification_message(alert_sender)

    assert result.status == DailyKlineIntegrityStatus.FAILED
    assert event.title == "每日 K线健康检查发现异常"
    assert event.severity in {AlertSeverity.ERROR, AlertSeverity.CRITICAL}
    assert "【每日 K线健康检查发现异常】" in message
    assert "级别：错误" in message
    assert "币种周期：BTCUSDT 4h" in message
    assert "检查范围：" in message
    assert "检查数量：100 根" in message
    assert "问题数量：4" in message
    assert "关键问题：" in message
    assert "1. 数据库缺失 Binance 官方 K线" in message
    assert "2. 数据库缺失 Binance 官方 K线" in message
    assert "3. 数据库缺失 Binance 官方 K线" in message
    assert "4. 数据库缺失 Binance 官方 K线" not in message
    assert "数据质量检查ID：" in message
    assert f"追踪ID：{result.trace_id}" in message
    assert "请检查采集链路、Binance REST 返回、数据库最近 K线" in message
    assert "不要人工改数、不要自动修复" in message
    assert "本次为只读健康检查" in message
    assert "本提醒不是交易建议" in message
    assert "action" not in message
    assert "check_mode" not in message
    assert "check_trigger" not in message
    assert "trigger_source" not in message
    _assert_daily_wechat_message_hides_internal_context(message)


def test_daily_database_field_mismatch_fails_alerts_and_does_not_overwrite() -> None:
    existing = [official_model(offset) for offset in range(100)]
    existing[7] = official_model(7, close_price="1.00000000")

    result, repository, _quality, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=existing,
    )

    assert result.status == DailyKlineIntegrityStatus.FAILED
    assert result.first_issue_type == KlineQualityIssueType.DATABASE_FIELD_MISMATCH.value
    assert_single_daily_notification(alert_sender, "unhealthy")
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
    assert_single_daily_notification(alert_sender, "unhealthy")
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
    assert quality_repository.records[0].report.status.value == "error"
    assert_single_daily_notification(alert_sender, "unknown")
    assert repository.bulk_write_called is False


def test_daily_task_exception_releases_integrity_lock() -> None:
    task_lock = FakeIntegrityTaskLock()

    result, _repository, _quality_repository, _alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
        task_lock=task_lock,
        fail_binance_on="klines",
    )

    assert result.status == DailyKlineIntegrityStatus.ERROR
    assert len(task_lock.acquire_calls) == 1
    assert task_lock.release_calls == [
        {"key": task_lock.acquire_calls[0]["key"], "owner": task_lock.acquire_calls[0]["owner"]}
    ]


def test_daily_lock_release_failure_is_logged(monkeypatch: Any) -> None:
    import app.market_data.kline_integrity.kline_integrity_service as daily_service

    task_lock = FakeIntegrityTaskLock(raise_on_release=True)
    logged: list[tuple[str, tuple[Any, ...]]] = []

    def fake_log_exception(message: str, *args: Any, **_kwargs: Any) -> None:
        logged.append((message, args))

    monkeypatch.setattr(daily_service.LOGGER, "exception", fake_log_exception)

    result, _repository, _quality_repository, _alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
        task_lock=task_lock,
    )

    assert result.status == DailyKlineIntegrityStatus.HEALTHY
    assert len(task_lock.release_calls) == 1
    assert logged[0][0].startswith("Failed to release daily Kline integrity lock")


def test_daily_database_read_failure_records_error_when_possible_and_alerts() -> None:
    result, _repository, quality_repository, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[],
        kline_repository=FakeDailyKlineRepository(fail_on_read=True),
    )

    assert result.status == DailyKlineIntegrityStatus.ERROR
    assert result.exit_code == EXIT_TASK_FAILED
    assert quality_repository.records[0].report.status.value == "error"
    assert_single_daily_notification(alert_sender, "unknown")


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
    assert_single_daily_notification(alert_sender, "unknown")
    assert repository.bulk_write_called is False


def test_daily_success_hermes_failure_keeps_check_fact_but_returns_alert_failed() -> None:
    failed_alert = FakeAlertSender(
        AlertSendResult(status=AlertSendStatus.SUBMIT_FAILED, error_message="Hermes unavailable")
    )

    result, _repository, quality_repository, alert_sender, _session, _client = run_daily_with_fakes(
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
        alert_sender=failed_alert,
    )

    assert result.status == DailyKlineIntegrityStatus.HEALTHY
    assert result.exit_code == EXIT_ALERT_FAILED
    assert result.alert_status == AlertSendStatus.SUBMIT_FAILED.value
    assert quality_repository.records[0].report.status.value == "passed"
    assert_single_daily_notification(alert_sender, "healthy")


def test_scheduler_notify_success_false_still_sends_single_daily_result_notification() -> None:
    success_request = DailyKlineIntegrityCheckRequest(notify_success=False)
    result, _repo, _quality, alert_sender, _session, _client = run_daily_with_fakes(
        request=success_request,
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(100)],
    )

    assert result.status == DailyKlineIntegrityStatus.HEALTHY
    assert_single_daily_notification(alert_sender, "healthy")

    failed_request = DailyKlineIntegrityCheckRequest(notify_success=False)
    result, _repo, _quality, alert_sender, _session, _client = run_daily_with_fakes(
        request=failed_request,
        raw_klines=make_raw_klines(101),
        existing=[official_model(offset) for offset in range(99)],
    )

    assert result.status == DailyKlineIntegrityStatus.FAILED
    assert_single_daily_notification(alert_sender, "unhealthy")


def test_scheduler_parameter_error_sends_one_unknown_notification_without_data_work() -> None:
    request = DailyKlineIntegrityCheckRequest(interval_value="1h")
    repository = FakeDailyKlineRepository([official_model(offset) for offset in range(100)])
    quality_repository = FakeQualityRepository()
    alert_sender = FakeAlertSender()

    result, repository, quality_repository, alert_sender, _session, client = run_daily_with_fakes(
        request=request,
        raw_klines=make_raw_klines(101),
        existing=[],
        kline_repository=repository,
        quality_repository=quality_repository,
        alert_sender=alert_sender,
    )

    assert result.status == DailyKlineIntegrityStatus.ERROR
    assert result.exit_code == EXIT_TASK_FAILED
    assert client.get_server_time_calls == 0
    assert client.get_klines_calls == []
    assert repository.read_calls == 0
    assert repository.bulk_write_called is False
    assert quality_repository.records == []
    assert_single_daily_notification(alert_sender, "unknown")


def test_manual_parameter_error_does_not_force_hermes() -> None:
    request = DailyKlineIntegrityCheckRequest(
        interval_value="1h",
        check_trigger=CHECK_TRIGGER_SOURCE_CLI,
        check_mode=CHECK_MODE_MANUAL_INTEGRITY_CHECK,
    )
    alert_sender = FakeAlertSender()

    result, repository, quality_repository, alert_sender, _session, client = run_daily_with_fakes(
        request=request,
        raw_klines=make_raw_klines(101),
        existing=[],
        alert_sender=alert_sender,
    )

    assert result.status == DailyKlineIntegrityStatus.ERROR
    assert result.exit_code == EXIT_PARAMETER_ERROR
    assert client.get_server_time_calls == 0
    assert repository.read_calls == 0
    assert repository.bulk_write_called is False
    assert quality_repository.records == []
    assert alert_sender.calls == []


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
    assert called["request"].check_trigger == CHECK_TRIGGER_SOURCE_SCHEDULER
    assert called["request"].check_mode == CHECK_MODE_DAILY_INTEGRITY_CHECK
    assert called["kwargs"]["db_session"] is db_session


def test_daily_cli_allows_only_cli_trigger_and_does_not_restore_send_alert(monkeypatch: Any) -> None:
    source = Path("scripts/check_kline_integrity.py").read_text(encoding="utf-8")
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
    assert called["request"].check_trigger == CHECK_TRIGGER_SOURCE_CLI
    assert called["request"].check_mode == CHECK_MODE_MANUAL_INTEGRITY_CHECK
    assert called["request"].lookback_count == 1
    assert called["request"].notify_success is False

    exit_code = daily_script.main(["--check-trigger", "cli", "--lookback-count", "2", "--no-notify-success"])

    assert exit_code == EXIT_SUCCESS
    assert called["request"].check_trigger == CHECK_TRIGGER_SOURCE_CLI
    assert called["request"].lookback_count == 2


def test_daily_cli_rejects_start_end_range_in_phase_11(monkeypatch: Any) -> None:
    called: dict[str, bool] = {}

    def fake_service(*_args: Any, **_kwargs: Any) -> Any:
        called["service"] = True
        raise AssertionError("service must not run for unsupported range review")

    monkeypatch.setattr(daily_script, "run_daily_kline_integrity_check", fake_service)

    exit_code = daily_script.main(
        [
            "--check-trigger",
            "cli",
            "--start-time",
            "2026-05-01T00:00:00Z",
            "--end-time",
            "2026-05-08T00:00:00Z",
        ]
    )

    assert exit_code == EXIT_PARAMETER_ERROR
    assert "service" not in called


def test_daily_review_sources_do_not_use_forbidden_private_or_repair_capabilities() -> None:
    source = (
        Path("app/market_data/kline_integrity/kline_integrity_service.py").read_text(encoding="utf-8")
        + Path("scripts/check_kline_integrity.py").read_text(encoding="utf-8")
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
