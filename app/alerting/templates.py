"""固定报警模板。

本文件属于 `app/alerting` 报警模块，负责把 `AlertEvent` 渲染为固定文案。
本文件不负责发送 Hermes，不连接 MySQL，不读写 Redis，不请求 Binance，
不调用 DeepSeek 或其他大模型，不生成交易建议，不涉及任何交易执行。
主要被 `app/alerting/service.py`、检查脚本和测试调用。
"""

from __future__ import annotations

from typing import Mapping

from app.alerting.sanitizer import sanitize_mapping, sanitize_text
from app.alerting.types import AlertEvent, AlertType
from app.core.exceptions import ValidationError
from app.core.time_utils import format_datetime_with_timezone, utc_aware_to_prc_aware

NOT_TRADING_ADVICE_TEXT = "本提醒不是交易建议，不包含自动交易动作。"
KLINE_BOUNDARY_TEXT = "系统没有自动修复数据，没有人工改数，也没有执行自动交易。"

TEMPLATE_TITLES: Mapping[AlertType, str] = {
    AlertType.SYSTEM_CHECK: "系统检查提醒",
    AlertType.INFRA_ERROR: "基础设施异常提醒",
    AlertType.DATA_QUALITY_ERROR: "数据质量异常提醒",
    AlertType.COLLECTOR_ERROR: "采集流程异常提醒",
    AlertType.PRICE_MONITOR_ERROR: "价格监控异常提醒",
    AlertType.SYSTEM_ERROR: "系统异常提醒",
    AlertType.MYSQL_ERROR: "MySQL 基础设施异常提醒",
    AlertType.REDIS_ERROR: "Redis 基础设施异常提醒",
    AlertType.KLINE_DATA_QUALITY_ERROR: "K 线数据质量异常提醒",
    AlertType.KLINE_INTEGRITY_CHECK_FAILED: "K 线一致性复核异常提醒",
    AlertType.KLINE_INTEGRITY_CHECK_PASSED: "K 线健康检查通过提醒",
    AlertType.MANUAL_TEST_ALERT: "人工测试提醒",
}

KLINE_RELATED_ALERT_TYPES = frozenset(
    {
        AlertType.DATA_QUALITY_ERROR,
        AlertType.COLLECTOR_ERROR,
        AlertType.KLINE_DATA_QUALITY_ERROR,
        AlertType.KLINE_INTEGRITY_CHECK_FAILED,
        AlertType.KLINE_INTEGRITY_CHECK_PASSED,
    }
)


def _format_details(details: Mapping[str, object]) -> str:
    sanitized = sanitize_mapping(details)
    if not sanitized:
        return "- 无额外上下文"

    lines: list[str] = []
    for key in sorted(sanitized):
        lines.append(f"- {sanitize_text(key)}: {sanitize_text(sanitized[key])}")
    return "\n".join(lines)


def render_alert_message(event: AlertEvent) -> str:
    """渲染固定模板报警文案。

    参数：`event` 是已校验的报警事件。
    返回值：固定模板字符串，可交给 Hermes client 发送。
    失败场景：缺少固定模板时抛出 `ValidationError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不调用 DeepSeek，不生成交易建议，不自动修复数据。
    """

    if event.alert_type not in TEMPLATE_TITLES:
        raise ValidationError(f"缺少固定报警模板：{event.alert_type.value}")

    occurred_at_utc = format_datetime_with_timezone(event.occurred_at_utc)
    occurred_at_prc = format_datetime_with_timezone(
        utc_aware_to_prc_aware(event.occurred_at_utc)
    )
    title = sanitize_text(event.title or TEMPLATE_TITLES[event.alert_type])
    summary = sanitize_text(event.summary)
    details = _format_details(event.details)

    boundary_text = KLINE_BOUNDARY_TEXT if event.alert_type in KLINE_RELATED_ALERT_TYPES else ""
    boundary_block = f"\n{boundary_text}" if boundary_text else ""

    return (
        f"[{TEMPLATE_TITLES[event.alert_type]}]\n"
        f"级别：{event.severity.value}\n"
        f"标题：{title}\n"
        f"摘要：{summary}\n"
        f"来源：{sanitize_text(event.source)}\n"
        f"追踪ID：{sanitize_text(event.trace_id)}\n"
        f"发生时间：{occurred_at_utc} / {occurred_at_prc}\n"
        f"上下文：\n{details}\n"
        f"{NOT_TRADING_ADVICE_TEXT}{boundary_block}"
    )


def supported_alert_type_values() -> tuple[str, ...]:
    """返回当前固定模板支持的报警类型。

    参数：无。
    返回值：报警类型字符串元组。
    失败场景：无预期失败场景。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数只用于检查和测试，不负责发送报警。
    """

    return tuple(alert_type.value for alert_type in TEMPLATE_TITLES)

