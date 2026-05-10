"""统一报警模块包边界。

本包承载 04 阶段 Hermes 固定模板报警能力。
本包不负责请求 Binance，不实现 K 线采集、策略建议、DeepSeek 调用或自动交易。
默认测试与检查脚本不真实发送 Hermes，真实发送只能由用户显式参数触发。
"""

from app.alerting.service import format_alert_message, send_alert, send_test_alert
from app.alerting.types import AlertEvent, AlertSeverity, AlertType

__all__ = [
    "AlertEvent",
    "AlertSeverity",
    "AlertType",
    "format_alert_message",
    "send_alert",
    "send_test_alert",
]

