"""Fixed-template Hermes alerting for 26B strategy evidence quality failures.

本文件属于 `app/strategy/evidence_quality` 模块。
本文件负责把 26B blocking failure 渲染为系统重大告警，并通过统一
`app/alerting` Hermes client 与 `alert_message` repository 发送/记录。
本文件不负责质量判定，不负责 pipeline 编排，不请求 Binance，不读写 Redis，
不调用 DeepSeek 或其他大模型，不读取账户或仓位，不生成订单，不自动交易。
主要被 `service.py::StrategyEvidenceQualityGateService` 调用。
外部服务：仅在调用方显式允许真实发送且 Hermes 全局配置允许时访问 Hermes。
MySQL：可写 `alert_message`，不写其他业务表。Redis：无。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.alerting.hermes_client import HermesClient
from app.alerting.service import format_alert_message
from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSendResult, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.core.logger import get_logger
from app.core.time_utils import format_datetime_with_timezone, now_utc
from app.storage.mysql.repositories.alert_message_repository import (
    AlertMessageRepository,
    create_default_alert_message_repository,
)
from app.strategy.evidence_quality.types import StrategyEvidenceQualityGateResult


@dataclass(frozen=True)
class StrategyEvidenceQualityAlertResult:
    """Result of one 26B Hermes system-alert attempt."""

    alert_status: str
    alert_message_id: int | None
    error_message: str | None
    attempted_real_send: bool
    rendered_message: str


def send_strategy_evidence_quality_failure_alert(
    db_session: Any,
    *,
    quality_result: StrategyEvidenceQualityGateResult,
    settings: AppSettings | None = None,
    alert_repository: AlertMessageRepository | Any | None = None,
    hermes_client: HermesClient | Any | None = None,
    send_real_alert: bool = True,
) -> StrategyEvidenceQualityAlertResult:
    """Send one fixed-template 26B critical system alert.

    Parameters:
    - `quality_result`: failed 26B result containing compact strategy/role/field
      failure summaries.
    - `send_real_alert`: explicit real Hermes gate from 26B config.

    Return value: alert status, optional `alert_message.id`, sanitized error
    message and the rendered fixed-template body.
    Failure scenarios: template rendering, repository writes or Hermes client
    errors may raise; the caller catches them and records failure in pipeline
    details. This function does not rollback the caller-owned database session.
    External services: Hermes only through `HermesClient.send_alert_message`.
    Data impact: may write/update `alert_message`; it does not write 26B quality
    rows, Kline tables, material packs, model rows, advice rows or Redis keys.
    """

    active_settings = settings or get_settings()
    active_repository = alert_repository or create_default_alert_message_repository()
    active_client = hermes_client or HermesClient(active_settings)
    event = build_strategy_evidence_quality_failure_event(quality_result)
    message = format_alert_message(event)
    alert_row = None
    logger = get_logger("strategy.evidence_quality.alerting")

    try:
        alert_row = active_repository.create_pending_alert_message(
            db_session,
            event,
            message,
            related_type="strategy_evidence_quality_check",
            related_id=quality_result.quality_check_id,
        )
    except Exception as exc:  # noqa: BLE001 - alert row failure must not prevent Hermes attempt.
        logger.error(
            "26B alert_message create failed, quality_check_id=%s error=%s",
            quality_result.quality_check_id,
            exc,
        )

    send_result = active_client.send_alert_message(event, message, send_real_alert=send_real_alert)
    if alert_row is not None:
        active_repository.update_alert_message_result(db_session, alert_row, send_result)

    return _alert_result_from_send_result(send_result, alert_row=alert_row, rendered_message=message)


def build_strategy_evidence_quality_failure_event(
    quality_result: StrategyEvidenceQualityGateResult,
) -> AlertEvent:
    """Build a Chinese fixed-template system alert for a 26B blocking failure."""

    body = _visible_body(quality_result)
    return AlertEvent(
        alert_type=AlertType.STRATEGY_EVIDENCE_QUALITY_FAILURE,
        severity=AlertSeverity.CRITICAL,
        title="策略证据质量重大异常",
        summary="策略证据质量重大异常，已阻断 18 材料包，未调用大模型，未生成策略建议，未自动交易。",
        details={
            WECHAT_VISIBLE_BODY_DETAIL_KEY: body,
            "symbol": quality_result.symbol,
            "base_interval": quality_result.base_interval,
            "higher_interval": quality_result.higher_interval,
            "kline_slot_utc": _datetime_text(quality_result.kline_slot_utc),
            "pipeline_run_id": quality_result.pipeline_run_id or "",
            "strategy_signal_run_id": quality_result.strategy_signal_run_id,
            "strategy_evidence_aggregation_id": quality_result.strategy_evidence_aggregation_id,
            "quality_check_id": quality_result.quality_check_id,
            "failed_strategies": list(quality_result.failed_strategies),
            "failed_roles": list(quality_result.failed_roles),
            "missing_fields": list(quality_result.missing_fields),
            "not_trading_advice": True,
            "blocked_18_material_pack": True,
            "large_model_called": False,
            "strategy_advice_generated": False,
            "auto_trading": False,
        },
        source="hermes_btc_agent.strategy_evidence_quality_gate",
        occurred_at_utc=now_utc(),
        trace_id=quality_result.trace_id,
    )


def _visible_body(quality_result: StrategyEvidenceQualityGateResult) -> str:
    failed_strategies = _bullet_lines(quality_result.failed_strategies, empty="无")
    failed_roles = _bullet_lines(quality_result.failed_roles, empty="无")
    reasons = _bullet_lines(
        tuple(issue.reason for issue in quality_result.failed_checks),
        empty=quality_result.error_message or "未提供失败原因",
    )
    return "\n".join(
        [
            "【策略证据质量重大异常】",
            "",
            f"symbol / interval：{quality_result.symbol} {quality_result.base_interval} / {quality_result.higher_interval}",
            f"kline_slot_utc：{_datetime_text(quality_result.kline_slot_utc)}",
            f"pipeline_run_id：{quality_result.pipeline_run_id or ''}",
            f"strategy_signal_run_id：{quality_result.strategy_signal_run_id}",
            f"strategy_evidence_aggregation_id：{quality_result.strategy_evidence_aggregation_id}",
            "",
            "失败策略列表：",
            failed_strategies,
            "",
            "失败角色列表：",
            failed_roles,
            "",
            "缺失字段 / 失败原因：",
            reasons,
            "",
            "处理结果：",
            "- 已阻断 18 材料包",
            "- 未调用大模型",
            "- 未生成策略建议",
            "- 未自动交易",
            "",
            "not_trading_advice=true",
            f"trace_id：{quality_result.trace_id}",
        ]
    )


def _alert_result_from_send_result(
    send_result: AlertSendResult,
    *,
    alert_row: Any | None,
    rendered_message: str,
) -> StrategyEvidenceQualityAlertResult:
    return StrategyEvidenceQualityAlertResult(
        alert_status=send_result.status.value,
        alert_message_id=_alert_message_id(alert_row),
        error_message=send_result.error_message or None,
        attempted_real_send=bool(send_result.attempted_real_send),
        rendered_message=rendered_message,
    )


def _alert_message_id(alert_row: Any | None) -> int | None:
    if alert_row is None:
        return None
    value = getattr(alert_row, "id", None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bullet_lines(values: tuple[str, ...] | list[str], *, empty: str) -> str:
    items = tuple(str(value).strip() for value in values if str(value).strip())
    if not items:
        return f"- {empty}"
    return "\n".join(f"- {item}" for item in items[:30])


def _datetime_text(value: Any) -> str:
    if value is None:
        return ""
    return format_datetime_with_timezone(value)


__all__ = [
    "StrategyEvidenceQualityAlertResult",
    "build_strategy_evidence_quality_failure_event",
    "send_strategy_evidence_quality_failure_alert",
]
