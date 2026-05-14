"""Fixed-template Hermes alert helpers for phase-09 incremental collection.

This file belongs to `app/market_data/collector`.
It builds fixed alert events for incremental collector success/failure and
delegates sending to `app/alerting/service.py`. It does not request Binance,
write formal Klines, write Redis, call DeepSeek, generate advice, repair data,
schedule jobs, or trade.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.alerting.types import AlertEvent, AlertSendResult, AlertSendStatus, AlertSeverity, AlertType
from app.core.logger import get_logger
from app.market_data.collector.types import (
    COLLECTOR_EVENT_TYPE,
    EXIT_ALERT_FAILED,
    IncrementalKlineCollectRequest,
    IncrementalKlineCollectResult,
    KlineCollectStatus,
)
from app.market_data.kline_quality.report_formatter import format_quality_report_summary
from app.market_data.kline_quality.types import KlineQualityReport

LOGGER = get_logger("market_data.collector.alerts")


def send_collect_failure_alert_and_adjust_exit_code(
    request: IncrementalKlineCollectRequest,
    result: IncrementalKlineCollectResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    report: KlineQualityReport | None = None,
) -> IncrementalKlineCollectResult:
    """Send mandatory failure alert and return exit 3 when delivery fails."""

    alert_result = _send_collect_alert(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=report,
        success=False,
    )
    _commit_if_possible(db_session)
    if alert_result.status != AlertSendStatus.SUBMITTED_TO_HERMES:
        LOGGER.error(
            "Incremental collector failure alert submission to Hermes failed trace_id=%s status=%s error=%s",
            request.trace_id,
            alert_result.status.value,
            alert_result.error_message,
        )
        return _replace_result_alert(result, alert_result, exit_code=EXIT_ALERT_FAILED)
    return _replace_result_alert(result, alert_result)


def send_collect_success_alert_and_adjust_exit_code(
    request: IncrementalKlineCollectRequest,
    result: IncrementalKlineCollectResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> IncrementalKlineCollectResult:
    """Send optional success alert and return exit 3 when delivery fails."""

    alert_result = _send_collect_alert(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=None,
        success=True,
    )
    _commit_if_possible(db_session)
    if alert_result.status != AlertSendStatus.SUBMITTED_TO_HERMES:
        return _replace_result_alert(result, alert_result, exit_code=EXIT_ALERT_FAILED)
    return _replace_result_alert(result, alert_result)


def _send_collect_alert(
    request: IncrementalKlineCollectRequest,
    result: IncrementalKlineCollectResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    report: KlineQualityReport | None,
    success: bool,
) -> AlertSendResult:
    active_alert_sender = alert_sender or _default_alert_sender()
    active_alert_repository = alert_repository or _default_alert_repository()
    event = _build_collect_alert_event(request, result, report=report, success=success)
    return active_alert_sender(
        event,
        repository=active_alert_repository,
        db_session=db_session,
        send_real_alert=True,
    )


def _build_collect_alert_event(
    request: IncrementalKlineCollectRequest,
    result: IncrementalKlineCollectResult,
    *,
    report: KlineQualityReport | None,
    success: bool,
) -> AlertEvent:
    if success:
        return AlertEvent(
            alert_type=AlertType.KLINE_INTEGRITY_CHECK_PASSED,
            severity=AlertSeverity.INFO,
            title="4h Kline incremental collection succeeded",
            summary="Official Binance REST 4h Kline incremental collection completed successfully",
            details=_alert_details(request, result, report),
            source="app.market_data.collector.alerts",
            trace_id=request.trace_id,
        )
    alert_type = (
        AlertType.KLINE_DATA_QUALITY_ERROR
        if result.status == KlineCollectStatus.BLOCKED
        else AlertType.COLLECTOR_ERROR
    )
    severity = AlertSeverity.CRITICAL if result.status == KlineCollectStatus.FAILED else AlertSeverity.ERROR
    return AlertEvent(
        alert_type=alert_type,
        severity=severity,
        title="4h Kline incremental collection did not complete",
        summary=result.first_issue_message or result.message,
        details=_alert_details(request, result, report),
        source="app.market_data.collector.alerts",
        trace_id=request.trace_id,
    )


def _alert_details(
    request: IncrementalKlineCollectRequest,
    result: IncrementalKlineCollectResult,
    report: KlineQualityReport | None,
) -> dict[str, object]:
    return {
        "event_type": COLLECTOR_EVENT_TYPE,
        "symbol": request.symbol,
        "interval_value": request.interval_value,
        "trigger_source": request.trigger_source,
        "data_source": request.data_source,
        "dry_run": request.dry_run,
        "formal_write_performed": bool(result.details.get("formal_write_performed", result.inserted_count > 0)),
        "requested_closed_limit": request.limit,
        "status": result.status.value,
        "requested_count": result.requested_count,
        "fetched_count": result.fetched_count,
        "parsed_count": result.parsed_count,
        "closed_count": result.closed_count,
        "writable_count": result.writable_count,
        "inserted_count": result.inserted_count,
        "skipped_existing_count": result.skipped_existing_count,
        "issue_count": result.issue_count,
        "first_issue_type": result.first_issue_type or "",
        "first_issue_message": result.first_issue_message or "",
        "quality_summary": format_quality_report_summary(report) if report is not None else "",
        "action": "no_auto_repair_no_human_field_edit_no_extra_backfill_no_market_kline_overwrite",
    }


def _replace_result_alert(
    result: IncrementalKlineCollectResult,
    alert_result: AlertSendResult,
    *,
    exit_code: int | None = None,
) -> IncrementalKlineCollectResult:
    return replace(
        result,
        exit_code=result.exit_code if exit_code is None else exit_code,
        alert_status=alert_result.status.value,
    )


def _commit_if_possible(db_session: Any) -> None:
    if hasattr(db_session, "commit"):
        db_session.commit()


def _default_alert_repository() -> Any:
    from app.storage.mysql.repositories.alert_message_repository import AlertMessageRepository

    return AlertMessageRepository()


def _default_alert_sender() -> Any:
    from app.alerting.service import send_alert

    return send_alert
