"""alert_message 报警记录 Repository。

本文件属于 `app/storage/mysql/repositories` 存储层，负责创建报警待发送记录
和更新 Hermes 发送结果。
本文件不负责发送 Hermes，不负责固定模板渲染，不读写 Redis，不请求 Binance，
不调用 DeepSeek，不涉及任何交易执行。
主要被 `app/alerting/service.py` 在调用方显式传入 session 时调用。
"""

from __future__ import annotations

from typing import Any

from app.alerting.sanitizer import sanitize_mapping, sanitize_text
from app.alerting.types import AlertEvent, AlertSendResult, AlertSendStatus
from app.core.time_utils import now_utc
from app.storage.mysql.models.alert_message import AlertMessage


class AlertMessageRepository:
    """报警记录 Repository。

    参数：无。
    返回值：Repository 实例。
    失败场景：调用方传入不可用 session 时由 session.add/flush 抛出异常。
    外部服务：不访问外部 HTTP 服务。
    数据影响：只在调用方显式传入 MySQL session 后写入 `alert_message`。
    本类不直接发送 Hermes，不读写 Redis，不执行 migration，不自动交易。
    """

    def create_pending_alert_message(
        self,
        db_session: Any,
        event: AlertEvent,
        message: str,
    ) -> AlertMessage:
        """创建 pending 报警记录。

        参数：`db_session` 是调用方提供的 SQLAlchemy session；
        `event` 是报警事件；`message` 是固定模板文案。
        返回值：新增的 `AlertMessage` 记录。
        失败场景：session 不可用、数据库写入失败或字段非法时抛出异常。
        外部服务：不访问外部服务。
        数据影响：写入 `alert_message`，不写其他表，不读写 Redis，不发送 Hermes。
        本方法不负责 commit、rollback、migration 或自动交易。
        """

        now = now_utc()
        record = AlertMessage(
            alert_type=event.alert_type.value,
            severity=event.severity.value,
            title=sanitize_text(event.title),
            message=sanitize_text(message),
            channel="hermes",
            status=AlertSendStatus.PENDING.value,
            source=sanitize_text(event.source),
            trace_id=sanitize_text(event.trace_id),
            channel_response=None,
            error_message=None,
            retry_count=0,
            http_status_code=None,
            occurred_at_utc=event.occurred_at_utc,
            sent_at_utc=None,
            created_at_utc=now,
            updated_at_utc=now,
        )
        db_session.add(record)
        if hasattr(db_session, "flush"):
            db_session.flush()
        return record

    def update_alert_message_result(
        self,
        db_session: Any,
        alert_message: AlertMessage,
        result: AlertSendResult,
    ) -> AlertMessage:
        """更新报警发送结果。

        参数：`db_session` 是调用方提供的 SQLAlchemy session；
        `alert_message` 是待更新记录；`result` 是 Hermes client 返回的脱敏结果。
        返回值：更新后的 `AlertMessage`。
        失败场景：session 不可用或数据库更新失败时抛出异常。
        外部服务：不访问外部服务。
        数据影响：只更新 `alert_message`，不写其他表，不读写 Redis，不发送 Hermes。
        本方法不负责 commit、rollback、补偿队列或自动交易。
        """

        alert_message.status = result.status.value
        alert_message.channel_response = sanitize_mapping(result.channel_response)
        alert_message.error_message = sanitize_text(result.error_message) if result.error_message else None
        alert_message.retry_count = result.retry_count
        alert_message.http_status_code = result.http_status_code
        # Physical column name is kept for the existing schema; it now stores
        # the Hermes gateway submission timestamp, not final Weixin delivery.
        alert_message.sent_at_utc = result.submitted_at_utc
        alert_message.updated_at_utc = now_utc()
        if hasattr(db_session, "flush"):
            db_session.flush()
        return alert_message


def create_default_alert_message_repository() -> AlertMessageRepository:
    """创建默认报警记录 Repository。

    参数：无。
    返回值：`AlertMessageRepository` 实例。
    失败场景：无预期失败场景。
    外部服务：不访问外部服务。
    数据影响：不连接 MySQL，不读写 Redis，不发送 Hermes。
    本函数只创建对象，不负责 session 生命周期或自动交易。
    """

    return AlertMessageRepository()
