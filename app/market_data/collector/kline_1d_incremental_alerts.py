"""Fixed-template Hermes alert helpers for BTCUSDT 1d incremental collection.

This file belongs to `app/market_data/collector`. It builds compact Chinese
alert events for 1d incremental success, blocked, and failed outcomes, then
delegates sending to `app/alerting/service.py`. It does not request Binance,
write formal Klines, write Redis, call DeepSeek, generate advice, repair data,
schedule jobs, or trade.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSendResult, AlertSendStatus, AlertSeverity, AlertType
from app.core.logger import get_logger
from app.core.time_utils import timestamp_ms_to_utc_datetime
from app.market_data.collector.kline_1d_incremental_types import (
    EXIT_ALERT_FAILED,
    IncrementalKline1dCollectRequest,
    IncrementalKline1dCollectResult,
    KLINE_1D_INCREMENTAL_EVENT_TYPE,
    KlineCollectStatus,
)
from app.market_data.kline_quality.types import KlineQualityIssueType, KlineQualityReport

LOGGER = get_logger("market_data.collector.kline_1d_alerts")

_INTERNAL_CONTEXT_DETAIL_KEY = "_internal_context"
_BOUNDARY_TEXT = "本次仅执行 1d 增量采集边界内的校验与写入：系统没有自动修复、没有人工改数、没有自动回补，也没有执行自动交易。"


def send_incremental_1d_failure_alert_and_adjust_exit_code(
    request: IncrementalKline1dCollectRequest,
    result: IncrementalKline1dCollectResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    report: KlineQualityReport | None = None,
) -> IncrementalKline1dCollectResult:
    """Send mandatory real-run 1d blocked/failed alert and adjust exit code."""

    alert_result = _send_incremental_1d_alert(
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
            "1d incremental alert submission to Hermes failed trace_id=%s status=%s error=%s",
            request.trace_id,
            alert_result.status.value,
            alert_result.error_message,
        )
        return _replace_result_alert(result, alert_result, exit_code=EXIT_ALERT_FAILED)
    return _replace_result_alert(result, alert_result)


def send_incremental_1d_success_alert_and_adjust_exit_code(
    request: IncrementalKline1dCollectRequest,
    result: IncrementalKline1dCollectResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> IncrementalKline1dCollectResult:
    """Send optional real-run 1d success alert and adjust exit code when needed."""

    alert_result = _send_incremental_1d_alert(
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


def _send_incremental_1d_alert(
    request: IncrementalKline1dCollectRequest,
    result: IncrementalKline1dCollectResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    report: KlineQualityReport | None,
    success: bool,
) -> AlertSendResult:
    active_alert_sender = alert_sender or _default_alert_sender()
    active_alert_repository = alert_repository or _default_alert_repository()
    event = _build_incremental_1d_alert_event(request, result, report=report, success=success)
    return active_alert_sender(
        event,
        repository=active_alert_repository,
        db_session=db_session,
        send_real_alert=True,
    )


def _build_incremental_1d_alert_event(
    request: IncrementalKline1dCollectRequest,
    result: IncrementalKline1dCollectResult,
    *,
    report: KlineQualityReport | None,
    success: bool,
) -> AlertEvent:
    if success:
        title = "1d 日 K 增量采集完成"
        summary = "BTCUSDT 1d 日 K 增量采集已完成。"
        if request.dry_run:
            title = "1d 日 K 增量采集预演通过"
            summary = "BTCUSDT 1d 日 K 增量采集预演通过，正式 1d K线表未被修改。"
        return AlertEvent(
            alert_type=AlertType.SYSTEM_CHECK,
            severity=AlertSeverity.INFO,
            title=title,
            summary=summary,
            details=_compact_1d_incremental_alert_details(
                request,
                result,
                report,
                reason=summary,
                result_text=_success_result_text(request, result),
                suggestion="无需处理；可通过 collector_event_log 和追踪ID排查本次任务。",
            ),
            source="app.market_data.collector.kline_1d_incremental_alerts",
            trace_id=request.trace_id,
        )

    alert_type = (
        AlertType.KLINE_DATA_QUALITY_ERROR
        if result.status == KlineCollectStatus.BLOCKED
        else AlertType.COLLECTOR_ERROR
    )
    severity = AlertSeverity.CRITICAL if result.status == KlineCollectStatus.FAILED else AlertSeverity.ERROR
    title = (
        "1d 日 K 增量采集被质量检查阻断"
        if result.status == KlineCollectStatus.BLOCKED
        else "1d 日 K 增量采集执行失败"
    )
    return AlertEvent(
        alert_type=alert_type,
        severity=severity,
        title=title,
        summary=_public_failure_summary(result),
        details=_compact_1d_incremental_alert_details(
            request,
            result,
            report,
            reason=_public_failure_reason(result),
            result_text="系统已停止本次写入，market_kline_1d 未被本次任务覆盖、删除或修复。",
            suggestion=_failure_retry_suggestion(result),
        ),
        source="app.market_data.collector.kline_1d_incremental_alerts",
        trace_id=request.trace_id,
    )


def _compact_1d_incremental_alert_details(
    request: IncrementalKline1dCollectRequest,
    result: IncrementalKline1dCollectResult,
    report: KlineQualityReport | None,
    *,
    reason: str,
    result_text: str,
    suggestion: str,
) -> dict[str, object]:
    body = "\n".join(
        [
            f"币种周期：{request.symbol} {request.interval_value}",
            f"检查范围：{_format_result_range_utc(result)}",
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
    request: IncrementalKline1dCollectRequest,
    result: IncrementalKline1dCollectResult,
    report: KlineQualityReport | None,
) -> dict[str, object]:
    return {
        "event_type": KLINE_1D_INCREMENTAL_EVENT_TYPE,
        "symbol": request.symbol,
        "interval_value": request.interval_value,
        "trigger_source": request.trigger_source,
        "data_source": request.data_source,
        "dry_run": request.dry_run,
        "formal_write_performed": bool(result.details.get("formal_write_performed", False)),
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
    request: IncrementalKline1dCollectRequest,
    result: IncrementalKline1dCollectResult,
) -> str:
    filter_text = ""
    if result.filtered_unclosed_count:
        filter_text = f" 已过滤当前未收盘日 K {result.filtered_unclosed_count} 根，未写入数据库。"
    if request.dry_run:
        return f"预演模式只完成请求、解析和质量检查，正式 1d K线表未被修改。{filter_text}".strip()
    if result.inserted_count > 0:
        return f"系统已写入 {result.inserted_count} 根通过质量检查的已收盘 1d K线。{filter_text}".strip()
    return f"本次没有新增正式 1d K线，正式 1d K线表未被修改。{filter_text}".strip()


def _public_failure_summary(result: IncrementalKline1dCollectResult) -> str:
    if result.status == KlineCollectStatus.BLOCKED:
        return "1d 日 K 增量采集被质量规则阻断，系统没有写入正式 1d K线表。"
    return "1d 日 K 增量采集执行失败，系统没有继续写入正式 1d K线表。"


def _public_failure_reason(result: IncrementalKline1dCollectResult) -> str:
    reason_by_issue_type = {
        KlineQualityIssueType.BATCH_NOT_CONTINUOUS.value: "Binance 返回的 1d 增量区间存在缺失、断档或不连续。",
        KlineQualityIssueType.DATABASE_NOT_CONTINUOUS.value: "REST 数据与 market_kline_1d 最新日 K 无法连续衔接。",
        KlineQualityIssueType.DATABASE_CONFLICT.value: "market_kline_1d 已有同一 open time 的日 K，但核心字段与 Binance 返回不一致。",
        KlineQualityIssueType.INVALID_KLINE.value: "Binance 返回的 1d K线字段未通过基础校验。",
        KlineQualityIssueType.EMPTY_BATCH.value: "Binance REST 未返回可校验的 1d K线数据。",
        KlineQualityIssueType.UNCLOSED_KLINE.value: "发现未收盘日 K 异常，系统已阻止写入正式 1d 表。",
    }
    if result.first_issue_type:
        return reason_by_issue_type.get(result.first_issue_type, f"质量检查发现异常类型：{result.first_issue_type}。")
    return f"任务异常：{result.message}"


def _failure_retry_suggestion(result: IncrementalKline1dCollectResult) -> str:
    if result.status == KlineCollectStatus.BLOCKED:
        return (
            "请先查看 collector_event_log、data_quality_check、Binance REST 官方返回和 market_kline_1d "
            "最近日 K；确认问题后可手动选择已收盘区间执行 backfill，不要人工改数、不要自动修复。"
        )
    return "请先排查程序异常、Binance REST、Redis 任务锁或 MySQL 写入状态；确认系统恢复后再重试。"


def _format_result_range_utc(result: IncrementalKline1dCollectResult) -> str:
    start = result.details.get("requested_start_open_time_ms")
    end = result.details.get("requested_end_open_time_ms")
    if not isinstance(start, int) or not isinstance(end, int) or start <= 0 or end <= 0:
        reason = result.details.get("range_unavailable_reason")
        if isinstance(reason, str) and reason.strip():
            return f"未生成，原因：{reason.strip()}"
        return "未生成，原因：本次未生成检查范围"
    return f"{_format_open_time_utc(start)} ~ {_format_open_time_utc(end)}"


def _format_open_time_utc(open_time_ms: object) -> str:
    if isinstance(open_time_ms, int):
        return f"{timestamp_ms_to_utc_datetime(open_time_ms).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    if not isinstance(open_time_ms, int):
        return "无法确认 UTC"
    return f"{timestamp_ms_to_utc_datetime(open_time_ms).strftime('%Y-%m-%d %H:%M')} UTC"


def _alert_submission_failed(result: AlertSendResult) -> bool:
    return result.status != AlertSendStatus.SUBMITTED_TO_HERMES


def _replace_result_alert(
    result: IncrementalKline1dCollectResult,
    alert_result: AlertSendResult,
    *,
    exit_code: int | None = None,
) -> IncrementalKline1dCollectResult:
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


__all__ = [
    "send_incremental_1d_failure_alert_and_adjust_exit_code",
    "send_incremental_1d_success_alert_and_adjust_exit_code",
]
