"""Read-only BTCUSDT 1d daily Kline integrity review service.

Call chain for scheduler:
app/scheduler/jobs/kline_1d_integrity_check.py::run_kline_1d_integrity_check_job
    -> app/market_data/kline_integrity/kline_1d_integrity_service.py::run_daily_1d_kline_integrity_check
    -> app/core/task_lock.py::RedisTaskLock.acquire_lock
    -> app/storage/mysql/repositories/market_kline_1d_repository.py::list_recent
    -> app/storage/mysql/repositories/data_quality_check_repository.py::create_quality_check_record
    -> app/storage/mysql/repositories/collector_event_log_repository.py::mark_event_status
    -> app/alerting/service.py::send_alert

This file belongs to `app/market_data/kline_integrity`. It performs a read-only
daily health review of the formal `market_kline_1d` table. It does not request
Binance, write `market_kline_1d`, write `market_kline_4h`, backfill data, repair
data, call scripts, call DeepSeek, generate strategy advice, or perform trading.
It may write `collector_event_log`, `data_quality_check`, Redis task-lock keys,
and optional `alert_message` records through the shared alerting service.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Sequence

from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.logger import get_logger
from app.core.task_lock import RedisTaskLock, build_kline_integrity_check_lock_key
from app.core.time_utils import (
    UTC,
    now_utc,
    timestamp_ms_to_utc_datetime,
    utc_aware_to_prc_aware,
    utc_datetime_to_timestamp_ms,
)
from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_MS,
    KLINE_1D_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
    TRIGGER_SOURCE_SCHEDULER,
)
from app.market_data.kline_integrity.kline_1d_integrity_alerts import build_daily_1d_integrity_alert_event
from app.market_data.kline_integrity.kline_1d_integrity_types import (
    CHECK_TYPE_DAILY_KLINE_1D_INTEGRITY,
    EXIT_ALERT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_QUALITY_FAILED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    DailyKline1dIntegrityCheckRequest,
    DailyKline1dIntegrityCheckResult,
    DailyKline1dIntegrityStatus,
    KLINE_1D_INTEGRITY_EVENT_TYPE,
)
from app.market_data.kline_integrity.results import record_id
from app.market_data.kline_quality.types import (
    KlineQualityIssue,
    KlineQualityIssueType,
    KlineQualityReport,
    KlineQualitySeverity,
    KlineQualityStatus,
)

LOGGER = get_logger("market_data.kline_integrity.1d_daily")
ALLOWED_1D_DAILY_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER})


class DailyKline1dIntegrityParameterError(ValueError):
    """Raised when a 1d daily review request is invalid before external access."""


def run_daily_1d_kline_integrity_check(
    request: DailyKline1dIntegrityCheckRequest,
    *,
    db_session: Any,
    kline_repository: Any | None = None,
    data_quality_repository: Any | None = None,
    collector_event_repository: Any | None = None,
    alert_sender: Any | None = None,
    alert_repository: Any | None = None,
    task_lock: Any | None = None,
    current_time_utc: datetime | None = None,
) -> DailyKline1dIntegrityCheckResult:
    """Run one read-only daily BTCUSDT 1d Kline review.

    Parameters: `request` identifies the symbol/interval, trigger source,
    lookback count, notification preference, and trace id. Dependencies are
    injectable for tests.
    Return value: a structured result for CLI, scheduler, and alerts.
    Failure scenarios: invalid parameters, Redis lock failures, repository
    failures, event-log failures, data-quality write failures, and alert
    submission failures are reported without modifying formal Kline data.
    External services: no Binance access; Hermes only through the alert sender.
    Data impact: reads `market_kline_1d`; writes only audit/alert tables and
    Redis lock keys.
    """

    try:
        validate_daily_1d_kline_integrity_request(request)
    except DailyKline1dIntegrityParameterError as exc:
        return DailyKline1dIntegrityCheckResult(
            status=DailyKline1dIntegrityStatus.ERROR,
            exit_code=EXIT_PARAMETER_ERROR,
            trace_id=request.trace_id,
            message=str(exc),
            requested_count=request.requested_count,
            details=_base_details(request, status="error", error_message=str(exc)),
        )

    active_now = _ensure_utc(current_time_utc or now_utc())
    active_lock = task_lock or RedisTaskLock()
    active_kline_repository = kline_repository or _default_kline_1d_repository()
    active_quality_repository = data_quality_repository or _default_data_quality_repository()
    active_collector_repository = collector_event_repository or _default_collector_event_repository()
    lock_key = build_kline_integrity_check_lock_key(
        symbol=request.symbol,
        interval_value=request.interval_value,
    )
    lock_acquired = False
    event_log: Any | None = None
    report: KlineQualityReport | None = None
    quality_record: Any | None = None

    try:
        lock_acquired = active_lock.acquire_lock(
            key=lock_key,
            owner=request.trace_id,
            ttl_seconds=request.lock_ttl_seconds,
        )
        if not lock_acquired:
            event_log = active_collector_repository.create_skipped_event(
                db_session,
                event_type=KLINE_1D_INTEGRITY_EVENT_TYPE,
                symbol=request.symbol,
                interval_value=request.interval_value,
                trigger_source=request.check_trigger,
                data_source=request.data_source,
                requested_start_open_time_ms=0,
                requested_end_open_time_ms=0,
                requested_count=request.requested_count,
                trace_id=request.trace_id,
                reason=f"task lock already held: {lock_key}",
                details={"lock_key": lock_key, "range_unavailable_reason": "任务锁已存在，本次跳过"},
            )
            _commit_if_possible(db_session)
            return DailyKline1dIntegrityCheckResult(
                status=DailyKline1dIntegrityStatus.SKIPPED,
                exit_code=EXIT_SUCCESS,
                trace_id=request.trace_id,
                message=f"Skipped because task lock is already held: {lock_key}",
                requested_count=request.requested_count,
                event_log_id=record_id(event_log),
                lock_key=lock_key,
                details=_base_details(
                    request,
                    status="skipped",
                    lock_key=lock_key,
                    range_unavailable_reason="任务锁已存在，本次跳过",
                ),
            )

        event_log = _create_running_event(
            active_collector_repository,
            db_session,
            request,
            lock_key=lock_key,
        )
        rows = list(active_kline_repository.list_recent(db_session, symbol=request.symbol, limit=request.lookback_count))
        report = _build_1d_integrity_report(
            request,
            rows,
            current_time_utc=active_now,
        )
        quality_record = active_quality_repository.create_quality_check_record(db_session, report)
        result = _result_from_report(
            request,
            report,
            quality_record=quality_record,
            event_log=event_log,
            lock_key=lock_key,
        )
        _mark_integrity_event(
            active_collector_repository,
            db_session,
            event_log,
            result,
            report=report,
            quality_check_id=record_id(quality_record),
        )
        _commit_if_possible(db_session)

        if _should_send_integrity_alert(request, result):
            return _send_integrity_alert_and_adjust_result(
                result,
                request=request,
                db_session=db_session,
                alert_sender=alert_sender,
                alert_repository=alert_repository,
                quality_repository=active_quality_repository,
                quality_record=quality_record,
            )
        return result
    except Exception as exc:  # noqa: BLE001 - audit and alert the task failure.
        LOGGER.exception("1d daily Kline integrity check failed trace_id=%s", request.trace_id)
        _rollback_if_possible(db_session)
        error_result = _build_task_failure_result(
            request,
            error=exc,
            event_log=event_log,
            lock_key=lock_key,
            report=report,
        )
        try:
            if event_log is None:
                event_log = _create_running_event(
                    active_collector_repository,
                    db_session,
                    request,
                    lock_key=lock_key,
                )
            _mark_failed_event_for_exception(
                active_collector_repository,
                db_session,
                event_log,
                error_result,
                error=exc,
            )
            _commit_if_possible(db_session)
        except Exception:  # noqa: BLE001 - preserve the failure result even if event logging fails.
            LOGGER.exception("Failed to record 1d integrity task failure event trace_id=%s", request.trace_id)
            _rollback_if_possible(db_session)

        return _send_integrity_alert_and_adjust_result(
            error_result,
            request=request,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
            quality_repository=None,
            quality_record=None,
        )
    finally:
        if lock_acquired:
            _release_integrity_lock_safely(active_lock, key=lock_key, owner=request.trace_id)


def validate_daily_1d_kline_integrity_request(request: DailyKline1dIntegrityCheckRequest) -> None:
    """Validate a 1d review request before Redis, MySQL, or Hermes access."""

    if not request.symbol.strip():
        raise DailyKline1dIntegrityParameterError("symbol must not be empty")
    if request.symbol.strip().upper() != DEFAULT_KLINE_SYMBOL:
        raise DailyKline1dIntegrityParameterError("1d daily integrity check only supports BTCUSDT")
    if request.interval_value != KLINE_1D_INTERVAL_VALUE:
        raise DailyKline1dIntegrityParameterError("interval must be 1d")
    if request.lookback_count <= 0:
        raise DailyKline1dIntegrityParameterError("lookback_count must be greater than 0")
    if request.check_trigger not in ALLOWED_1D_DAILY_TRIGGER_SOURCES:
        raise DailyKline1dIntegrityParameterError("check_trigger must be cli or scheduler")
    if request.lock_ttl_seconds <= 0:
        raise DailyKline1dIntegrityParameterError("lock_ttl_seconds must be greater than 0")


def _build_1d_integrity_report(
    request: DailyKline1dIntegrityCheckRequest,
    rows: Sequence[Any],
    *,
    current_time_utc: datetime,
) -> KlineQualityReport:
    """Check recent `market_kline_1d` rows without requesting Binance or writing Klines."""

    sorted_rows = sorted(rows, key=lambda row: int(getattr(row, "open_time_ms")))
    expected_latest_open_time_ms = _expected_latest_closed_1d_open_time(current_time_utc)
    issues = list(_collect_row_issues(sorted_rows, expected_latest_open_time_ms, current_time_utc))
    latest_open_time_ms = int(getattr(sorted_rows[-1], "open_time_ms")) if sorted_rows else None

    if not sorted_rows:
        status = KlineQualityStatus.WARNING
        severity = KlineQualitySeverity.WARNING
        metadata_status = "not_initialized"
    elif any(issue.severity in {KlineQualitySeverity.ERROR, KlineQualitySeverity.CRITICAL} for issue in issues):
        status = KlineQualityStatus.FAILED
        severity = _highest_issue_severity(issues)
        metadata_status = "failed"
    elif issues:
        status = KlineQualityStatus.WARNING
        severity = KlineQualitySeverity.WARNING
        metadata_status = "warning"
    else:
        status = KlineQualityStatus.PASSED
        severity = KlineQualitySeverity.INFO
        metadata_status = "healthy"

    first = sorted_rows[0] if sorted_rows else None
    last = sorted_rows[-1] if sorted_rows else None
    return KlineQualityReport(
        check_type=CHECK_TYPE_DAILY_KLINE_1D_INTEGRITY,
        symbol=request.symbol,
        interval_value=request.interval_value,
        check_trigger_source=request.check_trigger,
        status=status,
        severity=severity,
        checked_count=len(sorted_rows),
        issues=tuple(issues),
        start_open_time_ms=int(getattr(first, "open_time_ms")) if first is not None else None,
        start_open_time_utc=_row_utc_from_ms(first, "open_time_ms") if first is not None else None,
        start_open_time_prc=_row_prc_from_ms(first, "open_time_ms") if first is not None else None,
        end_open_time_ms=int(getattr(last, "open_time_ms")) if last is not None else None,
        end_open_time_utc=_row_utc_from_ms(last, "open_time_ms") if last is not None else None,
        end_open_time_prc=_row_prc_from_ms(last, "open_time_ms") if last is not None else None,
        metadata={
            "trace_id": request.trace_id,
            "status": metadata_status,
            "report_status": metadata_status,
            "latest_open_time_ms": latest_open_time_ms or "",
            "expected_latest_open_time_ms": expected_latest_open_time_ms,
            "source": "market_kline_1d read-only daily review",
            "no_repair_performed": True,
            "action": "read_only_no_repair_no_backfill_no_market_kline_write",
        },
    )


def _collect_row_issues(
    rows: Sequence[Any],
    expected_latest_open_time_ms: int,
    current_time_utc: datetime,
) -> Iterable[KlineQualityIssue]:
    if not rows:
        yield KlineQualityIssue(
            issue_type=KlineQualityIssueType.EMPTY_BATCH,
            severity=KlineQualitySeverity.WARNING,
            message="market_kline_1d 尚未初始化，请先执行手动 1d backfill",
        )
        return

    seen_open_times: set[int] = set()
    previous_open_time_ms: int | None = None
    now_ms = utc_datetime_to_timestamp_ms(current_time_utc)
    for row in rows:
        open_time_ms = int(getattr(row, "open_time_ms"))
        if open_time_ms in seen_open_times:
            yield _issue(
                KlineQualityIssueType.DUPLICATE_OPEN_TIME,
                KlineQualitySeverity.ERROR,
                "market_kline_1d 存在重复 open_time",
                open_time_ms=open_time_ms,
            )
        seen_open_times.add(open_time_ms)

        if previous_open_time_ms is not None and open_time_ms - previous_open_time_ms != KLINE_1D_INTERVAL_MS:
            yield KlineQualityIssue(
                issue_type=KlineQualityIssueType.BATCH_NOT_CONTINUOUS,
                severity=KlineQualitySeverity.ERROR,
                message="最近 1d 日 K 不连续，存在缺口或异常间隔",
                previous_open_time_ms=previous_open_time_ms,
                next_open_time_ms=open_time_ms,
                open_time_ms=open_time_ms,
                expected_value=str(previous_open_time_ms + KLINE_1D_INTERVAL_MS),
                actual_value=str(open_time_ms),
            )
        previous_open_time_ms = open_time_ms

        yield from _collect_single_row_issues(row, now_ms=now_ms, expected_latest_open_time_ms=expected_latest_open_time_ms)

    latest_open_time_ms = int(getattr(rows[-1], "open_time_ms"))
    if latest_open_time_ms > expected_latest_open_time_ms:
        yield _issue(
            KlineQualityIssueType.UNCLOSED_KLINE,
            KlineQualitySeverity.CRITICAL,
            "最新 1d 日 K 晚于理论最新已收盘日 K，疑似未收盘日 K 误写正式表或系统时间异常",
            open_time_ms=latest_open_time_ms,
            expected_value=str(expected_latest_open_time_ms),
            actual_value=str(latest_open_time_ms),
        )
    elif latest_open_time_ms < expected_latest_open_time_ms:
        lag_bars = (expected_latest_open_time_ms - latest_open_time_ms) // KLINE_1D_INTERVAL_MS
        severity = KlineQualitySeverity.WARNING if lag_bars <= 1 else KlineQualitySeverity.ERROR
        yield _issue(
            KlineQualityIssueType.MISSING_IN_DATABASE,
            severity,
            f"最新 1d 日 K 落后理论最新已收盘日 K {lag_bars} 根",
            open_time_ms=latest_open_time_ms,
            expected_value=str(expected_latest_open_time_ms),
            actual_value=str(latest_open_time_ms),
        )


def _collect_single_row_issues(
    row: Any,
    *,
    now_ms: int,
    expected_latest_open_time_ms: int,
) -> Iterable[KlineQualityIssue]:
    open_time_ms = int(getattr(row, "open_time_ms"))
    close_time_ms = int(getattr(row, "close_time_ms"))
    if open_time_ms % KLINE_1D_INTERVAL_MS != 0:
        yield _issue(
            KlineQualityIssueType.INVALID_KLINE,
            KlineQualitySeverity.ERROR,
            "1d open_time 未对齐 UTC 00:00:00",
            open_time_ms=open_time_ms,
            field_name="open_time_ms",
        )
    if close_time_ms != open_time_ms + KLINE_1D_INTERVAL_MS - 1:
        yield _issue(
            KlineQualityIssueType.INVALID_KLINE,
            KlineQualitySeverity.ERROR,
            "1d close_time_ms 不符合 open_time_ms + 86400000 - 1",
            open_time_ms=open_time_ms,
            field_name="close_time_ms",
            expected_value=str(open_time_ms + KLINE_1D_INTERVAL_MS - 1),
            actual_value=str(close_time_ms),
        )
    if close_time_ms >= now_ms or open_time_ms > expected_latest_open_time_ms:
        yield _issue(
            KlineQualityIssueType.UNCLOSED_KLINE,
            KlineQualitySeverity.CRITICAL,
            "正式表存在未收盘 1d 日 K 误写",
            open_time_ms=open_time_ms,
            field_name="close_time_ms",
            expected_value=f"< {now_ms}",
            actual_value=str(close_time_ms),
        )

    open_price = _decimal_or_none(row, "open_price")
    high_price = _decimal_or_none(row, "high_price")
    low_price = _decimal_or_none(row, "low_price")
    close_price = _decimal_or_none(row, "close_price")
    volume = _decimal_or_none(row, "volume")
    quote_volume = _decimal_or_none(row, "quote_volume")
    taker_buy_base_volume = _decimal_or_none(row, "taker_buy_base_volume")
    taker_buy_quote_volume = _decimal_or_none(row, "taker_buy_quote_volume")
    trade_count = _int_or_none(row, "trade_count")

    for field_name, value in {
        "open_price": open_price,
        "high_price": high_price,
        "low_price": low_price,
        "close_price": close_price,
    }.items():
        if value is None or value <= Decimal("0"):
            yield _issue(
                KlineQualityIssueType.INVALID_KLINE,
                KlineQualitySeverity.ERROR,
                "1d 价格字段为空、为零或为负数",
                open_time_ms=open_time_ms,
                field_name=field_name,
                actual_value=str(value),
            )
    if None not in (open_price, high_price, low_price, close_price):
        assert open_price is not None and high_price is not None and low_price is not None and close_price is not None
        if high_price < max(open_price, close_price, low_price):
            yield _issue(
                KlineQualityIssueType.INVALID_KLINE,
                KlineQualitySeverity.ERROR,
                "1d high_price 小于 OHLC 组成字段",
                open_time_ms=open_time_ms,
                field_name="high_price",
            )
        if low_price > min(open_price, close_price, high_price):
            yield _issue(
                KlineQualityIssueType.INVALID_KLINE,
                KlineQualitySeverity.ERROR,
                "1d low_price 大于 OHLC 组成字段",
                open_time_ms=open_time_ms,
                field_name="low_price",
            )
    for field_name, value in {
        "volume": volume,
        "quote_volume": quote_volume,
        "taker_buy_base_volume": taker_buy_base_volume,
        "taker_buy_quote_volume": taker_buy_quote_volume,
    }.items():
        if value is None or value < Decimal("0"):
            yield _issue(
                KlineQualityIssueType.INVALID_KLINE,
                KlineQualitySeverity.ERROR,
                "1d 成交量字段为空或为负数",
                open_time_ms=open_time_ms,
                field_name=field_name,
                actual_value=str(value),
            )
    if trade_count is None or trade_count < 0:
        yield _issue(
            KlineQualityIssueType.INVALID_KLINE,
            KlineQualitySeverity.ERROR,
            "1d trade_count 为空或为负数",
            open_time_ms=open_time_ms,
            field_name="trade_count",
            actual_value=str(trade_count),
        )


def _result_from_report(
    request: DailyKline1dIntegrityCheckRequest,
    report: KlineQualityReport,
    *,
    quality_record: Any | None,
    event_log: Any | None,
    lock_key: str,
) -> DailyKline1dIntegrityCheckResult:
    first_issue = report.first_issue
    status = _result_status_from_report(report)
    exit_code = EXIT_SUCCESS if status == DailyKline1dIntegrityStatus.HEALTHY else EXIT_QUALITY_FAILED
    latest_open_time_ms = _metadata_int(report, "latest_open_time_ms")
    expected_latest_open_time_ms = _metadata_int(report, "expected_latest_open_time_ms")
    return DailyKline1dIntegrityCheckResult(
        status=status,
        exit_code=exit_code,
        trace_id=request.trace_id,
        message=_result_message(status, first_issue),
        requested_count=request.requested_count,
        checked_count=report.checked_count,
        issue_count=report.issue_count,
        first_issue_type=first_issue.issue_type.value if first_issue else None,
        first_issue_message=first_issue.message if first_issue else None,
        checked_start_time=_datetime_to_text(report.start_open_time_utc),
        checked_end_time=_datetime_to_text(report.end_open_time_utc),
        latest_open_time_ms=latest_open_time_ms,
        expected_latest_open_time_ms=expected_latest_open_time_ms,
        quality_check_id=record_id(quality_record),
        event_log_id=record_id(event_log),
        lock_key=lock_key,
        details={
            **_base_details(
                request,
                status=status.value,
                checked_count=report.checked_count,
                issue_count=report.issue_count,
                latest_open_time_ms=latest_open_time_ms,
                expected_latest_open_time_ms=expected_latest_open_time_ms,
                lock_key=lock_key,
            ),
            "issues": [issue.to_dict() for issue in report.issues],
            "report": report.to_dict(),
            "data_quality_check_id": record_id(quality_record) or "",
        },
    )


def _result_status_from_report(report: KlineQualityReport) -> DailyKline1dIntegrityStatus:
    if report.passed:
        return DailyKline1dIntegrityStatus.HEALTHY
    if report.first_issue and report.first_issue.issue_type == KlineQualityIssueType.EMPTY_BATCH:
        return DailyKline1dIntegrityStatus.BLOCKED
    if report.severity == KlineQualitySeverity.WARNING:
        return DailyKline1dIntegrityStatus.WARNING
    return DailyKline1dIntegrityStatus.FAILED


def _result_message(
    status: DailyKline1dIntegrityStatus,
    first_issue: KlineQualityIssue | None,
) -> str:
    if status == DailyKline1dIntegrityStatus.HEALTHY:
        return "1d daily Kline integrity check passed"
    if status == DailyKline1dIntegrityStatus.WARNING:
        return first_issue.message if first_issue else "1d daily Kline integrity check warning"
    if status == DailyKline1dIntegrityStatus.BLOCKED:
        return first_issue.message if first_issue else "1d daily Kline integrity check blocked"
    return first_issue.message if first_issue else "1d daily Kline integrity check failed"


def _create_running_event(
    collector_repository: Any,
    db_session: Any,
    request: DailyKline1dIntegrityCheckRequest,
    *,
    lock_key: str,
) -> Any:
    event_log = collector_repository.create_running_event(
        db_session,
        event_type=KLINE_1D_INTEGRITY_EVENT_TYPE,
        symbol=request.symbol,
        interval_value=request.interval_value,
        trigger_source=request.check_trigger,
        data_source=request.data_source,
        requested_start_open_time_ms=0,
        requested_end_open_time_ms=0,
        requested_count=request.requested_count,
        trace_id=request.trace_id,
        details={"lock_key": lock_key, "read_only": True, "check_type": CHECK_TYPE_DAILY_KLINE_1D_INTEGRITY},
    )
    _commit_if_possible(db_session)
    return event_log


def _mark_integrity_event(
    collector_repository: Any,
    db_session: Any,
    event_log: Any,
    result: DailyKline1dIntegrityCheckResult,
    *,
    report: KlineQualityReport,
    quality_check_id: int | None,
) -> None:
    status, severity = _event_status_and_severity(result.status)
    _mark_event_status(
        collector_repository,
        db_session,
        event_log,
        status=status,
        severity=severity,
        parsed_count=report.checked_count,
        closed_count=report.checked_count,
        issue_count=report.issue_count,
        actual_start_open_time_ms=report.start_open_time_ms,
        actual_end_open_time_ms=report.end_open_time_ms,
        quality_check_id=quality_check_id,
        first_issue_type=result.first_issue_type,
        first_issue_message=result.first_issue_message,
        report_json=report.to_dict(),
        details={
            "read_only": True,
            "status": result.status.value,
            "latest_open_time_ms": result.latest_open_time_ms or "",
            "expected_latest_open_time_ms": result.expected_latest_open_time_ms or "",
            "formal_write_performed": False,
        },
    )


def _mark_failed_event_for_exception(
    collector_repository: Any,
    db_session: Any,
    event_log: Any,
    result: DailyKline1dIntegrityCheckResult,
    *,
    error: Exception,
) -> None:
    _mark_event_status(
        collector_repository,
        db_session,
        event_log,
        status="failed",
        severity="critical",
        issue_count=1,
        first_issue_type=result.first_issue_type,
        first_issue_message=result.first_issue_message,
        error_code=error.__class__.__name__,
        error_message=str(error),
        details={"read_only": True, "formal_write_performed": False},
    )


def _mark_event_status(collector_repository: Any, db_session: Any, event_log: Any, **values: Any) -> None:
    if hasattr(collector_repository, "mark_event_status"):
        collector_repository.mark_event_status(db_session, event_log, **values)
        return
    status = str(values.get("status") or "")
    if status == "success" and hasattr(collector_repository, "mark_success"):
        collector_repository.mark_success(db_session, event_log, **values)
    elif status == "blocked" and hasattr(collector_repository, "mark_blocked"):
        collector_repository.mark_blocked(db_session, event_log, **values)
    elif hasattr(collector_repository, "mark_failed"):
        collector_repository.mark_failed(db_session, event_log, **values)


def _event_status_and_severity(status: DailyKline1dIntegrityStatus) -> tuple[str, str]:
    if status == DailyKline1dIntegrityStatus.HEALTHY:
        return "success", "info"
    if status == DailyKline1dIntegrityStatus.WARNING:
        return "warning", "warning"
    if status == DailyKline1dIntegrityStatus.BLOCKED:
        return "blocked", "warning"
    return "failed", "error"


def _send_integrity_alert_and_adjust_result(
    result: DailyKline1dIntegrityCheckResult,
    *,
    request: DailyKline1dIntegrityCheckRequest,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    quality_repository: Any | None,
    quality_record: Any | None,
) -> DailyKline1dIntegrityCheckResult:
    alert_result = _send_integrity_alert_safely(
        result,
        request=request,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
    )
    _commit_if_possible(db_session)
    if (
        alert_result.status == AlertSendStatus.SUBMITTED_TO_HERMES
        and quality_repository is not None
        and quality_record is not None
        and hasattr(quality_repository, "mark_quality_check_alert_sent")
    ):
        try:
            quality_repository.mark_quality_check_alert_sent(db_session, quality_record)
            _commit_if_possible(db_session)
        except Exception:  # noqa: BLE001 - alert was already submitted; expose only in logs.
            LOGGER.exception("Failed to mark 1d integrity quality alert sent trace_id=%s", request.trace_id)
            _rollback_if_possible(db_session)
    exit_code = EXIT_ALERT_FAILED if _alert_submission_failed(alert_result) else result.exit_code
    return replace(
        result,
        exit_code=exit_code,
        alert_status=alert_result.status.value,
        details={**dict(result.details), "alert_error": alert_result.error_message},
    )


def _send_integrity_alert_safely(
    result: DailyKline1dIntegrityCheckResult,
    *,
    request: DailyKline1dIntegrityCheckRequest,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> AlertSendResult:
    try:
        active_sender = alert_sender or _default_alert_sender()
        active_repository = alert_repository or _default_alert_repository()
        return active_sender(
            build_daily_1d_integrity_alert_event(result, request=request),
            repository=active_repository,
            db_session=db_session,
            send_real_alert=True,
        )
    except Exception as exc:  # noqa: BLE001 - report Hermes failure without changing Klines.
        LOGGER.exception("1d daily integrity alert submission raised trace_id=%s", request.trace_id)
        return AlertSendResult(
            status=AlertSendStatus.SUBMIT_FAILED,
            error_message=str(exc),
            attempted_real_send=True,
        )


def _should_send_integrity_alert(
    request: DailyKline1dIntegrityCheckRequest,
    result: DailyKline1dIntegrityCheckResult,
) -> bool:
    if result.status == DailyKline1dIntegrityStatus.HEALTHY:
        return request.notify_success
    return result.status != DailyKline1dIntegrityStatus.SKIPPED


def _build_task_failure_result(
    request: DailyKline1dIntegrityCheckRequest,
    *,
    error: Exception,
    event_log: Any | None,
    lock_key: str,
    report: KlineQualityReport | None,
) -> DailyKline1dIntegrityCheckResult:
    first_issue = report.first_issue if report is not None else None
    first_issue_type = first_issue.issue_type.value if first_issue else KlineQualityIssueType.TASK_ERROR.value
    first_issue_message = first_issue.message if first_issue else str(error)
    return DailyKline1dIntegrityCheckResult(
        status=DailyKline1dIntegrityStatus.ERROR,
        exit_code=EXIT_TASK_FAILED,
        trace_id=request.trace_id,
        message="1d daily Kline integrity check task failed",
        requested_count=request.requested_count,
        checked_count=report.checked_count if report is not None else 0,
        issue_count=report.issue_count if report is not None else 1,
        first_issue_type=first_issue_type,
        first_issue_message=first_issue_message,
        checked_start_time=_datetime_to_text(report.start_open_time_utc if report else None),
        checked_end_time=_datetime_to_text(report.end_open_time_utc if report else None),
        event_log_id=record_id(event_log),
        lock_key=lock_key,
        details=_base_details(
            request,
            status="error",
            checked_count=report.checked_count if report else 0,
            issue_count=report.issue_count if report else 1,
            error_code=error.__class__.__name__,
            error_message=str(error),
            lock_key=lock_key,
            range_unavailable_reason="任务执行异常，未能确认检查范围",
        ),
    )


def _base_details(request: DailyKline1dIntegrityCheckRequest, *, status: str, **extra: Any) -> dict[str, Any]:
    details = {
        "event_type": KLINE_1D_INTEGRITY_EVENT_TYPE,
        "check_type": CHECK_TYPE_DAILY_KLINE_1D_INTEGRITY,
        "symbol": request.symbol,
        "interval_value": request.interval_value,
        "check_trigger": request.check_trigger,
        "trigger_source": request.check_trigger,
        "data_source": request.data_source,
        "lookback_count": request.lookback_count,
        "status": status,
        "read_only": True,
        "no_repair_performed": True,
        "formal_write_performed": False,
        "action": "read_only_no_repair_no_backfill_no_market_kline_write",
    }
    details.update(extra)
    return details


def _issue(
    issue_type: KlineQualityIssueType,
    severity: KlineQualitySeverity,
    message: str,
    **values: Any,
) -> KlineQualityIssue:
    return KlineQualityIssue(
        issue_type=issue_type,
        severity=severity,
        message=message,
        **values,
    )


def _expected_latest_closed_1d_open_time(current_time_utc: datetime) -> int:
    current_ms = utc_datetime_to_timestamp_ms(_ensure_utc(current_time_utc))
    current_day_open_time_ms = (current_ms // KLINE_1D_INTERVAL_MS) * KLINE_1D_INTERVAL_MS
    return current_day_open_time_ms - KLINE_1D_INTERVAL_MS


def _row_utc_from_ms(row: Any, field_name: str) -> datetime:
    timestamp_ms = int(getattr(row, field_name))
    return timestamp_ms_to_utc_datetime(timestamp_ms)


def _row_prc_from_ms(row: Any, field_name: str) -> datetime:
    return utc_aware_to_prc_aware(_row_utc_from_ms(row, field_name))


def _decimal_or_none(row: Any, field_name: str) -> Decimal | None:
    value = getattr(row, field_name, None)
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _int_or_none(row: Any, field_name: str) -> int | None:
    value = getattr(row, field_name, None)
    return int(value) if value is not None else None


def _highest_issue_severity(issues: Sequence[KlineQualityIssue]) -> KlineQualitySeverity:
    if any(issue.severity == KlineQualitySeverity.CRITICAL for issue in issues):
        return KlineQualitySeverity.CRITICAL
    if any(issue.severity == KlineQualitySeverity.ERROR for issue in issues):
        return KlineQualitySeverity.ERROR
    if any(issue.severity == KlineQualitySeverity.WARNING for issue in issues):
        return KlineQualitySeverity.WARNING
    return KlineQualitySeverity.INFO


def _metadata_int(report: KlineQualityReport, key: str) -> int | None:
    value = report.metadata.get(key)
    return int(value) if isinstance(value, int) else None


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _alert_submission_failed(result: AlertSendResult) -> bool:
    return result.status != AlertSendStatus.SUBMITTED_TO_HERMES


def _commit_if_possible(db_session: Any) -> None:
    if hasattr(db_session, "commit"):
        db_session.commit()


def _rollback_if_possible(db_session: Any) -> None:
    if hasattr(db_session, "rollback"):
        db_session.rollback()


def _release_integrity_lock_safely(task_lock: Any, *, key: str, owner: str) -> None:
    try:
        task_lock.release_lock(key=key, owner=owner)
    except Exception:  # noqa: BLE001 - TTL is the remaining safety net.
        LOGGER.exception("Failed to release 1d daily integrity lock key=%s trace_id=%s", key, owner)


def _default_kline_1d_repository() -> Any:
    from app.storage.mysql.repositories.market_kline_1d_repository import MarketKline1dRepository

    return MarketKline1dRepository()


def _default_data_quality_repository() -> Any:
    from app.storage.mysql.repositories.data_quality_check_repository import DataQualityCheckRepository

    return DataQualityCheckRepository()


def _default_collector_event_repository() -> Any:
    from app.storage.mysql.repositories.collector_event_log_repository import CollectorEventLogRepository

    return CollectorEventLogRepository()


def _default_alert_repository() -> Any:
    from app.storage.mysql.repositories.alert_message_repository import AlertMessageRepository

    return AlertMessageRepository()


def _default_alert_sender() -> Any:
    from app.alerting.service import send_alert

    return send_alert


__all__ = [
    "DailyKline1dIntegrityParameterError",
    "run_daily_1d_kline_integrity_check",
    "validate_daily_1d_kline_integrity_request",
]
