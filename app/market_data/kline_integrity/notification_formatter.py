"""每日 K线一致性复核通知格式化。

本文件属于 `app/market_data/kline_integrity` 模块。
本文件只负责把每日复核结果转换为 Hermes `AlertEvent` 和微信可见中文摘要。
本文件不负责执行 K线检查，不负责读写 `market_kline_4h`，不负责写
`data_quality_check` 或 `alert_message`，不负责发送 Hermes。
主要被 `app/market_data/kline_integrity/kline_integrity_service.py` 调用。
本文件不请求 Binance，不读写 MySQL，不读写 Redis，不调用 DeepSeek，
不生成交易建议，不实现任何自动交易或自动修复能力。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSeverity, AlertType
from app.core.time_utils import UTC, timestamp_ms_to_utc_datetime
from app.market_data.kline_integrity.types import (
    DailyKlineIntegrityCheckRequest,
    DailyKlineIntegrityCheckResult,
    DailyKlineIntegrityStatus,
)
from app.market_data.kline_quality.types import KlineQualityIssueType

INTERNAL_CONTEXT_DETAIL_KEY = "_internal_context"


def build_daily_result_alert_event(
    result: DailyKlineIntegrityCheckResult,
    *,
    request: DailyKlineIntegrityCheckRequest,
    report_status: str,
) -> AlertEvent:
    """Build the fixed-template alert event for one daily review result.

    Parameters: `result` is the already computed read-only review result,
    `request` carries symbol, interval and trace id, and `report_status` is the
    public daily status such as `healthy` or `unhealthy`.
    Return value: an `AlertEvent` whose structured details keep audit context
    while `_wechat_visible_body` contains the compact Chinese WeChat body.
    Failure scenarios: no external failure; this function only formats data.
    External services: does not request Binance, MySQL, Redis or Hermes.
    Data impact: does not write formal Klines or quality records.
    This function does not repair data, backfill data, call DeepSeek, or trade.
    """

    severity_by_status = {
        "healthy": AlertSeverity.INFO,
        "unhealthy": AlertSeverity.ERROR,
        "unknown": AlertSeverity.CRITICAL,
        "skipped": AlertSeverity.WARNING,
    }
    summary_by_status = {
        "healthy": f"最近 {result.checked_count or request.lookback_count} 根 4h K线检查通过",
        "unhealthy": "每日 K线健康检查发现数据质量问题",
        "unknown": "每日 K线健康检查无法确认健康状态",
        "skipped": "每日 K线健康检查已跳过，无法确认本次健康状态",
    }
    title_by_status = {
        "healthy": "每日 K线健康检查通过",
        "unhealthy": "每日 K线健康检查发现异常",
        "unknown": "每日 K线健康检查无法确认",
        "skipped": "每日 K线健康检查已跳过",
    }
    details = dict(result.details)
    return AlertEvent(
        alert_type=(
            AlertType.KLINE_INTEGRITY_CHECK_PASSED
            if report_status == "healthy"
            else AlertType.KLINE_INTEGRITY_CHECK_FAILED
        ),
        severity=severity_by_status.get(report_status, AlertSeverity.CRITICAL),
        title=title_by_status.get(report_status, "每日 K线健康检查结果"),
        summary=summary_by_status.get(report_status, "每日 K线健康检查无法确认健康状态"),
        details={
            **details,
            WECHAT_VISIBLE_BODY_DETAIL_KEY: _build_daily_result_wechat_visible_body(
                result,
                request=request,
                report_status=report_status,
            ),
            INTERNAL_CONTEXT_DETAIL_KEY: details,
        },
        source="app.market_data.kline_integrity.kline_integrity_service",
        trace_id=request.trace_id,
    )


def _build_daily_result_wechat_visible_body(
    result: DailyKlineIntegrityCheckResult,
    *,
    request: DailyKlineIntegrityCheckRequest,
    report_status: str,
) -> str:
    """Build the compact Chinese WeChat body while keeping raw context hidden."""

    checked_count = result.checked_count or 0
    issue_count = result.issue_count or 0
    common_lines = [
        f"币种周期：{request.symbol} {request.interval_value}",
        "",
        "检查范围：",
        _format_checked_range_utc(result),
        "",
        f"检查数量：{checked_count} 根",
        f"问题数量：{issue_count}",
        "",
    ]

    if report_status == "healthy" and issue_count == 0:
        outcome_lines = [
            "检查结果：",
            (
                f"最近 {checked_count or request.lookback_count} 根 {request.interval_value} K线"
                "连续、无缺失、无重复、未发现数据质量异常。"
            ),
            "",
            "数据来源：",
            "Binance REST 官方 K线；本次仅检查，不修复、不回补、不写入正式 K线表。",
            "",
            "补充：",
            f"已过滤未收盘 K线 {_filtered_unclosed_count(result)} 根，未写入数据库。",
        ]
    else:
        outcome_lines = [
            "关键问题：",
            *_format_public_issue_summary_lines(result),
            "",
            "数据来源：",
            "Binance REST 官方 K线；本次仅检查，不修复、不回补、不写入正式 K线表。",
            "",
            "数据质量检查ID：",
            _quality_check_id_text(result),
            "",
            "建议动作：",
            "请检查采集链路、Binance REST 返回、数据库最近 K线；不要人工改数、不要自动修复。",
        ]

    boundary_lines = [
        "",
        "边界声明：",
        "本次为只读健康检查：系统没有自动修复、没有人工改数、没有自动回补，也没有执行自动交易。",
        "",
        f"追踪ID：{request.trace_id}",
    ]
    return "\n".join(common_lines + outcome_lines + boundary_lines)


def _format_public_issue_summary_lines(result: DailyKlineIntegrityCheckResult) -> list[str]:
    """Return at most three Chinese issue summaries for the visible notice."""

    issues = _extract_report_issues(result)
    lines: list[str] = []
    for index, issue in enumerate(issues[:3], start=1):
        lines.append(f"{index}. {_public_issue_summary(issue)}")
    if lines:
        return lines
    if result.first_issue_type:
        return [
            "1. "
            + _public_issue_summary(
                {
                    "issue_type": result.first_issue_type,
                    "message": result.first_issue_message or "",
                }
            )
        ]
    if result.status == DailyKlineIntegrityStatus.SKIPPED:
        return ["1. 复核任务被跳过，本次未完成检查，无法确认 K线健康状态。"]
    return ["1. 复核任务异常，本次无法确认 K线健康状态。"]


def _extract_report_issues(result: DailyKlineIntegrityCheckResult) -> list[Mapping[str, object]]:
    report = result.details.get("report") if isinstance(result.details, Mapping) else None
    if not isinstance(report, Mapping):
        return []
    raw_issues = report.get("issues")
    if not isinstance(raw_issues, list):
        return []
    return [issue for issue in raw_issues if isinstance(issue, Mapping)]


def _public_issue_summary(issue: Mapping[str, object]) -> str:
    issue_type = str(issue.get("issue_type", "") or "")
    open_time_text = _format_open_time_ms_utc(issue.get("open_time_ms"))
    field_name = str(issue.get("field_name", "") or "")

    if issue_type == KlineQualityIssueType.MISSING_IN_DATABASE.value:
        return f"数据库缺失 Binance 官方 K线（open time：{open_time_text}）。"
    if issue_type == KlineQualityIssueType.DATABASE_FIELD_MISMATCH.value:
        field_text = f"，字段：{field_name}" if field_name else ""
        return f"数据库 K线与 Binance 官方 K线核心字段不一致（open time：{open_time_text}{field_text}）。"
    if issue_type == KlineQualityIssueType.EXTRA_IN_DATABASE.value:
        return f"数据库存在本次官方范围内未返回的异常 K线（open time：{open_time_text}）。"
    if issue_type == KlineQualityIssueType.DUPLICATE_OPEN_TIME.value:
        return f"数据库或检查批次存在重复 K线（open time：{open_time_text}）。"
    if issue_type == KlineQualityIssueType.UNCLOSED_KLINE.value:
        return f"数据库存在未收盘 K线（open time：{open_time_text}）。"
    if issue_type == KlineQualityIssueType.INVALID_DATA_SOURCE_MAPPING.value:
        return f"数据库 K线的数据来源与触发来源映射不符合官方来源规则（open time：{open_time_text}）。"
    if issue_type == KlineQualityIssueType.INVALID_KLINE.value:
        return f"数据库或官方 K线字段未通过基础校验（open time：{open_time_text}）。"
    if issue_type == KlineQualityIssueType.INSUFFICIENT_CLOSED_KLINES.value:
        return "Binance REST 本次返回的已收盘 K线数量不足，无法完成指定数量复核。"
    if issue_type == KlineQualityIssueType.TASK_ERROR.value:
        return "检查任务执行异常，无法确认本次 K线健康状态。"
    if not issue_type:
        return "检查任务未完成，无法确认本次 K线健康状态。"
    return "发现未分类数据质量问题，请查看数据质量检查记录。"


def _filtered_unclosed_count(result: DailyKlineIntegrityCheckResult) -> int:
    report = result.details.get("report") if isinstance(result.details, Mapping) else None
    if isinstance(report, Mapping):
        metadata = report.get("metadata")
        if isinstance(metadata, Mapping):
            value = metadata.get("filtered_unclosed_count")
            if isinstance(value, int):
                return value
    value = result.details.get("filtered_unclosed_count") if isinstance(result.details, Mapping) else None
    return value if isinstance(value, int) else 0


def _quality_check_id_text(result: DailyKlineIntegrityCheckResult) -> str:
    if result.quality_check_id is not None:
        return str(result.quality_check_id)
    value = result.details.get("data_quality_check_id") if isinstance(result.details, Mapping) else None
    return str(value) if value else "未生成"


def _format_checked_range_utc(result: DailyKlineIntegrityCheckResult) -> str:
    start = _format_checked_time_utc(result.checked_start_time)
    end = _format_checked_time_utc(result.checked_end_time)
    if start == "无法确认 UTC" and end == "无法确认 UTC":
        return "无法确认 UTC"
    return f"{start} ~ {end}"


def _format_checked_time_utc(value: object | None) -> str:
    if value in (None, ""):
        return "无法确认 UTC"
    if isinstance(value, datetime):
        return f"{value.astimezone(UTC).strftime('%Y-%m-%d %H:%M')} UTC"
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return f"{parsed.astimezone(UTC).strftime('%Y-%m-%d %H:%M')} UTC"


def _format_open_time_ms_utc(value: object | None) -> str:
    if value in (None, ""):
        return "无法确认 UTC"
    try:
        open_time_ms = int(value)
    except (TypeError, ValueError):
        return "无法确认 UTC"
    return f"{timestamp_ms_to_utc_datetime(open_time_ms).strftime('%Y-%m-%d %H:%M')} UTC"
