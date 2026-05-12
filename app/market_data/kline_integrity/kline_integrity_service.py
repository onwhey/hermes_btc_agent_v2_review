"""Daily BTCUSDT 4h Kline integrity review service.

Call chain for manual debugging:
scripts/check_kline_integrity.py::main
    -> app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check
    -> app/core/task_lock.py::RedisTaskLock.acquire_lock
    -> app/market_data/kline_quality/service.py::run_recent_kline_integrity_check
    -> app/market_data/kline_quality/integrity_checker.py::run_recent_kline_integrity_check
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
    -> app/storage/mysql/repositories/market_kline_4h_repository.py::list_by_time_range
    -> app/storage/mysql/repositories/data_quality_check_repository.py::create_quality_check_record
    -> app/alerting/service.py::send_alert

Call chain for scheduling:
app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job
    -> app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check

This file belongs to `app/market_data/kline_integrity`.
It orchestrates the phase-11 daily review of official Binance REST closed 4h
Klines against `market_kline_4h`. It may request Binance public REST Klines,
read `market_kline_4h`, write `data_quality_check`, optionally write
`alert_message` through `app/alerting`, acquire a short Redis re-entry lock,
and send fixed-template Hermes alerts.
It does not write, overwrite, delete, repair, or backfill formal Kline rows. It
does not use Redis for market data, call DeepSeek, generate strategy advice, or perform
any trading execution.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.task_lock import RedisTaskLock
from app.core.logger import get_logger
from app.core.time_utils import now_utc
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_4H_INTERVAL_VALUE
from app.market_data.kline_integrity.types import (
    ALLOWED_CHECK_MODES,
    EXIT_ALERT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    DailyKlineIntegrityCheckRequest,
    DailyKlineIntegrityCheckResult,
    DailyKlineIntegrityStatus,
    build_kline_integrity_check_lock_key,
)
from app.market_data.kline_integrity.results import (
    datetime_to_text,
    format_daily_kline_integrity_result_lines,
    record_id,
    result_from_quality_report,
)
from app.market_data.kline_quality.service import (
    record_quality_check_result,
    run_recent_kline_integrity_check,
    send_quality_alert_if_needed,
    send_quality_task_failure_alert,
)
from app.market_data.kline_quality.types import (
    CHECK_TRIGGER_SOURCE_CLI,
    CHECK_TRIGGER_SOURCE_SCHEDULER,
    CHECK_TYPE_DAILY_KLINE_INTEGRITY,
    KlineQualityIssue,
    KlineQualityIssueType,
    KlineQualityReport,
    KlineQualitySeverity,
    KlineQualityStatus,
)

LOGGER = get_logger("market_data.kline_integrity.daily")
ALLOWED_DAILY_TRIGGER_SOURCES = frozenset({CHECK_TRIGGER_SOURCE_CLI, CHECK_TRIGGER_SOURCE_SCHEDULER})


class DailyKlineIntegrityParameterError(ValueError):
    """Raised when a daily review request is invalid before external access."""


def run_daily_kline_integrity_check(
    request: DailyKlineIntegrityCheckRequest,
    *,
    db_session: Any,
    binance_client: Any | None = None,
    kline_repository: Any | None = None,
    data_quality_repository: Any | None = None,
    alert_sender: Any | None = None,
    alert_repository: Any | None = None,
    task_lock: Any | None = None,
) -> DailyKlineIntegrityCheckResult:
    """Run one daily official-vs-database 4h Kline review.

    Parameters: `request` contains symbol, interval, recent closed Kline count,
    trigger source, success-notification choice, and trace id. Dependencies are
    injectable for mock tests.
    Return value: a plain result with exit code, checked range, issue summary,
    optional quality record id, and alert status.
    Failure scenarios: Binance, parser, database read/write, and Hermes failures
    become explicit error or alert-failed results with best-effort fixed-template
    alerts. Parameter errors return exit code 1 without external access.
    External service access: Binance public REST and Hermes only through injected
    or default clients.
    Data impact: reads `market_kline_4h`; writes only `data_quality_check` and
    optional `alert_message`; writes Redis only for the owner-checked re-entry lock.
    It never writes formal Kline rows.
    """

    try:
        validate_daily_kline_integrity_request(request)
    except DailyKlineIntegrityParameterError as exc:
        return DailyKlineIntegrityCheckResult(
            status=DailyKlineIntegrityStatus.ERROR,
            exit_code=EXIT_PARAMETER_ERROR,
            trace_id=request.trace_id,
            message=str(exc),
            requested_count=request.requested_count,
            details={"error_code": "parameter_error"},
        )

    started_at = now_utc()
    lock_key = build_kline_integrity_check_lock_key(
        symbol=request.symbol,
        interval_value=request.interval_value,
        check_mode=request.check_mode,
    )
    database_state = {"entered": False}
    tracking_repository = _DatabaseReadTrackingRepository(kline_repository, database_state)
    active_quality_repository = data_quality_repository or _default_data_quality_repository()
    active_task_lock = task_lock or RedisTaskLock()
    quality_record: Any | None = None
    final_report: KlineQualityReport | None = None
    lock_acquired = False

    try:
        lock_acquired = active_task_lock.acquire_lock(
            key=lock_key,
            owner=request.trace_id,
            ttl_seconds=request.lock_ttl_seconds,
        )
        if not lock_acquired:
            LOGGER.warning(
                "Daily Kline integrity check skipped because lock is held key=%s trace_id=%s",
                lock_key,
                request.trace_id,
            )
            return DailyKlineIntegrityCheckResult(
                status=DailyKlineIntegrityStatus.SKIPPED,
                exit_code=EXIT_SUCCESS,
                trace_id=request.trace_id,
                message="Daily Kline integrity check skipped because another task holds the lock",
                requested_count=request.requested_count,
                lock_key=lock_key,
                details={
                    "lock_key": lock_key,
                    "check_mode": request.check_mode,
                    "check_trigger": request.check_trigger,
                    "lock_acquired": False,
                },
            )

        report = run_recent_kline_integrity_check(
            db_session,
            symbol=request.symbol,
            interval_value=request.interval_value,
            limit=request.lookback_count,
            check_trigger_source=request.check_trigger_source,
            binance_client=binance_client,
            kline_repository=tracking_repository,
            quality_repository=active_quality_repository,
            record_result=False,
            send_alert=False,
            check_type=CHECK_TYPE_DAILY_KLINE_INTEGRITY,
            enforce_database_source_rules=True,
        )
        final_report = _with_daily_metadata(
            report,
            request=request,
            started_at=started_at,
            finished_at=now_utc(),
            data_quality_check_id=None,
        )
        quality_record = record_quality_check_result(
            db_session,
            final_report,
            repository=active_quality_repository,
        )
        final_report = _with_daily_metadata(
            final_report,
            request=request,
            started_at=started_at,
            finished_at=now_utc(),
            data_quality_check_id=record_id(quality_record),
        )

        result = result_from_quality_report(
            request,
            final_report,
            quality_record=quality_record,
        )
        result = replace(
            result,
            lock_key=lock_key,
            details={
                **dict(result.details),
                "lock_key": lock_key,
                "check_mode": request.check_mode,
                "check_trigger": request.check_trigger,
                "lookback_count": request.lookback_count,
            },
        )
        if final_report.passed and not request.notify_success:
            return result
        return _send_report_alert_and_adjust_result(
            result,
            final_report,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
            data_quality_repository=active_quality_repository,
            quality_record=quality_record,
        )
    except Exception as exc:  # noqa: BLE001 - health could not be confirmed, so alert.
        LOGGER.exception("Daily Kline integrity check failed trace_id=%s", request.trace_id)
        _rollback_if_possible(db_session)
        quality_record_failed = False
        quality_record_error = ""
        try:
            error_report = _build_task_error_quality_report(
                request,
                error_message=str(exc),
                started_at=started_at,
                finished_at=now_utc(),
                previous_report=final_report,
            )
            quality_record = record_quality_check_result(
                db_session,
                error_report,
                repository=active_quality_repository,
            )
        except Exception as quality_exc:  # noqa: BLE001 - still alert when quality recording fails.
            quality_record_failed = True
            quality_record_error = str(quality_exc)
            LOGGER.critical(
                "EMERGENCY daily Kline integrity failure record unavailable trace_id=%s error=%s",
                request.trace_id,
                quality_record_error,
            )
            LOGGER.exception(
                "Failed to record daily Kline integrity error report trace_id=%s",
                request.trace_id,
            )
            _rollback_if_possible(db_session)

        alert_result = _send_task_failure_alert_safely(
            request,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
            error_message=str(exc),
        )
        return DailyKlineIntegrityCheckResult(
            status=DailyKlineIntegrityStatus.ERROR,
            exit_code=EXIT_TASK_FAILED,
            trace_id=request.trace_id,
            message="Daily Kline health could not be confirmed",
            requested_count=request.requested_count,
            checked_count=final_report.checked_count if final_report else 0,
            issue_count=final_report.issue_count if final_report else 0,
            first_issue_type=final_report.first_issue.issue_type.value
            if final_report and final_report.first_issue
            else KlineQualityIssueType.TASK_ERROR.value,
            first_issue_message=final_report.first_issue.message
            if final_report and final_report.first_issue
            else str(exc),
            checked_start_time=datetime_to_text(final_report.start_open_time_utc if final_report else None),
            checked_end_time=datetime_to_text(final_report.end_open_time_utc if final_report else None),
            quality_check_id=record_id(quality_record),
            alert_status=_alert_status_text(alert_result),
            lock_key=lock_key,
            details={
                "error_code": exc.__class__.__name__,
                "error_message": str(exc),
                "database_read_started": database_state["entered"],
                "data_quality_check_record_failed": quality_record_failed,
                "data_quality_check_record_error": quality_record_error,
                "alert_error": alert_result.error_message if alert_result else "",
                "source": "Binance REST official klines",
                "lock_key": lock_key,
                "check_mode": request.check_mode,
                "action": "check_only_no_repair_no_backfill_no_market_kline_write",
            },
        )
    finally:
        if lock_acquired:
            _release_integrity_lock_safely(active_task_lock, key=lock_key, owner=request.trace_id)


def validate_daily_kline_integrity_request(request: DailyKlineIntegrityCheckRequest) -> None:
    """Validate daily review parameters before any Binance, MySQL, or Hermes access."""

    if not request.symbol.strip():
        raise DailyKlineIntegrityParameterError("symbol must not be empty")
    if request.symbol.strip().upper() != DEFAULT_KLINE_SYMBOL:
        raise DailyKlineIntegrityParameterError("daily Kline integrity check only supports BTCUSDT")
    if request.interval_value != KLINE_4H_INTERVAL_VALUE:
        raise DailyKlineIntegrityParameterError("interval must be 4h")
    if request.lookback_count <= 0:
        raise DailyKlineIntegrityParameterError("lookback_count must be greater than 0")
    if request.check_trigger not in ALLOWED_DAILY_TRIGGER_SOURCES:
        raise DailyKlineIntegrityParameterError("check_trigger must be cli or scheduler")
    if request.check_mode not in ALLOWED_CHECK_MODES:
        raise DailyKlineIntegrityParameterError("check_mode must be daily_integrity_check or manual_integrity_check")
    if request.lock_ttl_seconds <= 0:
        raise DailyKlineIntegrityParameterError("lock_ttl_seconds must be greater than 0")


class _DatabaseReadTrackingRepository:
    """Read-only repository proxy that records whether the review reached MySQL."""

    def __init__(self, wrapped_repository: Any | None, state: dict[str, bool]) -> None:
        self._wrapped_repository = wrapped_repository or _default_kline_repository()
        self._state = state

    def list_by_time_range(self, db_session: Any, **kwargs: Any) -> list[Any]:
        """Delegate the read and mark the daily task as having entered the database layer."""

        self._state["entered"] = True
        return self._wrapped_repository.list_by_time_range(db_session, **kwargs)


def _send_report_alert_and_adjust_result(
    result: DailyKlineIntegrityCheckResult,
    report: KlineQualityReport,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    data_quality_repository: Any,
    quality_record: Any | None,
) -> DailyKlineIntegrityCheckResult:
    alert_result = _send_report_alert_safely(
        report,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
    )
    alert_failed = _alert_delivery_failed(alert_result)
    if alert_result and alert_result.status == AlertSendStatus.SENT:
        _mark_quality_alert_sent_if_supported(
            data_quality_repository,
            db_session=db_session,
            quality_record=quality_record,
        )
    return replace(
        result,
        exit_code=EXIT_ALERT_FAILED if alert_failed else result.exit_code,
        alert_status=_alert_status_text(alert_result),
        details={
            **dict(result.details),
            "alert_error": alert_result.error_message if alert_result else "",
        },
    )


def _send_report_alert_safely(
    report: KlineQualityReport,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> AlertSendResult | None:
    try:
        return send_quality_alert_if_needed(
            report,
            alert_sender=alert_sender,
            send_success_alert=report.passed,
            send_real_alert=True,
            db_session=db_session,
            alert_repository=alert_repository,
        )
    except Exception as exc:  # noqa: BLE001 - expose Hermes failure without changing Kline data.
        LOGGER.exception("Daily Kline integrity alert delivery raised")
        return AlertSendResult(
            status=AlertSendStatus.FAILED,
            error_message=str(exc),
            attempted_real_send=True,
        )


def _send_task_failure_alert_safely(
    request: DailyKlineIntegrityCheckRequest,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    error_message: str,
) -> AlertSendResult | None:
    try:
        return send_quality_task_failure_alert(
            symbol=request.symbol,
            interval_value=request.interval_value,
            check_trigger_source=request.check_trigger_source,
            error_message=error_message,
            alert_sender=alert_sender,
            send_real_alert=True,
            db_session=db_session,
            alert_repository=alert_repository,
        )
    except Exception as exc:  # noqa: BLE001 - task result must still report alert failure.
        LOGGER.exception("Daily Kline integrity task failure alert raised")
        return AlertSendResult(
            status=AlertSendStatus.FAILED,
            error_message=str(exc),
            attempted_real_send=True,
        )


def _with_daily_metadata(
    report: KlineQualityReport,
    *,
    request: DailyKlineIntegrityCheckRequest,
    started_at: Any,
    finished_at: Any,
    data_quality_check_id: int | None,
) -> KlineQualityReport:
    metadata = {
        **dict(report.metadata),
        "trace_id": request.trace_id,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "source": "Binance REST official klines",
        "status": "healthy" if report.passed else "failed",
        "no_repair_performed": True,
        "action": "check_only_no_repair_no_backfill_no_market_kline_write",
    }
    if data_quality_check_id is not None:
        metadata["data_quality_check_id"] = data_quality_check_id
    return replace(report, metadata=metadata)


def _build_task_error_quality_report(
    request: DailyKlineIntegrityCheckRequest,
    *,
    error_message: str,
    started_at: Any,
    finished_at: Any,
    previous_report: KlineQualityReport | None,
) -> KlineQualityReport:
    issue = KlineQualityIssue(
        issue_type=KlineQualityIssueType.TASK_ERROR,
        severity=KlineQualitySeverity.CRITICAL,
        message=error_message,
        field_name="daily_kline_integrity_task",
    )
    return KlineQualityReport(
        check_type=CHECK_TYPE_DAILY_KLINE_INTEGRITY,
        symbol=request.symbol,
        interval_value=request.interval_value,
        check_trigger_source=request.check_trigger_source,
        status=KlineQualityStatus.ERROR,
        severity=KlineQualitySeverity.CRITICAL,
        checked_count=previous_report.checked_count if previous_report else 0,
        issues=(issue,),
        start_open_time_ms=previous_report.start_open_time_ms if previous_report else None,
        start_open_time_utc=previous_report.start_open_time_utc if previous_report else None,
        start_open_time_prc=previous_report.start_open_time_prc if previous_report else None,
        end_open_time_ms=previous_report.end_open_time_ms if previous_report else None,
        end_open_time_utc=previous_report.end_open_time_utc if previous_report else None,
        end_open_time_prc=previous_report.end_open_time_prc if previous_report else None,
        writable_klines=(),
        metadata={
            "trace_id": request.trace_id,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "source": "Binance REST official klines",
            "status": "error",
            "no_repair_performed": True,
            "action": "check_only_no_repair_no_backfill_no_market_kline_write",
        },
    )


def _mark_quality_alert_sent_if_supported(
    repository: Any,
    *,
    db_session: Any,
    quality_record: Any | None,
) -> None:
    if quality_record is None or not hasattr(repository, "mark_quality_check_alert_sent"):
        return
    try:
        repository.mark_quality_check_alert_sent(db_session, quality_record)
    except Exception:  # noqa: BLE001 - alert delivery already happened; preserve task result.
        LOGGER.exception("Failed to mark daily Kline integrity quality record alert_sent")


def _alert_delivery_failed(result: AlertSendResult | None) -> bool:
    return result is not None and result.status != AlertSendStatus.SENT


def _alert_status_text(result: AlertSendResult | None) -> str | None:
    return result.status.value if result is not None else None


def _rollback_if_possible(db_session: Any) -> None:
    if hasattr(db_session, "rollback"):
        db_session.rollback()


def _default_kline_repository() -> Any:
    from app.storage.mysql.repositories.market_kline_4h_repository import MarketKline4hRepository

    return MarketKline4hRepository()


def _default_data_quality_repository() -> Any:
    from app.storage.mysql.repositories.data_quality_check_repository import DataQualityCheckRepository

    return DataQualityCheckRepository()


__all__ = [
    "format_daily_kline_integrity_result_lines",
    "run_daily_kline_integrity_check",
    "validate_daily_kline_integrity_request",
]
