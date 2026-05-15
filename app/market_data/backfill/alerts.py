"""Fixed-template Hermes alert helpers for manual 4h backfill.

This file belongs to `app/market_data/backfill`.
It builds fixed alert events for manual backfill success/failure and delegates
sending to `app/alerting/service.py`. It does not request Binance, write formal
Klines, write Redis, call DeepSeek, generate advice, repair data, or trade.
"""

from __future__ import annotations

from typing import Any

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSendResult, AlertSendStatus, AlertSeverity, AlertType
from app.core.logger import get_logger
from app.core.time_utils import timestamp_ms_to_utc_datetime
from app.market_data.kline_constants import KLINE_4H_INTERVAL_MS
from app.market_data.backfill.types import (
    BACKFILL_EVENT_TYPE,
    EXIT_ALERT_FAILED,
    KlineBackfillStatus,
    ManualKlineBackfillRequest,
    ManualKlineBackfillResult,
)
from app.market_data.kline_quality.report_formatter import format_quality_report_summary
from app.market_data.kline_quality.types import KlineQualityIssueType, KlineQualityReport

LOGGER = get_logger("market_data.backfill.alerts")

_INTERNAL_CONTEXT_DETAIL_KEY = "_internal_context"


def send_failure_alert_and_adjust_exit_code(
    request: ManualKlineBackfillRequest,
    result: ManualKlineBackfillResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    report: KlineQualityReport | None = None,
) -> ManualKlineBackfillResult:
    """Send mandatory failure alert and return exit 3 when delivery fails."""

    alert_result = _send_backfill_alert(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=report,
        success=False,
    )
    _commit_if_possible(db_session)
    if _alert_submission_failed(alert_result):
        LOGGER.error(
            "Manual backfill failure alert submission to Hermes failed trace_id=%s status=%s error=%s",
            request.trace_id,
            alert_result.status.value,
            alert_result.error_message,
        )
        return _replace_result_alert(result, alert_result, exit_code=EXIT_ALERT_FAILED)
    return _replace_result_alert(result, alert_result)


