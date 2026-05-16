"""Runtime status rendering and optional alert event construction.

本文件属于 `app/monitoring` 模块，只负责把只读运行状态报告渲染成中文控制台
文本或精简 Hermes 摘要事件。
本文件不读取 MySQL、不读取 Redis、不请求 Binance、不触发采集、回补或修复；
仅 `send_runtime_status_alert` 在用户显式 `--send-alert` 时调用现有告警链路。
"""

from __future__ import annotations

from typing import Any

from app.alerting.service import send_alert
from app.alerting.status_text import alert_send_status_label
from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSeverity, AlertType
from app.core.config import AppSettings
from app.core.time_utils import format_datetime_with_timezone, utc_aware_to_prc_aware
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE
from app.monitoring.runtime_status_types import LEVEL_LABELS, RuntimeStatusLevel, RuntimeStatusReport

LEGACY_ALERT_STATUS_VALUES = {"sent", "failed", "delivered", "weixin_success"}


def render_runtime_status_console(report: RuntimeStatusReport) -> str:
    """Render the runtime report for human console reading in Chinese."""

    lines = [
        "【Hermes BTC 运行状态检查】",
        "",
        f"总体结论：{LEVEL_LABELS[report.overall_level]}",
        "",
        "服务状态：",
    ]
    for service in report.services:
        lines.append(f"- {service.display_name}：{service.status_label}")

    lines.extend(["", "数据状态："])
    if report.mysql.connection_ok:
        latest = _format_utc_prc(report.mysql.latest_kline_open_time_utc)
        lines.append(f"- 最新 {DEFAULT_KLINE_SYMBOL} {KLINE_4H_INTERVAL_VALUE} K线：{latest}")
        if report.mysql.recent_kline_count is None:
            lines.append("- 最近 100 根 K线：无法确认")
        else:
            count_label = (
                "已读取 100 根，连续性以每日 K线复核为准"
                if report.mysql.recent_kline_count >= 100
                else f"仅读取到 {report.mysql.recent_kline_count} 根"
            )
            lines.append(f"- 最近 100 根 K线：{count_label}")
        lines.append(f"- 最近一次 4h 增量采集：{_collector_status_label(report.mysql.latest_collector_status)}")
        lines.append(f"- 最近一次 4h 每日复核：{_daily_quality_status_label(report.mysql.latest_daily_quality_status)}")
        lines.append(f"- 最新 {DEFAULT_KLINE_SYMBOL} {KLINE_1D_INTERVAL_VALUE} 日 K：{_format_1d_latest(report)}")
        lines.append(f"- 1d 数据新鲜度：{_kline_1d_freshness_label(report)}")
        lines.append(f"- 最近一次 1d 增量采集：{_collector_status_label(report.mysql.latest_1d_collector_status, report.mysql.latest_1d_collector_message)}")
        lines.append(f"- 最近一次 1d 每日复核：{_daily_1d_quality_status_label(report)}")
    else:
        lines.append(f"- MySQL：{report.mysql.error_message or '连接失败'}")

    lines.extend(["", "Redis 状态："])
    if report.redis.connection_ok:
        lines.append(f"- bitcoin_price：{_bitcoin_price_label(report.redis.bitcoin_price_exists, report.redis.bitcoin_price_ttl)}")
        lines.append(f"- scheduler running key：{report.redis.scheduler_running_count}")
        lines.append(f"- scheduler completed key：{report.redis.scheduler_completed_count}")
        lines.append(f"- scheduler status key：{report.redis.scheduler_status_count}")
        if report.redis.scheduler_job_legacy_count:
            lines.append(f"- scheduler job 旧 key：{report.redis.scheduler_job_legacy_count}（历史残留，等待过期）")
        else:
            lines.append("- scheduler job 旧 key：0")
    else:
        lines.append(f"- Redis：{report.redis.error_message or '连接失败'}")

    lines.extend(["", "告警状态："])
    if report.alert.connection_ok and report.alert.latest_status:
        lines.append(f"- 最近一次 Hermes 提交：{_alert_status_label(report.alert.latest_status)}")
        lines.append(f"- 回看窗口内历史提交失败：{'有' if report.alert.failed_count else '无'}")
        lines.append(f"- 旧版状态记录：{_legacy_status_record_label(report)}")
        if report.alert.latest_failure_error_message:
            lines.append(f"- 最近失败原因：{report.alert.latest_failure_error_message}")
        lines.append("- 说明：BTC Agent 只记录是否提交 Hermes；最终微信送达状态不由 alert_message 表直接确认。")
    elif report.alert.connection_ok:
        lines.append("- 暂无告警发送记录")
        lines.append("- 说明：BTC Agent 只记录是否提交 Hermes；最终微信送达状态不由 alert_message 表直接确认。")
    else:
        lines.append(f"- 告警记录：{report.alert.error_message or '无法读取'}")

    if report.issues:
        lines.extend(["", "关键问题："])
        for issue in report.issues[:5]:
            lines.append(f"- {LEVEL_LABELS[issue.level]}：{issue.message}")

    lines.extend(
        [
            "",
            "结论：",
            _overall_sentence(report.overall_level),
            "",
            "注意：",
            "本检查只读，不修复、不回补、不写正式 K线表，也不执行自动交易。",
        ]
    )
    return "\n".join(lines)


