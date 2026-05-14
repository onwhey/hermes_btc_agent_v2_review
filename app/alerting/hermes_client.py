"""Hermes webhook client。

本文件属于 `app/alerting` 报警模块，负责构造 Hermes webhook JSON 请求、
按显式配置发送固定模板报警，并返回脱敏后的发送结果。
本文件不负责模板决策，不连接 MySQL，不读写 Redis，不请求 Binance，
不调用 DeepSeek，不生成交易建议，不涉及任何交易执行。
主要被 `app/alerting/service.py` 和人工检查脚本调用。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from app.alerting.sanitizer import REDACTED_TEXT, sanitize_mapping, sanitize_text
from app.alerting.types import (
    AlertEvent,
    AlertFinalDeliveryStatus,
    AlertGatewayStatus,
    AlertSendResult,
    AlertSendStatus,
    HermesRequest,
    HermesResponse,
)
from app.core.config import AppSettings, get_settings
from app.core.exceptions import HermesError
from app.core.logger import get_logger
from app.core.time_utils import now_utc

HERMES_SIGNATURE_HEADER = "X-Webhook-Signature"
HERMES_EVENT_TYPE = "hermes_btc_agent.alert"
FINAL_DELIVERY_UNKNOWN_NOTE = (
    "Hermes webhook status only describes BTC Agent submission to the Hermes gateway; "
    "BTC Agent does not know final Weixin/iLink delivery."
)


@dataclass(frozen=True)
class HermesTransportResponse:
    """底层 HTTP 传输响应。

    参数：`status_code` 是 HTTP 状态码；`body` 和 `headers` 是原始响应摘要。
    返回值：不可变传输响应。
    失败场景：网络异常由传输函数抛出给 Hermes client 处理。
    外部服务：本对象不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本对象不负责脱敏或持久化。
    """

    status_code: int
    body: str = ""
    headers: Mapping[str, object] = field(default_factory=dict)


HermesHttpPost = Callable[[str, bytes, Mapping[str, str], float], HermesTransportResponse]


def build_hermes_payload(
    event: AlertEvent,
    message: str,
    *,
    extra_sensitive_values: Sequence[str] = (),
) -> dict[str, object]:
    """构造 Hermes webhook payload。

    参数：`event` 是报警事件；`message` 是固定模板渲染后的文案；
    `extra_sensitive_values` 是必须在发往 Hermes 前隐藏的真实敏感值。
    返回值：已脱敏的 JSON 可序列化字典。
    失败场景：调用方传入不可序列化 details 时由 JSON 序列化阶段抛出异常。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不读取 webhook，不包含 secret，不生成交易建议。
    """

    return {
        "event_type": HERMES_EVENT_TYPE,
        "alert_type": event.alert_type.value,
        "severity": event.severity.value,
        "title": sanitize_text(event.title, extra_sensitive_values),
        "message": sanitize_text(message, extra_sensitive_values),
        "source": sanitize_text(event.source, extra_sensitive_values),
        "trace_id": sanitize_text(event.trace_id, extra_sensitive_values),
        "occurred_at_utc": event.occurred_at_utc.isoformat(),
        "not_trading_advice": True,
    }


def build_hermes_request(
    event: AlertEvent,
    message: str,
    *,
    extra_sensitive_values: Sequence[str] = (),
) -> HermesRequest:
    """构造待签名 Hermes 请求。

    参数：`event` 是报警事件；`message` 是固定模板文案；
    `extra_sensitive_values` 是必须在发往 Hermes 前隐藏的真实敏感值。
    返回值：包含 payload 和 JSON bytes 的 `HermesRequest`。
    失败场景：JSON 序列化失败时抛出 `HermesError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责网络发送或自动交易。
    """

    payload = build_hermes_payload(
        event,
        message,
        extra_sensitive_values=extra_sensitive_values,
    )
    try:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HermesError("Hermes payload 无法 JSON 序列化") from exc
    return HermesRequest(payload=payload, body=body)


def build_hermes_headers(body: bytes, settings: AppSettings) -> dict[str, str]:
    """构造 Hermes webhook 请求头。

    参数：`body` 是原始 JSON bytes；`settings` 提供可选 HMAC secret。
    返回值：请求头字典；不会包含明文 secret。
    失败场景：无预期失败场景。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责打印请求头或保存认证信息。
    """

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "hermes-btc-agent/0.1",
    }
    if settings.hermes_secret:
        signature = hmac.new(
            settings.hermes_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        headers[HERMES_SIGNATURE_HEADER] = signature
    return headers


def _default_http_post(
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    timeout: float,
) -> HermesTransportResponse:
    request = urllib.request.Request(url=url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return HermesTransportResponse(
                status_code=response.status,
                body=response_body,
                headers=dict(response.headers.items()),
            )
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        return HermesTransportResponse(
            status_code=exc.code,
            body=response_body,
            headers=dict(exc.headers.items()) if exc.headers else {},
        )


class HermesClient:
    """Hermes webhook 客户端。

    参数：`settings` 提供 Hermes 配置；`http_post` 可注入 mock 传输函数。
    返回值：client 实例。
    失败场景：真实发送时可能因配置缺失、超时、网络失败或 HTTP 非 2xx 返回 submit_failed 或 gateway_rejected。
    外部服务：只有 `send_alert_message(..., send_real_alert=True)` 且配置允许时才访问 Hermes。
    数据影响：不读写 MySQL，不读写 Redis，不保存 channel_response。
    本类不负责模板生成、报警入库、DeepSeek 调用或自动交易。
    """

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        http_post: HermesHttpPost | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._http_post = http_post or _default_http_post
        self._logger = get_logger("alerting.hermes_client")

    def _sensitive_values_for_payload(self) -> tuple[str, ...]:
        """返回发往 Hermes 前必须脱敏的配置敏感值。

        参数：无。
        返回值：当前 settings 中非空 webhook 和 secret。
        失败场景：无预期失败场景。
        外部服务：不访问外部服务。
        数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
        本方法只为 payload 构造提供脱敏输入，不负责网络发送或自动交易。
        """

        return tuple(
            value
            for value in (
                self._settings.hermes_webhook_url,
                self._settings.hermes_secret,
            )
            if value
        )

    def _skipped_result(self, reason: str) -> AlertSendResult:
        return AlertSendResult(
            status=AlertSendStatus.SKIPPED,
            message=reason,
            gateway_status=AlertGatewayStatus.NOT_ATTEMPTED,
            final_delivery_status=AlertFinalDeliveryStatus.UNKNOWN,
            channel_response={
                "gateway_status": AlertGatewayStatus.NOT_ATTEMPTED.value,
                "final_delivery_status": AlertFinalDeliveryStatus.UNKNOWN.value,
                "delivery_note": FINAL_DELIVERY_UNKNOWN_NOTE,
                "reason": sanitize_text(reason),
            },
            attempted_real_send=False,
        )

    def _failed_result(
        self,
        *,
        status: AlertSendStatus = AlertSendStatus.SUBMIT_FAILED,
        gateway_status: AlertGatewayStatus = AlertGatewayStatus.SUBMIT_FAILED,
        error_message: str,
        retry_count: int,
        http_status_code: int | None = None,
        response: HermesResponse | None = None,
        attempted_real_send: bool = True,
    ) -> AlertSendResult:
        sanitized_error = sanitize_text(
            error_message,
            (self._settings.hermes_webhook_url, self._settings.hermes_secret),
        )
        response_payload: dict[str, object] = {
            "gateway_status": gateway_status.value,
            "final_delivery_status": AlertFinalDeliveryStatus.UNKNOWN.value,
            "delivery_note": FINAL_DELIVERY_UNKNOWN_NOTE,
        }
        if response is not None:
            response_payload.update(
                {
                    "status_code": response.status_code,
                    "body": response.body,
                    "headers": response.headers,
                }
            )
        return AlertSendResult(
            status=status,
            message=(
                "Hermes gateway rejected alert submission; final delivery unknown"
                if status == AlertSendStatus.GATEWAY_REJECTED
                else "Hermes alert submission failed; final delivery unknown"
            ),
            http_status_code=http_status_code,
            gateway_status=gateway_status,
            final_delivery_status=AlertFinalDeliveryStatus.UNKNOWN,
            channel_response=sanitize_mapping(
                response_payload,
                (self._settings.hermes_webhook_url, self._settings.hermes_secret),
            ),
            error_message=sanitized_error,
            retry_count=retry_count,
            attempted_real_send=attempted_real_send,
        )

    def send_alert_message(
        self,
        event: AlertEvent,
        message: str,
        *,
        send_real_alert: bool = False,
    ) -> AlertSendResult:
        """发送固定模板报警文案到 Hermes。

        参数：`event` 是报警事件；`message` 是固定模板文案；
        `send_real_alert` 必须显式为 True 才允许真实发送。
        返回值：`AlertSendResult`，包含提交 Hermes 的状态、最终送达 unknown 和脱敏响应。
        失败场景：配置未启用、dry-run、webhook 缺失、网络失败或 HTTP 非 2xx。
        外部服务：只有真实发送条件全部满足时才访问 Hermes。
        数据影响：不读写 MySQL，不读写 Redis，不保存 channel_response。
        本方法不调用 DeepSeek，不生成交易建议，不执行任何交易动作。
        """

        if not send_real_alert:
            return self._skipped_result("未显式要求真实发送，保持 dry-run")
        if not self._settings.hermes_enabled:
            return self._skipped_result("HERMES_ENABLED=false，跳过真实发送")
        if self._settings.hermes_dry_run:
            return self._skipped_result("HERMES_DRY_RUN=true，跳过真实发送")
        if not self._settings.hermes_webhook_url:
            return self._failed_result(
                error_message="HERMES_WEBHOOK_URL 未配置，无法真实发送 Hermes",
                retry_count=0,
                attempted_real_send=False,
            )
        if self._settings.hermes_timeout_seconds <= 0:
            return self._failed_result(
                error_message="HERMES_TIMEOUT_SECONDS 必须大于 0",
                retry_count=0,
                attempted_real_send=False,
            )

        sensitive_values = self._sensitive_values_for_payload()
        request = build_hermes_request(
            event,
            message,
            extra_sensitive_values=sensitive_values,
        )
        headers = build_hermes_headers(request.body, self._settings)
        max_retries = max(0, self._settings.hermes_max_retries)
        sanitized_header_keys = sorted(headers)
        self._logger.info(
            "用户手动显式触发 Hermes 真实发送，alert_type=%s severity=%s headers=%s",
            event.alert_type.value,
            event.severity.value,
            sanitized_header_keys,
        )

        last_response: HermesResponse | None = None
        for attempt_index in range(max_retries + 1):
            try:
                response = self._http_post(
                    self._settings.hermes_webhook_url,
                    request.body,
                    headers,
                    self._settings.hermes_timeout_seconds,
                )
                sanitized_response = HermesResponse(
                    status_code=response.status_code,
                    body=sanitize_text(
                        response.body,
                        (self._settings.hermes_webhook_url, self._settings.hermes_secret),
                    ),
                    headers=sanitize_mapping(
                        response.headers,
                        (self._settings.hermes_webhook_url, self._settings.hermes_secret),
                    ),
                )
                last_response = sanitized_response
                if 200 <= response.status_code < 300:
                    submitted_at_utc = now_utc()
                    self._logger.info(
                        "Hermes gateway accepted alert submission; final_delivery_status=unknown "
                        "trace_id=%s alert_type=%s http_status=%s",
                        event.trace_id,
                        event.alert_type.value,
                        response.status_code,
                    )
                    return AlertSendResult(
                        status=AlertSendStatus.SUBMITTED_TO_HERMES,
                        message="Submitted to Hermes gateway; final Weixin/iLink delivery unknown",
                        http_status_code=response.status_code,
                        gateway_status=AlertGatewayStatus.GATEWAY_ACCEPTED,
                        final_delivery_status=AlertFinalDeliveryStatus.UNKNOWN,
                        channel_response={
                            "gateway_status": AlertGatewayStatus.GATEWAY_ACCEPTED.value,
                            "final_delivery_status": AlertFinalDeliveryStatus.UNKNOWN.value,
                            "delivery_note": FINAL_DELIVERY_UNKNOWN_NOTE,
                            "status_code": sanitized_response.status_code,
                            "body": sanitized_response.body,
                            "headers": sanitized_response.headers,
                        },
                        retry_count=attempt_index,
                        attempted_real_send=True,
                        submitted_at_utc=submitted_at_utc,
                    )
            except Exception as exc:  # noqa: BLE001 - 需要把底层网络异常转换为脱敏失败结果。
                error_message = sanitize_text(
                    str(exc),
                    (self._settings.hermes_webhook_url, self._settings.hermes_secret),
                )
                if attempt_index >= max_retries:
                    self._logger.warning(
                        "Hermes alert submission failed; final_delivery_status=unknown "
                        "trace_id=%s alert_type=%s error=%s",
                        event.trace_id,
                        event.alert_type.value,
                        error_message,
                    )
                    return self._failed_result(
                        status=AlertSendStatus.SUBMIT_FAILED,
                        gateway_status=AlertGatewayStatus.SUBMIT_FAILED,
                        error_message=error_message,
                        retry_count=attempt_index,
                    )
            if attempt_index < max_retries:
                time.sleep(0)

        self._logger.warning(
            "Hermes gateway rejected alert submission; final_delivery_status=unknown "
            "trace_id=%s alert_type=%s http_status=%s",
            event.trace_id,
            event.alert_type.value,
            last_response.status_code if last_response else REDACTED_TEXT,
        )
        return self._failed_result(
            status=AlertSendStatus.GATEWAY_REJECTED,
            gateway_status=AlertGatewayStatus.GATEWAY_REJECTED,
            error_message=(
                "Hermes gateway rejected alert submission with HTTP "
                f"{last_response.status_code if last_response else REDACTED_TEXT}"
            ),
            retry_count=max_retries,
            http_status_code=last_response.status_code if last_response else None,
            response=last_response,
        )