def send_success_alert_and_adjust_exit_code(
    request: ManualKlineBackfillRequest,
    result: ManualKlineBackfillResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> ManualKlineBackfillResult:
    """Send optional success alert and return exit 3 when delivery fails."""

    alert_result = _send_backfill_alert(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=None,
        success=True,
    )
    _commit_if_possible(db_session)
    if _alert_submission_failed(alert_result):
        return _replace_result_alert(result, alert_result, exit_code=EXIT_ALERT_FAILED)
    return _replace_result_alert(result, alert_result)


def _send_backfill_alert(
    request: ManualKlineBackfillRequest,
    result: ManualKlineBackfillResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    report: KlineQualityReport | None,
    success: bool,
) -> AlertSendResult:
    active_alert_sender = alert_sender or _default_alert_sender()
    active_alert_repository = alert_repository or _default_alert_repository()
    event = _build_backfill_alert_event(request, result, report=report, success=success)
    return active_alert_sender(
        event,
        repository=active_alert_repository,
        db_session=db_session,
        send_real_alert=True,
    )


def _build_backfill_alert_event(
    request: ManualKlineBackfillRequest,
    result: ManualKlineBackfillResult,
    *,
    report: KlineQualityReport | None,
    success: bool,
) -> AlertEvent:
    if success:
        title = "手动补 K 已完成"
        summary = "手动 4h K线回补已完成。"
        if request.dry_run:
            title = "手动补 K 预演检查（dry-run）通过"
            summary = "手动补 K 预演检查（dry-run）通过，正式 K线表未被修改。"
        return AlertEvent(
            alert_type=AlertType.KLINE_INTEGRITY_CHECK_PASSED,
            severity=AlertSeverity.INFO,
            title=title,
            summary=summary,
            details=_compact_alert_details(
                request,
                result,
                report,
                reason=summary,
                result_text=_success_result_text(request, result),
                suggestion="无需处理。可通过采集事件日志 collector_event_log 和追踪ID排查本次任务。",
            ),
            source="app.market_data.backfill.alerts",
            trace_id=request.trace_id,
        )
    if _is_safe_manual_unclosed_kline_block(request, result, report):
        unclosed_open_time_ms = _first_unclosed_open_time_ms(report) or request.end_open_time_ms
        return AlertEvent(
            alert_type=AlertType.MANUAL_BACKFILL_NOTICE,
            severity=AlertSeverity.NOTICE,
            title="手动补 K 已安全阻断",
            summary="请求区间包含尚未收盘的 4h K线，系统已阻断写入。",
            details=_compact_alert_details(
                request,
                result,
                report,
                reason=(
                    "请求区间包含尚未收盘的 4h K线："
                    f"{_format_open_time_utc(unclosed_open_time_ms)}。"
                ),
                result_text="系统已阻断写入，正式 K线表未被修改。",
                suggestion=_unclosed_kline_retry_suggestion(unclosed_open_time_ms),
            ),
            source="app.market_data.backfill.alerts",
            trace_id=request.trace_id,
        )

    alert_type = (
        AlertType.KLINE_DATA_QUALITY_ERROR
        if result.status == KlineBackfillStatus.BLOCKED
        else AlertType.COLLECTOR_ERROR
    )
    severity = AlertSeverity.CRITICAL if result.status == KlineBackfillStatus.FAILED else AlertSeverity.ERROR
    title = (
        "手动补 K 被质量检查阻断"
        if result.status == KlineBackfillStatus.BLOCKED
        else "手动补 K 执行失败"
    )
    return AlertEvent(
        alert_type=alert_type,
        severity=severity,
        title=title,
        summary=_public_failure_summary(result),
        details=_compact_alert_details(
            request,
            result,
            report,
            reason=_public_failure_reason(result),
            result_text="系统已停止本次写入，正式 K线表未被本次任务修改。",
            suggestion=_failure_retry_suggestion(result),
        ),
        source="app.market_data.backfill.alerts",
        trace_id=request.trace_id,
    )


def _compact_alert_details(
    request: ManualKlineBackfillRequest,
    result: ManualKlineBackfillResult,
    report: KlineQualityReport | None,
    *,
    reason: str,
    result_text: str,
    suggestion: str,
) -> dict[str, object]:
    """Build the short WeChat body while preserving internal context off-screen.

    The visible body is intentionally Chinese and action-oriented. Raw counters,
    source fields, and quality summaries remain in the hidden structured context
    and in collector_event_log/data_quality_check records; they are not rendered
    into the WeChat message.
    """

    body = "\n".join(
        [
            f"币种周期：{request.symbol} {request.interval_value}",
            f"请求区间：{_format_request_range_utc(request)}",
            "",
            "原因：",
            reason,
            "",
            "结果：",
            result_text,
            "",
            "建议：",
            suggestion,
            "",
            f"追踪ID：{request.trace_id}",
        ]
    )
    return {
        WECHAT_VISIBLE_BODY_DETAIL_KEY: body,
        _INTERNAL_CONTEXT_DETAIL_KEY: _internal_alert_context(request, result, report),
    }


def _internal_alert_context(
    request: ManualKlineBackfillRequest,
    result: ManualKlineBackfillResult,
    report: KlineQualityReport | None,
) -> dict[str, object]:
    formal_write_performed = bool(
        result.details.get(
            "formal_write_performed",
            (not request.dry_run and result.inserted_count > 0),
        )
    )
    return {
        "event_type": BACKFILL_EVENT_TYPE,
        "symbol": request.symbol,
        "interval_value": request.interval_value,
        "trigger_source": request.trigger_source,
        "data_source": request.data_source,
        "dry_run": request.dry_run,
        "formal_write_performed": formal_write_performed,
        "requested_start_open_time_ms": request.start_open_time_ms,
        "requested_end_open_time_ms": request.end_open_time_ms,
        "status": result.status.value,
        "requested_count": result.requested_count,
        "fetched_count": result.fetched_count,
        "parsed_count": result.parsed_count,
        "writable_count": result.writable_count,
        "inserted_count": result.inserted_count,
        "skipped_existing_count": result.skipped_existing_count,
        "issue_count": result.issue_count,
        "first_issue_type": result.first_issue_type or "",
        "first_issue_message": result.first_issue_message or "",
        "quality_summary": format_quality_report_summary(report) if report is not None else "",
        "action": "no_auto_repair_no_human_field_edit_no_extra_backfill_no_market_kline_overwrite",
    }


def _is_safe_manual_unclosed_kline_block(
    request: ManualKlineBackfillRequest,
    result: ManualKlineBackfillResult,
    report: KlineQualityReport | None,
) -> bool:
    """Return whether a manual CLI block is only the requested final unclosed Kline."""

    if result.status != KlineBackfillStatus.BLOCKED:
        return False
    if result.inserted_count != 0:
        return False
    if _formal_write_performed(request, result):
        return False
    if report is None or not report.issues:
        return False
    issue_open_times = [issue.open_time_ms for issue in report.issues]
    if any(issue.issue_type != KlineQualityIssueType.UNCLOSED_KLINE for issue in report.issues):
        return False
    return set(issue_open_times) == {request.end_open_time_ms}


def _formal_write_performed(
    request: ManualKlineBackfillRequest,
    result: ManualKlineBackfillResult,
) -> bool:
    return bool(
        result.details.get(
            "formal_write_performed",
            (not request.dry_run and result.inserted_count > 0),
        )
    )


def _first_unclosed_open_time_ms(report: KlineQualityReport | None) -> int | None:
    if report is None:
        return None
    for issue in report.issues:
        if issue.issue_type == KlineQualityIssueType.UNCLOSED_KLINE:
            return issue.open_time_ms
    return None


def _format_request_range_utc(request: ManualKlineBackfillRequest) -> str:
    return (
        f"{_format_open_time_utc(request.start_open_time_ms)} ~ "
        f"{_format_open_time_utc(request.end_open_time_ms)}"
    )


def _format_open_time_utc(open_time_ms: int | None) -> str:
    if open_time_ms is None:
        return "无法确认 UTC"
    return f"{timestamp_ms_to_utc_datetime(open_time_ms).strftime('%Y-%m-%d %H:%M')} UTC"


def _format_open_time_iso_utc(open_time_ms: int | None) -> str:
    if open_time_ms is None:
        return ""
    return timestamp_ms_to_utc_datetime(open_time_ms).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unclosed_kline_retry_suggestion(unclosed_open_time_ms: int) -> str:
    previous_open_time_ms = unclosed_open_time_ms - KLINE_4H_INTERVAL_MS
    if previous_open_time_ms >= 0:
        return (
            "如需重试，请将结束时间参数 end-utc 改为最近一根已收盘 K线，例如：\n"
            f"{_format_open_time_iso_utc(previous_open_time_ms)}"
        )
    return "请重新选择已收盘 K线区间后重试。"


def _success_result_text(
    request: ManualKlineBackfillRequest,
    result: ManualKlineBackfillResult,
) -> str:
    if request.dry_run:
        return "预演模式（dry-run）只完成请求、解析和质量检查，正式 K线表未被修改。"
    if result.inserted_count > 0:
        return f"系统已写入 {result.inserted_count} 根通过质量检查的已收盘 K线。"
    return "本次没有新增正式 K线，正式 K线表未被修改。"


def _public_failure_summary(result: ManualKlineBackfillResult) -> str:
    if result.status == KlineBackfillStatus.BLOCKED:
        return "手动 4h K线回补被质量规则阻断，系统没有写入正式 K线表。"
    return "手动 4h K线回补执行失败，系统没有继续写入正式 K线表。"


def _public_failure_reason(result: ManualKlineBackfillResult) -> str:
    reason_by_issue_type = {
        KlineQualityIssueType.BATCH_NOT_CONTINUOUS.value: "Binance 返回的历史 K线区间存在缺失、断档或不连续。",
        KlineQualityIssueType.DATABASE_NOT_CONTINUOUS.value: "回补区间与正式库已有相邻 K线无法连续衔接。",
        KlineQualityIssueType.DATABASE_CONFLICT.value: "正式库已有同一 open time 的 K线，但核心字段与 Binance 返回不一致。",
        KlineQualityIssueType.INVALID_KLINE.value: "Binance 返回的 K线字段未通过基础校验。",
        KlineQualityIssueType.EMPTY_BATCH.value: "Binance REST 未返回可校验的 K线数据。",
        KlineQualityIssueType.UNCLOSED_KLINE.value: "请求区间包含未收盘 K线，当前场景未被识别为安全阻断。",
    }
    if result.first_issue_type:
        return reason_by_issue_type.get(
            result.first_issue_type,
            f"质量检查发现异常类型：{result.first_issue_type}。",
        )
    return f"任务异常：{result.message}"


def _failure_retry_suggestion(result: ManualKlineBackfillResult) -> str:
    if result.status == KlineBackfillStatus.BLOCKED:
        return (
            "请先查看采集事件日志 collector_event_log、数据质量记录 data_quality_check "
            "与 Binance REST 官方返回，再确认是否需要重新选择已收盘区间执行手动补 K。"
        )
    return "请先排查程序异常、Binance REST、Redis 任务锁或 MySQL 写入状态；确认系统恢复后再重试。"


def _alert_submission_failed(result: AlertSendResult) -> bool:
    return result.status != AlertSendStatus.SUBMITTED_TO_HERMES


def _replace_result_alert(
    result: ManualKlineBackfillResult,
    alert_result: AlertSendResult,
    *,
    exit_code: int | None = None,
) -> ManualKlineBackfillResult:
    return ManualKlineBackfillResult(
        status=result.status,
        exit_code=result.exit_code if exit_code is None else exit_code,
        trace_id=result.trace_id,
        message=result.message,
        requested_count=result.requested_count,
        fetched_count=result.fetched_count,
        parsed_count=result.parsed_count,
        closed_count=result.closed_count,
        filtered_unclosed_count=result.filtered_unclosed_count,
        writable_count=result.writable_count,
        inserted_count=result.inserted_count,
        skipped_existing_count=result.skipped_existing_count,
        issue_count=result.issue_count,
        first_issue_type=result.first_issue_type,
        first_issue_message=result.first_issue_message,
        event_log_id=result.event_log_id,
        quality_check_id=result.quality_check_id,
        alert_status=alert_result.status.value,
        details=result.details,
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
