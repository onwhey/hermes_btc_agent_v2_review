"""Chinese receipt rendering for stage-22A manual execution feedback.

This file belongs to `app/manual_execution`. It renders fixed Chinese receipt
and blocked-input reminder text from structured manual execution data.

Called by `app/manual_execution/service.py`. External services: none. MySQL:
none. Redis: none. Hermes: none here; the service passes the rendered text to
the unified alerting module. DeepSeek: none. Trading execution: none. Receipts
are audit messages, not trading advice.
"""

from __future__ import annotations

from typing import Iterable

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSeverity, AlertType
from app.core.time_utils import format_datetime_with_timezone, utc_aware_to_prc_aware
from app.manual_execution.calculations import ManualPositionState, decimal_to_text


def render_manual_execution_close_receipt(
    *,
    position_state: ManualPositionState,
    execution_records: Iterable[object],
) -> str:
    """Render the stage-22A close receipt without calling external services."""

    opened_prc = format_datetime_with_timezone(utc_aware_to_prc_aware(position_state.opened_at_utc))
    opened_utc = format_datetime_with_timezone(position_state.opened_at_utc)
    closed_line = ""
    if position_state.closed_at_utc is not None:
        closed_line = (
            "关闭时间："
            f"{format_datetime_with_timezone(utc_aware_to_prc_aware(position_state.closed_at_utc))} / "
            f"{format_datetime_with_timezone(position_state.closed_at_utc)}\n"
        )
    chain_lines: list[str] = []
    advice_ids: list[str] = []
    for record in execution_records:
        action = getattr(record, "execution_action", "")
        price = decimal_to_text(getattr(record, "price", None))
        notional = decimal_to_text(getattr(record, "notional_usdt", None))
        advice_id = str(getattr(record, "advice_id", ""))
        if advice_id and advice_id not in advice_ids:
            advice_ids.append(advice_id)
        chain_lines.append(f"- {action} price={price} notional_usdt={notional} advice_id={advice_id}")
    chain_text = "\n".join(chain_lines) if chain_lines else "- 无执行流水"
    advice_text = ", ".join(advice_ids) if advice_ids else "无"
    return (
        f"manual_position_id：{position_state.manual_position_id}\n"
        f"symbol / side：{position_state.symbol} / {position_state.side}\n"
        f"开仓时间：{opened_prc} / {opened_utc}\n"
        f"{closed_line}"
        f"开仓价：{decimal_to_text(position_state.initial_entry_price)}\n"
        f"平均成本：{decimal_to_text(position_state.avg_entry_price)}\n"
        f"平仓价：{decimal_to_text(position_state.close_price)}\n"
        f"总开仓金额：{decimal_to_text(position_state.total_open_notional_usdt)} USDT\n"
        f"总退出金额：{decimal_to_text(position_state.total_close_notional_usdt)} USDT\n"
        f"保证金基准：{decimal_to_text(position_state.margin_basis_usdt)} USDT\n"
        f"有效杠杆：{decimal_to_text(position_state.effective_leverage)}x\n"
        f"总手续费：{decimal_to_text(position_state.total_fee_usdt)} USDT\n"
        f"账面已实现盈亏：{decimal_to_text(position_state.gross_realized_pnl_usdt)} USDT\n"
        f"实际净已实现盈亏：{decimal_to_text(position_state.net_realized_pnl_usdt)} USDT\n"
        f"按保证金收益率：{decimal_to_text(position_state.net_pnl_ratio_on_margin)}\n"
        f"关联 advice_id：{advice_text}\n"
        f"操作链概要：\n{chain_text}\n"
        "边界声明：本回执只记录用户主动反馈的人工执行事实，不读取交易所账户，不同步真实仓位，不自动交易。"
    )


def build_manual_execution_receipt_event(
    *,
    position_state: ManualPositionState,
    receipt_text: str,
    trace_id: str,
) -> AlertEvent:
    """Build a fixed-template alert event for a close receipt."""

    return AlertEvent(
        alert_type=AlertType.MANUAL_EXECUTION_RECEIPT,
        severity=AlertSeverity.NOTICE,
        title="人工执行结算回执",
        summary=f"{position_state.manual_position_id} 已关闭，生成结算回执。",
        details={
            "manual_position_id": position_state.manual_position_id,
            "symbol": position_state.symbol,
            "side": position_state.side,
            WECHAT_VISIBLE_BODY_DETAIL_KEY: receipt_text,
        },
        source="app.manual_execution.service",
        trace_id=trace_id,
    )


def build_manual_execution_error_event(
    *,
    manual_position_id: str,
    symbol: str,
    side: str,
    reason: str,
    trace_id: str,
) -> AlertEvent:
    """Build a fixed-template alert event for invalid manual_position_id input."""

    visible_body = (
        "人工执行录入失败。\n"
        f"manual_position_id：{manual_position_id}\n"
        f"symbol / side：{symbol} / {side}\n"
        f"失败原因：{reason}\n"
        "请重新输入正确的 manual_position_id。\n"
        "本提醒不是交易建议，只用于提示人工反馈录入失败。"
    )
    return AlertEvent(
        alert_type=AlertType.MANUAL_EXECUTION_ERROR,
        severity=AlertSeverity.WARNING,
        title="人工执行录入失败提醒",
        summary=reason,
        details={
            "manual_position_id": manual_position_id,
            "symbol": symbol,
            "side": side,
            WECHAT_VISIBLE_BODY_DETAIL_KEY: visible_body,
        },
        source="app.manual_execution.service",
        trace_id=trace_id,
    )