def build_runtime_status_alert_event(report: RuntimeStatusReport) -> AlertEvent:
    """Build a compact Chinese alert event for optional manual notification."""

    return AlertEvent(
        alert_type=AlertType.SYSTEM_CHECK,
        severity=_alert_severity_from_runtime_level(report.overall_level),
        title="Hermes BTC 运行状态检查",
        summary=f"总体结论：{LEVEL_LABELS[report.overall_level]}",
        details={
            WECHAT_VISIBLE_BODY_DETAIL_KEY: _build_runtime_status_alert_body(report),
            "overall_level": report.overall_level.value,
            "trace_id": report.trace_id,
            "checked_at_utc": format_datetime_with_timezone(report.checked_at_utc),
            "source": "manual_runtime_status_check",
        },
        trace_id=report.trace_id,
    )


def send_runtime_status_alert(
    report: RuntimeStatusReport,
    *,
    settings: AppSettings | None = None,
    alert_sender: Any | None = None,
    alert_repository: Any | None = None,
    db_session: Any | None = None,
) -> Any:
    """Send the runtime status summary through the existing alerting service."""

    return send_alert(
        build_runtime_status_alert_event(report),
        settings=settings or AppSettings(),
        client=alert_sender,
        repository=alert_repository,
        db_session=db_session,
        send_real_alert=True,
    )


def _build_runtime_status_alert_body(report: RuntimeStatusReport) -> str:
    issue_lines = [f"- {issue.message}" for issue in report.issues[:3]]
    body = [
        "【Hermes BTC 运行状态检查】",
        "",
        f"级别：{_severity_label(_alert_severity_from_runtime_level(report.overall_level))}",
        f"总体结论：{LEVEL_LABELS[report.overall_level]}",
        "",
        "服务状态：",
        _service_summary(report),
        "",
        "数据状态：",
        _data_summary(report),
        "",
        "告警状态：",
        _alert_status_summary(report),
        "本摘要将通过 Hermes 通道提交。",
        "本报告反映发送前的系统状态；本次摘要提交结果见命令行输出。最终微信送达状态由 Hermes/微信通道决定，BTC Agent 不直接确认。",
    ]
    if issue_lines:
        body.extend(["", "关键问题：", *issue_lines])
    body.extend(
        [
            "",
            f"追踪ID：{report.trace_id}",
            "本检查只读，不修复、不回补、不写正式 K线表，也不执行自动交易。",
            "本提醒不是交易建议，系统没有执行自动交易。",
        ]
    )
    return "\n".join(body)


def _format_utc_prc(value: Any) -> str:
    if value is None:
        return "未知"
    prc_value = utc_aware_to_prc_aware(value)
    return f"{format_datetime_with_timezone(value)} / {prc_value.strftime('%Y-%m-%d %H:%M')} 北京时间"


def _alert_severity_from_runtime_level(level: RuntimeStatusLevel) -> AlertSeverity:
    if level is RuntimeStatusLevel.CRITICAL:
        return AlertSeverity.CRITICAL
    if level is RuntimeStatusLevel.ERROR:
        return AlertSeverity.ERROR
    if level is RuntimeStatusLevel.WARNING:
        return AlertSeverity.WARNING
    return AlertSeverity.INFO


def _severity_label(severity: AlertSeverity) -> str:
    return {
        AlertSeverity.INFO: "信息",
        AlertSeverity.WARNING: "警告",
        AlertSeverity.ERROR: "错误",
        AlertSeverity.CRITICAL: "严重",
    }[severity]


def _bitcoin_price_label(exists: bool, ttl: int | None) -> str:
    if not exists:
        return "不存在"
    if ttl is None:
        return "存在，TTL 未知"
    if ttl < 0:
        return "存在，TTL 异常"
    return "存在，TTL 正常"


def _alert_status_label(status: str | None) -> str:
    if status is None:
        return "未知"
    if str(status) in LEGACY_ALERT_STATUS_VALUES:
        return "旧版状态记录"
    if str(status) == "skipped":
        return "已跳过发送"
    return alert_send_status_label(status) or "未知"


def _legacy_status_record_label(report: RuntimeStatusReport) -> str:
    if report.alert.legacy_status_count:
        return "有，需后续清理或忽略历史数据"
    return "无"


def _alert_status_summary(report: RuntimeStatusReport) -> str:
    if not report.alert.latest_status:
        return "暂无告警发送记录。\nBTC Agent 只记录是否提交 Hermes；最终微信送达状态不由 alert_message 表直接确认。"

    summary = (
        f"最近一次 Hermes 提交：{_alert_status_label(report.alert.latest_status)}；"
        f"回看窗口内历史提交失败：{'有' if report.alert.failed_count else '无'}；"
        f"旧版状态记录：{_legacy_status_record_label(report)}。"
    )
    if report.alert.latest_failure_error_message:
        summary += f"\n最近失败原因：{report.alert.latest_failure_error_message}。"
    summary += "\nBTC Agent 只记录是否提交 Hermes；最终微信送达状态不由 alert_message 表直接确认。"
    return summary


