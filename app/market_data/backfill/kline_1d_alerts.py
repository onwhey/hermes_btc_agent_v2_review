"""Fixed-template Hermes alert helpers for manual BTCUSDT 1d backfill.

This file belongs to `app/market_data/backfill`.
It builds fixed Chinese alert events for manual 1d backfill success, blocked,
and failed outcomes, then delegates sending to `app/alerting/service.py`. It
does not request Binance, write formal Klines, write Redis, call DeepSeek,
generate advice, repair data, schedule jobs, or trade.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSendResult, AlertSendStatus, AlertSeverity, AlertType
from app.core.logger import get_logger
from app.core.time_utils import timestamp_ms_to_utc_datetime
from app.market_data.backfill.kline_1d_types import (
    BACKFILL_1D_EVENT_TYPE,
    EXIT_ALERT_FAILED,
    Kline1dBackfillStatus,
    ManualKline1dBackfillRequest,
    ManualKline1dBackfillResult,
)
from app.market_data.kline_quality.types import KlineQualityIssueType, KlineQualityReport

LOGGER = get_logger("market_data.backfill.kline_1d_alerts")

_INTERNAL_CONTEXT_DETAIL_KEY = "_internal_context"
_BOUNDARY_TEXT = "本次不会自动修复数据，不会人工改数，不会自动回补，也不会执行自动交易。"


def send_1d_failure_alert_and_adjust_exit_code(
    request: ManualKline1dBackfillRequest,
    result: ManualKline1dBackfillResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    report: KlineQualityReport | None = None,
) -> ManualKline1dBackfillResult:
    """Send mandatory 1d blocked/failed alert and return exit 3 when delivery fails."""

    alert_result = _send_1d_backfill_alert(
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
            "Manual 1d backfill alert submission to Hermes failed trace_id=%s status=%s error=%s",
            request.trace_id,
            alert_result.status.value,
            alert_result.error_message,
        )
        return _replace_result_alert(result, alert_result, exit_code=EXIT_ALERT_FAILED)
    return _replace_result_alert(result, alert_result)


def send_1d_success_alert_and_adjust_exit_code(
    request: ManualKline1dBackfillRequest,
    result: ManualKline1dBackfillResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> ManualKline1dBackfillResult:
    """Send optional 1d success alert and return exit 3 when delivery fails."""

    alert_result = _send_1d_backfill_alert(
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


def _send_1d_backfill_alert(
    request: ManualKline1dBackfillRequest,
    result: ManualKline1dBackfillResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    report: KlineQualityReport | None,
    success: bool,
) -> AlertSendResult:
    active_alert_sender = alert_sender or _default_alert_sender()
    active_alert_repository = alert_repository or _default_alert_repository()
    event = _build_1d_backfill_alert_event(request, result, report=report, success=success)
    return active_alert_sender(
        event,
        repository=active_alert_repository,
        db_session=db_session,
        send_real_alert=True,
    )


def _build_1d_backfill_alert_event(
    request: ManualKline1dBackfillRequest,
    result: ManualKline1dBackfillResult,
    *,
    report: KlineQualityReport | None,
    success: bool,
) -> AlertEvent:
    if success:
        title = "手动 1d 日 K 回补完成"
        summary = "BTCUSDT 1d 日 K 手动回补已完成。"
        if request.dry_run:
            title = "手动 1d 日 K 回补预演通过"
            summary = "手动 1d 日 K 回补预演通过，正式 1d K线表未被修改。"
        return AlertEvent(
            alert_type=AlertType.KLINE_INTEGRITY_CHECK_PASSED,
            severity=AlertSeverity.INFO,
            title=title,
            summary=summary,
            details=_compact_1d_alert_details(
                request,
                result,
                report,
                reason=summary,
                result_text=_success_result_text(request, result),
                suggestion="无需处理；可通过 collector_event_log 和追踪ID排查本次任务。",
            ),
            source="app.market_data.backfill.kline_1d_alerts",
            trace_id=request.trace_id,
        )

    alert_type = (
        AlertType.KLINE_DATA_QUALITY_ERROR
        if result.status == Kline1dBackfillStatus.BLOCKED
        else AlertType.COLLECTOR_ERROR
    )
    severity = AlertSeverity.CRITICAL if result.status == Kline1dBackfillStatus.FAILED else AlertSeverity.ERROR
    title = (
        "手动 1d 日 K 回补被质量检查阻断"
        if result.status == Kline1dBackfillStatus.BLOCKED
        else "手动 1d 日 K 回补执行失败"
    )
    return AlertEvent(
        alert_type=alert_type,
        severity=severity,
        title=title,
        summary=_public_failure_summary(result),
        details=_compact_1d_alert_details(
            request,
            result,
            report,
            reason=_public_failure_reason(result),
            result_text="系统已停止本次写入，market_kline_1d 未被本次任务覆盖、删除或修复。",
            suggestion=_failure_retry_suggestion(result),
        ),
        source="app.market_data.backfill.kline_1d_alerts",
        trace_id=request.trace_id,
    )


def _compact_1d_alert_details(
    request: ManualKline1dBackfillRequest,
    result: ManualKline1dBackfillResult,
    report: KlineQualityReport | None,
    *,
    reason: str,
    result_text: str,
    suggestion: str,
) -> dict[str, object]:
    body = "\n".join(
        [
            f"币种周期：{request.symbol} {request.interval_value}",
            f"请求范围：{_format_request_range_utc(request)}",
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
            "",
            f"边界声明：{_BOUNDARY_TEXT}",
        ]
    )
    return {
        WECHAT_VISIBLE_BODY_DETAIL_KEY: body,
        _INTERNAL_CONTEXT_DETAIL_KEY: _internal_alert_context(request, result, report),
    }


def _internal_alert_context(
    request: ManualKline1dBackfillRequest,
    result: ManualKline1dBackfillResult,
    report: KlineQualityReport | None,
) -> dict[str, object]:
    return {
        "event_type": BACKFILL_1D_EVENT_TYPE,
        "symbol": request.symbol,
        "interval_value": request.interval_value,
        "trigger_source": request.trigger_source,
        "data_source": request.data_source,
        "dry_run": request.dry_run,
        "formal_write_performed": bool(result.details.get("formal_write_performed", False)),
        "requested_start_open_time_ms": request.start_open_time_ms,
        "requested_end_open_time_ms": request.end_open_time_ms,
        "status": result.status.value,
        "requested_count": result.requested_count,
        "fetched_count": result.fetched_count,
        "parsed_count": result.parsed_count,
        "closed_count": result.closed_count,
        "filtered_unclosed_count": result.filtered_unclosed_count,
        "writable_count": result.writable_count,
        "inserted_count": result.inserted_count,
        "skipped_existing_count": result.skipped_existing_count,
        "issue_count": result.issue_count,
        "first_issue_type": result.first_issue_type or "",
        "first_issue_message": result.first_issue_message or "",
        "report_status": report.status.value if report is not None else "",
        "action": "no_auto_repair_no_human_field_edit_no_extra_backfill_no_market_kline_overwrite",
    }


def _success_result_text(
    request: ManualKline1dBackfillRequest,
    result: ManualKline1dBackfillResult,
) -> str:
    filter_text = ""
    if result.filtered_unclosed_count:
        filter_text = f" 已过滤未收盘日 K {result.filtered_unclosed_count} 根，未写入数据库。"
    if request.dry_run:
        return f"预演模式只完成请求、解析和质量检查，正式 1d K线表未被修改。{filter_text}".strip()
    if result.inserted_count > 0:
        return f"系统已写入 {result.inserted_count} 根通过质量检查的已收盘 1d K线。{filter_text}".strip()
    return f"本次没有新增正式 1d K线，正式 1d K线表未被修改。{filter_text}".strip()


def _public_failure_summary(result: ManualKline1dBackfillResult) -> str:
    if result.status == Kline1dBackfillStatus.BLOCKED:
        return "手动 1d 日 K 回补被质量规则阻断，系统没有写入正式 1d K线表。"
    return "手动 1d 日 K 回补执行失败，系统没有继续写入正式 1d K线表。"


def _public_failure_reason(result: ManualKline1dBackfillResult) -> str:
    reason_by_issue_type = {
        KlineQualityIssueType.BATCH_NOT_CONTINUOUS.value: "Binance 返回的 1d 历史区间存在缺失、断档或不连续。",
        KlineQualityIssueType.DATABASE_NOT_CONTINUOUS.value: "回补区间与 market_kline_1d 已有相邻日 K 无法连续衔接。",
        KlineQualityIssueType.DATABASE_CONFLICT.value: "market_kline_1d 已有同一 open time 的日 K，但核心字段与 Binance 返回不一致。",
        KlineQualityIssueType.INVALID_KLINE.value: "Binance 返回的 1d K线字段未通过基础校验。",
        KlineQualityIssueType.EMPTY_BATCH.value: "Binance REST 未返回可校验的 1d K线数据。",
        KlineQualityIssueType.UNCLOSED_KLINE.value: "发现未收盘日 K 异常，系统已阻止写入正式 1d 表。",
    }
    if result.first_issue_type:
        return reason_by_issue_type.get(result.first_issue_type, f"质量检查发现异常类型：{result.first_issue_type}。")
    return f"任务异常：{result.message}"


def _failure_retry_suggestion(result: ManualKline1dBackfillResult) -> str:
    if result.status == Kline1dBackfillStatus.BLOCKED:
        return (
            "请先查看 collector_event_log、data_quality_check、Binance REST 官方返回和 market_kline_1d "
            "最近日 K；确认问题后重新选择已收盘区间执行手动回补，不要人工改数、不要自动修复。"
        )
    return "请先排查程序异常、Binance REST、Redis 任务锁或 MySQL 写入状态；确认系统恢复后再重试。"


def _format_request_range_utc(request: ManualKline1dBackfillRequest) -> str:
    return (
        f"{_format_open_time_utc(request.start_open_time_ms)} ~ "
        f"{_format_open_time_utc(request.end_open_time_ms)}"
    )


def _format_open_time_utc(open_time_ms: int | None) -> str:
    if open_time_ms is None:
        return "无法确认 UTC"
    return f"{timestamp_ms_to_utc_datetime(open_time_ms).strftime('%Y-%m-%d %H:%M')} UTC"


def _alert_submission_failed(result: AlertSendResult) -> bool:
    return result.status != AlertSendStatus.SUBMITTED_TO_HERMES


def _replace_result_alert(
    result: ManualKline1dBackfillResult,
    alert_result: AlertSendResult,
    *,
    exit_code: int | None = None,
) -> ManualKline1dBackfillResult:
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
