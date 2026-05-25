"""Chinese templates for stage-22B manual execution intent replies.

This file belongs to `app/manual_execution/hermes_entry`. It renders fixed
confirmation/result text and builds alert events for the unified alerting
module. It does not send Hermes itself, read/write MySQL, read Redis, call
large language models, request Binance, or perform automatic trading.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSeverity, AlertType
from app.core.time_utils import ensure_utc_aware, format_datetime_with_timezone, now_utc
from app.manual_execution.decimal_utils import decimal_to_text
from app.manual_execution.hermes_entry.intent_schema import ParsedManualExecutionIntent


ACTION_LABELS: Mapping[str, str] = {
    "open_position": "开仓",
    "add_position": "加仓",
    "reduce_position": "减仓",
    "close_position": "平仓",
    "take_profit": "止盈平仓",
    "stop_loss": "止损平仓",
}

SIDE_LABELS: Mapping[str, str] = {"long": "多单", "short": "空单"}


def render_pending_confirmation_text(
    *,
    intent_id: str,
    parsed: ParsedManualExecutionIntent,
    expires_at_utc: datetime,
    dry_run_snapshot: Mapping[str, object],
) -> str:
    """Render the Chinese confirmation prompt for one pending MEI intent."""

    expires_text = _format_optional_time(expires_at_utc)
    fee_text = str(dry_run_snapshot.get("fee_usdt") or "")
    lines = [
        "已生成待确认的人工执行草稿。",
        f"确认码：{intent_id}",
        f"动作：{ACTION_LABELS.get(parsed.action or '', parsed.action or '')}",
        f"标的/方向：{parsed.symbol or ''} / {SIDE_LABELS.get(parsed.side or '', parsed.side or '')}",
        f"成交价：{decimal_to_text(parsed.price)}",
        f"advice_id：{parsed.advice_id or ''}",
    ]
    if parsed.manual_position_id:
        lines.append(f"manual_position_id：{parsed.manual_position_id}")
    if parsed.notional_usdt is not None:
        lines.append(f"名义金额：{decimal_to_text(parsed.notional_usdt)} USDT")
    if parsed.margin_usdt is not None:
        lines.append(f"保证金：{decimal_to_text(parsed.margin_usdt)} USDT")
    if fee_text:
        lines.append(f"预估手续费：{fee_text} USDT")
    lines.extend(
        [
            f"过期时间：{expires_text}",
            f"如确认，请回复：确认 {intent_id}",
            f"如取消，请回复：取消 {intent_id}",
            "本草稿尚未写入人工执行流水，也不会自动交易。",
        ]
    )
    return "\n".join(lines)


def render_parse_failed_text(*, error_message: str) -> str:
    """Render a Chinese parse-failure reminder."""

    return "\n".join(
        [
            "没有生成待确认草稿。",
            f"原因：{error_message}",
            "请补充动作、方向、成交价、名义金额、保证金和 advice_id 后重新发送。",
            "本提醒不是交易建议，也不会自动交易。",
        ]
    )


def render_validation_failed_text(*, error_message: str) -> str:
    """Render a Chinese validation-failure reminder."""

    return "\n".join(
        [
            "人工执行草稿已被阻断，未写入 22A 人工执行表。",
            f"原因：{error_message}",
            "请核对字段或 manual_position_id / advice_id 后重新发送。",
            "本提醒不是交易建议，也不会自动交易。",
        ]
    )


def render_executed_text(*, intent_id: str, manual_position_id: str | None, execution_id: str | None) -> str:
    """Render a Chinese success result after 22A writes database rows."""

    lines = [
        f"{intent_id} 已确认并完成写入。",
        "数据库已写入人工执行记录。",
    ]
    if manual_position_id:
        lines.append(f"manual_position_id：{manual_position_id}")
    if execution_id:
        lines.append(f"execution_id：{execution_id}")
    lines.append("本次确认没有触发自动交易。")
    return "\n".join(lines)


def render_already_executed_text(*, intent_id: str, manual_position_id: str | None, execution_id: str | None) -> str:
    """Render the idempotent duplicate-confirmation message."""

    lines = [
        f"{intent_id} 此前已经执行成功，本次重复确认未再次写库。",
    ]
    if manual_position_id:
        lines.append(f"manual_position_id：{manual_position_id}")
    if execution_id:
        lines.append(f"execution_id：{execution_id}")
    lines.append("请勿重复录入同一笔人工执行。")
    return "\n".join(lines)


def render_cancelled_text(*, intent_id: str) -> str:
    """Render a Chinese cancellation result."""

    return f"{intent_id} 已取消，未写入人工执行流水，也不会自动交易。"


def render_expired_text(*, intent_id: str) -> str:
    """Render a Chinese expiry reminder."""

    return f"{intent_id} 已过期，未写入人工执行流水。请重新发送人工执行内容生成新的确认码。"


def render_blocked_text(*, intent_id: str | None, error_message: str) -> str:
    """Render a Chinese blocked/failure reminder."""

    prefix = f"{intent_id} " if intent_id else ""
    return "\n".join(
        [
            f"{prefix}无法确认人工执行。",
            f"原因：{error_message}",
            "数据库未写入新的人工执行流水。",
            "本提醒不是交易建议，也不会自动交易。",
        ]
    )


def build_manual_execution_intent_event(*, reply_text: str, trace_id: str, summary: str) -> AlertEvent:
    """Build a fixed-template alert event for one 22B user-visible reply."""

    return AlertEvent(
        alert_type=AlertType.MANUAL_EXECUTION_INTENT,
        severity=AlertSeverity.NOTICE,
        title="人工执行确认提醒",
        summary=summary,
        details={WECHAT_VISIBLE_BODY_DETAIL_KEY: reply_text},
        source="manual_execution_hermes_entry",
        occurred_at_utc=now_utc(),
        trace_id=trace_id,
    )


def _format_optional_time(value: datetime | None) -> str:
    aware_value = ensure_utc_aware(value)
    if aware_value is None:
        return ""
    return format_datetime_with_timezone(aware_value)


__all__ = [
    "build_manual_execution_intent_event",
    "render_already_executed_text",
    "render_blocked_text",
    "render_cancelled_text",
    "render_executed_text",
    "render_expired_text",
    "render_parse_failed_text",
    "render_pending_confirmation_text",
    "render_validation_failed_text",
]