def _collector_status_label(status: str | None, message: str | None = None) -> str:
    if status in {"success", "completed", "healthy"}:
        return "成功"
    if status == "skipped":
        if message and ("lock" in message.lower() or "锁" in message):
            return "跳过：任务锁已存在"
        return f"跳过：{message}" if message else "跳过"
    if status in {"failed", "blocked", "error", "critical"}:
        return f"异常：{message}" if message else "异常"
    if status == "partial_success":
        return "部分成功"
    return "未知" if not status else str(status)


def _daily_quality_status_label(status: str | None) -> str:
    if status in {"healthy", "passed", "success"}:
        return "健康"
    if status in {"warning", "blocked", "not_initialized"}:
        return "警告"
    if status in {"failed", "error", "critical", "unhealthy"}:
        return "异常"
    return "未知" if not status else str(status)


def _daily_1d_quality_status_label(report: RuntimeStatusReport) -> str:
    status = report.mysql.latest_1d_daily_quality_status
    message = report.mysql.latest_1d_daily_quality_message
    if status is None:
        if report.mysql.latest_kline_1d_open_time_utc is None:
            return "暂无记录 / 未初始化"
        return "暂无记录"
    label = _daily_quality_status_label(status)
    if status in {"warning", "blocked", "not_initialized"} and message:
        return f"{label}：{message}"
    if status in {"failed", "error", "critical", "unhealthy"} and message:
        return f"{label}：{message}"
    return label


def _format_1d_latest(report: RuntimeStatusReport) -> str:
    latest = report.mysql.latest_kline_1d_open_time_utc
    if latest is None:
        return "未初始化，需先执行手动 1d 回补"
    return _format_utc_prc(latest)


def _kline_1d_freshness_label(report: RuntimeStatusReport) -> str:
    latest = report.mysql.latest_kline_1d_open_time_utc
    expected = report.mysql.expected_latest_kline_1d_open_time_utc
    if latest is None:
        return "未初始化，scheduler 不会自动初始化历史日 K"
    if expected is None:
        return "无法确认理论最新已收盘日 K"
    if latest > expected:
        return "异常，疑似未收盘日 K 误写正式表"
    if latest == expected:
        return "正常"
    return f"滞后，理论最新已收盘日 K 为 {format_datetime_with_timezone(expected)}"


def _overall_sentence(level: RuntimeStatusLevel) -> str:
    return {
        RuntimeStatusLevel.NORMAL: "系统当前运行正常。",
        RuntimeStatusLevel.NOTICE: "系统基本正常，有少量信息需要关注。",
        RuntimeStatusLevel.WARNING: "系统存在需要关注的警告，请结合关键问题排查。",
        RuntimeStatusLevel.ERROR: "系统存在错误，请尽快检查服务、Redis、MySQL 或告警链路。",
        RuntimeStatusLevel.CRITICAL: "系统存在严重异常，请优先检查核心服务、Redis、MySQL 与告警提交链路。",
    }[level]


def _service_summary(report: RuntimeStatusReport) -> str:
    inactive = [service.display_name for service in report.services if service.level is RuntimeStatusLevel.ERROR]
    unknown = [service.display_name for service in report.services if service.level is RuntimeStatusLevel.WARNING]
    if not inactive and not unknown:
        return "10 秒价格监控、调度器、Hermes 网关均在运行。"
    parts: list[str] = []
    if inactive:
        parts.append(f"{'、'.join(inactive)} 未运行")
    if unknown:
        parts.append(f"{'、'.join(unknown)} 状态未知")
    return "；".join(parts) + "。"


def _data_summary(report: RuntimeStatusReport) -> str:
    latest_4h = (
        format_datetime_with_timezone(report.mysql.latest_kline_open_time_utc)
        if report.mysql.latest_kline_open_time_utc
        else "未知"
    )
    latest_1d = (
        format_datetime_with_timezone(report.mysql.latest_kline_1d_open_time_utc)
        if report.mysql.latest_kline_1d_open_time_utc
        else "未初始化，需先执行手动 1d 回补"
    )
    collector_4h = _collector_status_label(report.mysql.latest_collector_status)
    daily_4h = _daily_quality_status_label(report.mysql.latest_daily_quality_status)
    collector_1d = _collector_status_label(report.mysql.latest_1d_collector_status, report.mysql.latest_1d_collector_message)
    daily_1d = _daily_1d_quality_status_label(report)
    return (
        f"4h：最新 {DEFAULT_KLINE_SYMBOL} {KLINE_4H_INTERVAL_VALUE} K线为 {latest_4h}，"
        f"最近采集{collector_4h}，每日复核{daily_4h}。\n"
        f"1d：最新 {DEFAULT_KLINE_SYMBOL} {KLINE_1D_INTERVAL_VALUE} 日 K 为 {latest_1d}，"
        f"最近增量采集{collector_1d}，每日复核{daily_1d}。"
    )
