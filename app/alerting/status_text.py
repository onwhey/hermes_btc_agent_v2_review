"""Hermes 发送状态中文展示工具。

本文件属于 `app/alerting` 报警模块，负责把 `AlertSendResult` 中的
提交状态、Hermes 网关状态和最终送达状态转换为用户可读中文。
本文件不负责发送 Hermes，不连接 MySQL，不读写 Redis，不请求 Binance，
不调用 DeepSeek，不生成交易建议，不涉及任何交易执行。
主要被人工检查脚本和运行状态观测模块调用。
"""

from __future__ import annotations

from app.alerting.types import (
    AlertFinalDeliveryStatus,
    AlertGatewayStatus,
    AlertSendResult,
    AlertSendStatus,
)


def alert_send_status_label(status: AlertSendStatus | str) -> str:
    """返回报警提交状态的中文说明。"""

    value = status.value if isinstance(status, AlertSendStatus) else str(status)
    labels = {
        AlertSendStatus.PENDING.value: "等待提交 Hermes",
        AlertSendStatus.SUBMITTED_TO_HERMES.value: "已提交 Hermes",
        AlertSendStatus.GATEWAY_REJECTED.value: "Hermes 网关拒绝",
        AlertSendStatus.SUBMIT_FAILED.value: "提交 Hermes 失败",
        AlertSendStatus.SKIPPED.value: "未真实提交 Hermes",
    }
    return labels.get(value, f"未知提交状态：{value}")


def gateway_status_label(status: AlertGatewayStatus | str | None) -> str:
    """返回 Hermes 网关状态的中文说明。"""

    if status is None:
        return "未知"
    value = status.value if isinstance(status, AlertGatewayStatus) else str(status)
    labels = {
        AlertGatewayStatus.NOT_ATTEMPTED.value: "未尝试提交 Hermes",
        AlertGatewayStatus.GATEWAY_ACCEPTED.value: "Hermes 网关已接收",
        AlertGatewayStatus.GATEWAY_REJECTED.value: "Hermes 网关拒绝",
        AlertGatewayStatus.SUBMIT_FAILED.value: "提交 Hermes 失败",
    }
    return labels.get(value, f"未知网关状态：{value}")


def final_delivery_status_label(status: AlertFinalDeliveryStatus | str | None) -> str:
    """返回最终微信送达状态的中文说明。

    当前 Hermes webhook 同步响应不表示微信最终送达结果，因此 `unknown`
    是正常可解释状态，不能写成最终送达确认语。
    """

    if status is None:
        return "未知，BTC Agent 无法确认微信最终送达"
    value = status.value if isinstance(status, AlertFinalDeliveryStatus) else str(status)
    labels = {
        AlertFinalDeliveryStatus.UNKNOWN.value: "未知，BTC Agent 无法确认微信最终送达",
        AlertFinalDeliveryStatus.DELIVERED.value: "Hermes 返回最终通道成功状态，BTC Agent 不直接判定微信结果",
        AlertFinalDeliveryStatus.DELIVERY_FAILED.value: "Hermes 返回最终通道失败状态",
    }
    return labels.get(value, f"未知最终状态：{value}")


def render_alert_send_result_lines(result: AlertSendResult) -> list[str]:
    """渲染发送结果摘要行，供人工脚本打印。"""

    lines = [
        f"提交状态：{alert_send_status_label(result.status)}。",
        f"网关状态：{gateway_status_label(result.gateway_status)}。",
        f"最终微信送达状态：{final_delivery_status_label(result.final_delivery_status)}。",
    ]
    if result.error_message:
        lines.append(f"错误摘要：{result.error_message}")
    return lines
