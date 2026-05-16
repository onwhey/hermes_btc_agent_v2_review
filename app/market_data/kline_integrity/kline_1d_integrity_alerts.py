"""Fixed-template alerts for BTCUSDT 1d daily Kline integrity checks.

This file belongs to `app/market_data/kline_integrity`. It converts the
read-only 1d daily review result into a compact Hermes `AlertEvent` with a
Chinese WeChat-visible body. It is called by
`app/market_data/kline_integrity/kline_1d_integrity_service.py::run_daily_1d_kline_integrity_check`.
It does not request Binance, read or write MySQL, read or write Redis, send
Hermes by itself, call DeepSeek, repair data, backfill data, generate strategy
advice, or perform trading.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSeverity, AlertType
from app.core.time_utils import timestamp_ms_to_utc_datetime
from app.market_data.kline_integrity.kline_1d_integrity_types import (
    DailyKline1dIntegrityCheckRequest,
    DailyKline1dIntegrityCheckResult,
    DailyKline1dIntegrityStatus,
)

INTERNAL_CONTEXT_DETAIL_KEY = "_internal_context"
KLINE_1D_BOUNDARY_TEXT = (
    "本次为只读健康检查：系统没有自动修复、没有人工改数、没有自动回补，"
    "也没有执行自动交易。"
)


def build_daily_1d_integrity_alert_event(
    result: DailyKline1dIntegrityCheckResult,
    *,
    request: DailyKline1dIntegrityCheckRequest,
) -> AlertEvent:
    """Build a compact fixed-template alert event for one 1d daily review.

    Parameters: `result` is already computed by the read-only service; `request`
    provides symbol, interval, trigger, and trace id.
    Return value: `AlertEvent` ready for the shared alerting service.
    Failure scenarios: none expected; this function only formats data.
    External services and data impact: none.
    """

    alert_type = (
        AlertType.KLINE_INTEGRITY_CHECK_PASSED
        if result.status == DailyKline1dIntegrityStatus.HEALTHY
        else AlertType.KLINE_INTEGRITY_CHECK_FAILED
    )
    severity = _severity_for_result(result)
    return AlertEvent(
        alert_type=alert_type,
        severity=severity,
        title=_title_for_result(result),
        summary=_summary_for_result(result),
        details={
            **dict(result.details),
            WECHAT_VISIBLE_BODY_DETAIL_KEY: _build_visible_body(result, request=request),
            INTERNAL_CONTEXT_DETAIL_KEY: dict(result.details),
        },
        source="app.market_data.kline_integrity.kline_1d_integrity_service",
        trace_id=request.trace_id,
    )


def _severity_for_result(result: DailyKline1dIntegrityCheckResult) -> AlertSeverity:
    if result.status == DailyKline1dIntegrityStatus.HEALTHY:
        return AlertSeverity.INFO
    if result.status in {DailyKline1dIntegrityStatus.WARNING, DailyKline1dIntegrityStatus.BLOCKED}:
        return AlertSeverity.WARNING
    if result.status == DailyKline1dIntegrityStatus.ERROR:
        return AlertSeverity.CRITICAL
    return AlertSeverity.ERROR


def _title_for_result(result: DailyKline1dIntegrityCheckResult) -> str:
    title_by_status = {
        DailyKline1dIntegrityStatus.HEALTHY: "1d 日 K 每日健康复核通过",
        DailyKline1dIntegrityStatus.WARNING: "1d 日 K 每日健康复核需关注",
        DailyKline1dIntegrityStatus.BLOCKED: "1d 日 K 每日健康复核未完成",
        DailyKline1dIntegrityStatus.FAILED: "1d 日 K 每日健康复核发现异常",
        DailyKline1dIntegrityStatus.ERROR: "1d 日 K 每日健康复核执行异常",
        DailyKline1dIntegrityStatus.SKIPPED: "1d 日 K 每日健康复核已跳过",
    }
    return title_by_status.get(result.status, "1d 日 K 每日健康复核结果")


def _summary_for_result(result: DailyKline1dIntegrityCheckResult) -> str:
    if result.status == DailyKline1dIntegrityStatus.HEALTHY:
        return f"最近 {result.checked_count} 根 1d 日 K 连续、字段合理，未发现数据质量异常。"
    if result.status == DailyKline1dIntegrityStatus.WARNING:
        return "1d 日 K 每日复核发现需关注状态，请检查最近日 K 和采集链路。"
    if result.status == DailyKline1dIntegrityStatus.BLOCKED:
        return "1d 日 K 尚未完成初始化或本次复核无法确认健康状态。"
    if result.status == DailyKline1dIntegrityStatus.SKIPPED:
        return "1d 日 K 每日复核因任务锁存在而跳过，本次未执行检查。"
    return result.first_issue_message or "1d 日 K 每日复核发现数据质量异常。"


def _build_visible_body(
    result: DailyKline1dIntegrityCheckResult,
    *,
    request: DailyKline1dIntegrityCheckRequest,
) -> str:
    common_lines = [
        f"币种周期：{request.symbol} {request.interval_value}",
        "",
        "检查范围：",
        _format_checked_range(result),
        "",
        f"检查数量：{result.checked_count} 根",
        f"问题数量：{result.issue_count}",
        "",
    ]
    if result.status == DailyKline1dIntegrityStatus.HEALTHY and result.issue_count == 0:
        result_lines = [
            "检查结果：",
            f"最近 {result.checked_count or request.lookback_count} 根 1d 日 K 连续、无缺失、无重复、字段合理。",
            "",
            "数据状态：",
            f"最新日 K：{_format_open_time_ms(result.latest_open_time_ms)}",
            f"理论最新已收盘日 K：{_format_open_time_ms(result.expected_latest_open_time_ms)}",
        ]
    else:
        result_lines = [
            "关键问题：",
            *_issue_lines(result),
            "",
            "数据状态：",
            f"最新日 K：{_format_open_time_ms(result.latest_open_time_ms)}",
            f"理论最新已收盘日 K：{_format_open_time_ms(result.expected_latest_open_time_ms)}",
            "",
            "建议动作：",
            "请检查 1d 采集链路、Binance REST 返回和 market_kline_1d 最近日 K；不要人工改数，不要自动修复。",
        ]
    tail_lines = [
        "",
        f"数据质量检查ID：{result.quality_check_id or '未生成'}",
        f"追踪ID：{request.trace_id}",
        KLINE_1D_BOUNDARY_TEXT,
        "本提醒不是交易建议。",
    ]
    return "\n".join(common_lines + result_lines + tail_lines)


def _issue_lines(result: DailyKline1dIntegrityCheckResult) -> list[str]:
    issues = result.details.get("issues") if isinstance(result.details, Mapping) else None
    if isinstance(issues, list):
        lines = []
        for index, issue in enumerate([item for item in issues if isinstance(item, Mapping)][:3], start=1):
            lines.append(f"{index}. {_public_issue_text(issue)}")
        if lines:
            return lines
    if result.first_issue_type or result.first_issue_message:
        return [
            "1. "
            + _public_issue_text(
                {
                    "issue_type": result.first_issue_type or "",
                    "message": result.first_issue_message or "",
                    "open_time_ms": result.latest_open_time_ms,
                }
            )
        ]
    return ["1. 本次未能确认 1d 日 K 健康状态。"]


def _public_issue_text(issue: Mapping[str, object]) -> str:
    issue_type = str(issue.get("issue_type", "") or "")
    message = str(issue.get("message", "") or "")
    open_time = _format_open_time_ms(issue.get("open_time_ms"))
    if issue_type == "empty_batch":
        return "market_kline_1d 尚未初始化，请先执行手动 1d backfill。"
    if issue_type == "batch_not_continuous":
        return f"最近 1d 日 K 存在缺口或不连续，位置：{open_time}。"
    if issue_type == "duplicate_open_time":
        return f"最近 1d 日 K 存在重复 open_time，位置：{open_time}。"
    if issue_type == "unclosed_kline":
        return f"正式表疑似存在未收盘日 K 误写，位置：{open_time}。"
    if issue_type == "missing_in_database":
        return f"最新 1d 日 K 落后理论最新已收盘日 K，位置：{open_time}。"
    if issue_type == "invalid_kline":
        return f"1d 日 K 字段合理性检查失败，位置：{open_time}。"
    if issue_type == "task_error":
        return "1d 每日复核任务执行异常，未能确认健康状态。"
    return message or "发现未分类的 1d 数据质量问题。"


def _format_checked_range(result: DailyKline1dIntegrityCheckResult) -> str:
    if result.checked_start_time and result.checked_end_time:
        return f"{_format_time_text(result.checked_start_time)} ~ {_format_time_text(result.checked_end_time)}"
    if result.details.get("range_unavailable_reason"):
        return f"未生成，原因：{result.details['range_unavailable_reason']}"
    return "未生成，原因：本次没有可复核的 1d 日 K 范围"


def _format_time_text(value: object) -> str:
    text = str(value)
    if text.endswith(" UTC"):
        return text
    if text.endswith("+00:00"):
        text = text[:-6]
    return text.replace("T", " ")[:19] + " UTC"


def _format_open_time_ms(value: object | None) -> str:
    if not isinstance(value, int) or value <= 0:
        return "无法确认 UTC"
    return f"{timestamp_ms_to_utc_datetime(value).strftime('%Y-%m-%d %H:%M:%S')} UTC"


__all__ = ["build_daily_1d_integrity_alert_event"]
