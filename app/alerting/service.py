"""报警业务服务。

调用链：
用户 CLI
    ↓
scripts/check_alerting.py::main
    ↓
scripts/check_alerting.py::collect_alerting_errors
    ↓
app/alerting/service.py::send_test_alert
    ↓
app/alerting/service.py::send_alert
    ↓
app/alerting/templates.py::render_alert_message
    ↓
app/alerting/hermes_client.py::HermesClient.send_alert_message

本文件属于 `app/alerting` 报警模块，负责固定模板渲染、可选报警记录写入、
调用 Hermes client 并返回发送结果。
本文件不直接拼 webhook，不请求 Binance，不读写 Redis，不调用 DeepSeek，
不生成交易建议，不涉及任何交易执行。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.alerting.hermes_client import HermesClient
from app.alerting.sanitizer import sanitize_text
from app.alerting.templates import render_alert_message
from app.alerting.types import AlertEvent, AlertSendResult, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.core.logger import get_logger


class AlertMessageRepositoryProtocol(Protocol):
    """报警记录仓储协议。

    参数：实现方需要提供 pending 创建和结果更新方法。
    返回值：由实现方返回 ORM 记录或测试替身。
    失败场景：实现方可抛出数据库相关异常，service 会记录脱敏日志后继续发送。
    外部服务：协议本身不访问外部服务。
    数据影响：实现方可能在调用方显式传入 session 时写 MySQL。
    本协议不负责 Hermes 发送、Redis 写入或自动交易。
    """

    def create_pending_alert_message(
        self,
        db_session: Any,
        event: AlertEvent,
        message: str,
    ) -> Any:
        ...

    def update_alert_message_result(
        self,
        db_session: Any,
        alert_message: Any,
        result: AlertSendResult,
    ) -> Any:
        ...


def format_alert_message(event: AlertEvent) -> str:
    """格式化固定模板报警文案。

    参数：`event` 是报警事件。
    返回值：固定模板字符串。
    失败场景：未知报警类型时由模板模块抛出 `ValidationError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不调用 DeepSeek，不生成交易建议。
    """

    return render_alert_message(event)


def send_alert(
    event: AlertEvent,
    *,
    settings: AppSettings | None = None,
    client: HermesClient | None = None,
    repository: AlertMessageRepositoryProtocol | None = None,
    db_session: Any | None = None,
    send_real_alert: bool = False,
) -> AlertSendResult:
    """发送固定模板报警。

    参数：`event` 是报警事件；`settings` 是可选配置；`client` 可注入 mock；
    `repository` 与 `db_session` 同时传入时才会写报警记录；
    `send_real_alert` 必须显式为 True 才允许真实 Hermes 发送。
    返回值：`AlertSendResult`。
    失败场景：模板缺失、仓储异常、Hermes 发送失败或配置禁止真实发送。
    外部服务：只有 client 在真实发送条件满足时才访问 Hermes。
    数据影响：默认不写 MySQL；只有调用方显式传入仓储和 session 才记录报警。
    本函数不读写 Redis，不请求 Binance，不调用 DeepSeek，不执行交易动作。
    """

    active_settings = settings or get_settings()
    active_client = client or HermesClient(active_settings)
    logger = get_logger("alerting.service")
    message = format_alert_message(event)
    alert_message_record: Any | None = None

    if repository is not None and db_session is not None:
        try:
            alert_message_record = repository.create_pending_alert_message(db_session, event, message)
        except Exception as exc:  # noqa: BLE001 - 仓储失败需脱敏记录并继续尝试报警发送。
            logger.error(
                "报警记录创建失败，alert_type=%s error=%s",
                event.alert_type.value,
                sanitize_text(
                    exc,
                    (active_settings.hermes_webhook_url, active_settings.hermes_secret),
                ),
            )

    result = active_client.send_alert_message(
        event,
        message,
        send_real_alert=send_real_alert,
    )

    if repository is not None and db_session is not None and alert_message_record is not None:
        try:
            repository.update_alert_message_result(db_session, alert_message_record, result)
        except Exception as exc:  # noqa: BLE001 - 记录结果失败不应改写 Hermes 发送结果。
            logger.error(
                "报警结果记录失败，alert_type=%s error=%s",
                event.alert_type.value,
                sanitize_text(
                    exc,
                    (active_settings.hermes_webhook_url, active_settings.hermes_secret),
                ),
            )

    return result


def send_test_alert(
    *,
    settings: AppSettings | None = None,
    client: HermesClient | None = None,
    send_real_alert: bool = False,
) -> AlertSendResult:
    """发送或 dry-run 一条人工测试报警。

    参数：`settings` 是可选配置；`client` 可注入 mock；
    `send_real_alert` 必须显式为 True 才允许真实 Hermes 发送。
    返回值：`AlertSendResult`。
    失败场景：配置禁止真实发送时返回 skipped，Hermes 提交失败时返回 submit_failed 或 gateway_rejected。
    外部服务：默认不访问外部服务；只有显式真实发送且配置允许才访问 Hermes。
    数据影响：不写 MySQL，不读写 Redis，不发送 DeepSeek。
    本函数只用于人工检查脚本，不涉及 scheduler 或自动交易。
    """

    event = AlertEvent(
        alert_type=AlertType.SYSTEM_CHECK,
        severity=AlertSeverity.INFO,
        title="Hermes 报警模块人工检查",
        summary="固定模板渲染与发送边界检查通过。",
        details={
            "mode": "real" if send_real_alert else "dry-run",
            "safe_default": "默认不真实发送 Hermes。",
        },
        source="scripts.check_alerting",
    )
    return send_alert(
        event,
        settings=settings,
        client=client,
        send_real_alert=send_real_alert,
    )
