"""报警类型定义。

本文件属于 `app/alerting` 报警模块，负责定义报警事件、Hermes 请求响应
和发送结果的数据结构。
本文件不负责发送 Hermes，不渲染模板，不连接 MySQL，不读写 Redis，
不请求 Binance，不调用 DeepSeek，不涉及任何交易执行。
主要被 `app/alerting/service.py`、`app/alerting/hermes_client.py`、测试和后续业务服务调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping
from uuid import uuid4

from app.core.exceptions import ValidationError
from app.core.time_utils import UTC, now_utc


class AlertSeverity(str, Enum):
    """报警严重级别枚举。

    参数：无。
    返回值：枚举值可作为固定模板和 `alert_message` 记录中的严重级别。
    失败场景：调用方传入非法字符串时由 `coerce_alert_severity()` 抛出 `ValidationError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本枚举不表达策略信号或交易动作。
    """

    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertType(str, Enum):
    """固定模板报警类型枚举。

    参数：无。
    返回值：枚举值对应 `app/alerting/templates.py` 中的固定模板。
    失败场景：调用方传入非法字符串时由 `coerce_alert_type()` 抛出 `ValidationError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本枚举只描述系统提醒类型，不包含交易执行动作。
    """

    SYSTEM_CHECK = "system_check"
    INFRA_ERROR = "infra_error"
    DATA_QUALITY_ERROR = "data_quality_error"
    COLLECTOR_ERROR = "collector_error"
    PRICE_MONITOR_ERROR = "price_monitor_error"
    SYSTEM_ERROR = "system_error"
    MYSQL_ERROR = "mysql_error"
    REDIS_ERROR = "redis_error"
    KLINE_DATA_QUALITY_ERROR = "kline_data_quality_error"
    KLINE_INTEGRITY_CHECK_FAILED = "kline_integrity_check_failed"
    KLINE_INTEGRITY_CHECK_PASSED = "kline_integrity_check_passed"
    MANUAL_BACKFILL_NOTICE = "manual_backfill_notice"
    STRATEGY_SIGNAL_SCHEDULER = "strategy_signal_scheduler"
    MANUAL_TEST_ALERT = "manual_test_alert"


class AlertSendStatus(str, Enum):
    """报警发送结果状态。

    参数：无。
    返回值：枚举值用于 service 返回和 `alert_message.status`。
    失败场景：无预期失败场景。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本枚举不负责重试或补偿队列。
    """

    PENDING = "pending"
    SUBMITTED_TO_HERMES = "submitted_to_hermes"
    GATEWAY_REJECTED = "gateway_rejected"
    SUBMIT_FAILED = "submit_failed"
    SKIPPED = "skipped"


class AlertGatewayStatus(str, Enum):
    """Hermes gateway submission status.

    This status describes only the BTC Agent -> Hermes gateway boundary.
    It never claims that Weixin/iLink or any other target channel delivered
    the alert to the final recipient.
    """

    NOT_ATTEMPTED = "not_attempted"
    GATEWAY_ACCEPTED = "gateway_accepted"
    GATEWAY_REJECTED = "gateway_rejected"
    SUBMIT_FAILED = "submit_failed"


class AlertFinalDeliveryStatus(str, Enum):
    """Final target-channel delivery status as known by BTC Agent.

    The current Hermes webhook integration does not synchronously return the
    final Weixin/iLink delivery outcome, so production code must use UNKNOWN.
    DELIVERED and DELIVERY_FAILED are reserved for a future explicit Hermes
    delivery-result contract.
    """

    UNKNOWN = "unknown"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"


def coerce_alert_type(value: AlertType | str) -> AlertType:
    """将字符串转换为固定报警类型。

    参数：`value` 是 `AlertType` 或其字符串值。
    返回值：`AlertType`。
    失败场景：未知报警类型抛出 `ValidationError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责猜测模板或扩大报警类型范围。
    """

    if isinstance(value, AlertType):
        return value
    try:
        return AlertType(value)
    except ValueError as exc:
        raise ValidationError(f"不支持的报警类型：{value}") from exc


def coerce_alert_severity(value: AlertSeverity | str) -> AlertSeverity:
    """将字符串转换为报警严重级别。

    参数：`value` 是 `AlertSeverity` 或其字符串值。
    返回值：`AlertSeverity`。
    失败场景：未知严重级别抛出 `ValidationError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责提升或降低业务严重级别。
    """

    if isinstance(value, AlertSeverity):
        return value
    try:
        return AlertSeverity(value)
    except ValueError as exc:
        raise ValidationError(f"不支持的报警严重级别：{value}") from exc


@dataclass
class AlertEvent:
    """报警事件。

    参数：`alert_type` 指定固定模板；`severity` 指定级别；`summary` 是脱敏摘要；
    `details` 是可选脱敏上下文；`source` 是发起模块；`occurred_at_utc` 是 UTC 事件时间。
    返回值：报警事件对象。
    失败场景：非法类型、非法级别或 naive 时间会抛出 `ValidationError`。
    外部服务：本对象不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本对象不表达交易建议，不包含自动交易动作。
    """

    alert_type: AlertType | str
    severity: AlertSeverity | str
    title: str
    summary: str
    details: Mapping[str, object] = field(default_factory=dict)
    source: str = "unknown"
    occurred_at_utc: datetime = field(default_factory=now_utc)
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        self.alert_type = coerce_alert_type(self.alert_type)
        self.severity = coerce_alert_severity(self.severity)
        if self.occurred_at_utc.tzinfo is None:
            raise ValidationError("AlertEvent.occurred_at_utc 必须是 UTC aware datetime")
        self.occurred_at_utc = self.occurred_at_utc.astimezone(UTC)


@dataclass(frozen=True)
class HermesRequest:
    """Hermes webhook 请求对象。

    参数：`payload` 是待发送 JSON 内容；`body` 是签名使用的 UTF-8 JSON bytes。
    返回值：不可变请求对象。
    失败场景：由客户端构造阶段处理 JSON 序列化错误。
    外部服务：本对象不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本对象不保存 webhook、secret 或认证头。
    """

    payload: Mapping[str, object]
    body: bytes


@dataclass(frozen=True)
class HermesResponse:
    """Hermes webhook 响应摘要。

    参数：`status_code` 是 HTTP 状态码；`body` 和 `headers` 必须先脱敏再保存。
    返回值：不可变响应摘要。
    失败场景：无预期失败场景。
    外部服务：本对象不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本对象不保存未脱敏 channel_response。
    """

    status_code: int
    body: str = ""
    headers: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AlertSendResult:
    """报警发送结果。

    参数：`status` 是提交 Hermes 的状态，不表示微信/iLink 最终送达；
    `channel_response` 是脱敏后的响应摘要；
    `attempted_real_send` 标识是否真的尝试访问 Hermes。
    返回值：不可变结果对象。
    失败场景：由 service 或 client 根据失败原因构造 submit_failed 或 gateway_rejected 结果。
    外部服务：本对象不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本对象不表示交易建议或交易执行结果。
    """

    status: AlertSendStatus
    channel: str = "hermes"
    message: str = ""
    http_status_code: int | None = None
    gateway_status: AlertGatewayStatus = AlertGatewayStatus.NOT_ATTEMPTED
    final_delivery_status: AlertFinalDeliveryStatus = AlertFinalDeliveryStatus.UNKNOWN
    channel_response: Mapping[str, object] = field(default_factory=dict)
    error_message: str = ""
    retry_count: int = 0
    attempted_real_send: bool = False
    submitted_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        if self.gateway_status != AlertGatewayStatus.NOT_ATTEMPTED:
            return
        inferred_gateway_status = {
            AlertSendStatus.SUBMITTED_TO_HERMES: AlertGatewayStatus.GATEWAY_ACCEPTED,
            AlertSendStatus.GATEWAY_REJECTED: AlertGatewayStatus.GATEWAY_REJECTED,
            AlertSendStatus.SUBMIT_FAILED: AlertGatewayStatus.SUBMIT_FAILED,
        }.get(self.status)
        if inferred_gateway_status is not None:
            object.__setattr__(self, "gateway_status", inferred_gateway_status)
