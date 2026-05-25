"""Rule parser for stage-22B manual execution Hermes/WeChat entry.

This file belongs to `app/manual_execution/hermes_entry`. It deterministically
extracts supported manual execution fields from user text. It does not call
large language models, read/write databases, send Hermes, request Binance,
read exchange accounts, modify Kline tables, or perform automatic trading.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.core.exceptions import ValidationError
from app.manual_execution.constants import (
    ACTION_ADD_POSITION,
    ACTION_CLOSE_POSITION,
    ACTION_OPEN_POSITION,
    ACTION_REDUCE_POSITION,
    ACTION_STOP_LOSS,
    ACTION_TAKE_PROFIT,
    CLOSING_ACTIONS,
    SIDE_LONG,
    SIDE_SHORT,
)
from app.manual_execution.decimal_utils import parse_decimal_value
from app.manual_execution.hermes_entry.constants import (
    INBOUND_COMMAND_CANCEL,
    INBOUND_COMMAND_CONFIRM,
    INBOUND_COMMAND_CREATE,
)
from app.manual_execution.hermes_entry.intent_schema import ParsedManualExecutionIntent

INTENT_ID_PATTERN = re.compile(r"\b(MEI-[A-Za-z0-9]{6,40})\b", re.IGNORECASE)
MANUAL_POSITION_ID_PATTERN = re.compile(r"\b(MP-[A-Za-z0-9-]{6,80})\b", re.IGNORECASE)
ADVICE_ID_PATTERN = re.compile(
    r"(?:advice[_\s-]*id|建议(?:编号|ID|id)?|信号(?:编号|ID|id)?)\s*[:：=]?\s*([A-Za-z0-9_.:-]{2,160})",
    re.IGNORECASE,
)
NUMBER_PATTERN = r"([0-9]+(?:\.[0-9]+)?)"

PRICE_PATTERN = re.compile(
    rf"(?:成交价|价格|价位|price)\s*[:：=]?\s*{NUMBER_PATTERN}",
    re.IGNORECASE,
)
MARGIN_PATTERN = re.compile(
    rf"(?:保证金|新增保证金|本次保证金|margin)\s*[:：=]?\s*{NUMBER_PATTERN}\s*(?:u|U|usdt|USDT)?",
    re.IGNORECASE,
)
NOTIONAL_LABEL_PATTERN = re.compile(
    rf"(?:名义金额|成交金额|实际成交|本次金额|金额|notional)\s*[:：=]?\s*{NUMBER_PATTERN}\s*(?:u|U|usdt|USDT)?",
    re.IGNORECASE,
)
ACTION_AMOUNT_PATTERNS = {
    ACTION_OPEN_POSITION: re.compile(rf"(?:开仓|开多|开空)\s*{NUMBER_PATTERN}\s*(?:u|U|usdt|USDT)", re.IGNORECASE),
    ACTION_ADD_POSITION: re.compile(rf"(?:加仓|补仓)\s*{NUMBER_PATTERN}\s*(?:u|U|usdt|USDT)", re.IGNORECASE),
    ACTION_REDUCE_POSITION: re.compile(
        rf"(?:减仓|部分减仓|部分止盈)\s*{NUMBER_PATTERN}\s*(?:u|U|usdt|USDT)",
        re.IGNORECASE,
    ),
}


def parse_inbound_manual_execution_command(text: str) -> tuple[str, str | None]:
    """Parse confirm/cancel commands; non-command text becomes create-intent."""

    normalized = normalize_manual_execution_text(text)
    intent_match = INTENT_ID_PATTERN.search(normalized)
    intent_id = intent_match.group(1).upper() if intent_match else None
    if intent_id and re.search(r"(确认|执行|confirm)", normalized, re.IGNORECASE):
        return INBOUND_COMMAND_CONFIRM, intent_id
    if intent_id and re.search(r"(取消|作废|cancel)", normalized, re.IGNORECASE):
        return INBOUND_COMMAND_CANCEL, intent_id
    return INBOUND_COMMAND_CREATE, None


def parse_manual_execution_intent_text(text: str) -> ParsedManualExecutionIntent:
    """Parse one user text into a structured 22A manual execution request draft."""

    normalized = normalize_manual_execution_text(text)
    if not normalized:
        return ParsedManualExecutionIntent(
            action=None,
            symbol=None,
            side=None,
            price=None,
            normalized_text=normalized,
            error_code="empty_message",
            error_message="没有收到可解析的人工执行内容。",
        )

    action = _parse_action(normalized)
    symbol = _parse_symbol(normalized)
    side = _parse_side(normalized, action)
    price = _parse_optional_decimal_from_pattern(PRICE_PATTERN, normalized, "price")
    margin = _parse_optional_decimal_from_pattern(MARGIN_PATTERN, normalized, "margin_usdt")
    notional = _parse_notional(normalized, action)
    manual_position_id = _parse_manual_position_id(normalized)
    advice_id = _parse_advice_id(normalized)
    reason = _parse_labeled_text(normalized, ("原因", "reason"))
    note = _parse_labeled_text(normalized, ("备注", "note"))

    missing_fields = _missing_fields_for_action(
        action=action,
        symbol=symbol,
        side=side,
        price=price,
        notional_usdt=notional,
        margin_usdt=margin,
        advice_id=advice_id,
    )
    error_code = None
    error_message = None
    if action is None:
        error_code = "action_not_recognized"
        error_message = "没有识别到支持的人工执行动作，请使用开仓、加仓、减仓、平仓、止盈或止损。"
    elif missing_fields:
        error_code = "missing_required_fields"
        error_message = "缺少必要字段：" + "、".join(missing_fields)
    elif action in {ACTION_REDUCE_POSITION, ACTION_CLOSE_POSITION, ACTION_TAKE_PROFIT, ACTION_STOP_LOSS} and margin is not None:
        error_code = "margin_not_allowed_for_close_or_reduce"
        error_message = "减仓、平仓、止盈、止损不需要保证金字段，请删除保证金后重新发送。"

    return ParsedManualExecutionIntent(
        action=action,
        symbol=symbol,
        side=side,
        price=price,
        notional_usdt=None if action in CLOSING_ACTIONS else notional,
        margin_usdt=None if action not in {ACTION_OPEN_POSITION, ACTION_ADD_POSITION} else margin,
        manual_position_id=manual_position_id,
        advice_id=advice_id,
        reason=reason,
        note=note,
        normalized_text=normalized,
        missing_fields=missing_fields,
        error_code=error_code,
        error_message=error_message,
    )


def normalize_manual_execution_text(text: str) -> str:
    """Normalize user text without changing numeric values or time semantics."""

    return " ".join(str(text or "").strip().split())


def _parse_action(text: str) -> str | None:
    if re.search(r"(部分止盈|部分减仓|减仓)", text):
        return ACTION_REDUCE_POSITION
    if re.search(r"(止盈|止盈全平)", text):
        return ACTION_TAKE_PROFIT
    if re.search(r"(止损|止损全平)", text):
        return ACTION_STOP_LOSS
    if re.search(r"(平仓|全平|全部平仓)", text):
        return ACTION_CLOSE_POSITION
    if re.search(r"(加仓|补仓)", text):
        return ACTION_ADD_POSITION
    if re.search(r"(开仓|开多|开空|做多|做空)", text):
        return ACTION_OPEN_POSITION
    return None


def _parse_symbol(text: str) -> str | None:
    if re.search(r"\bBTCUSDT\b", text, re.IGNORECASE) or re.search(r"\bBTC\b|比特币", text, re.IGNORECASE):
        return "BTCUSDT"
    symbol_match = re.search(r"\b([A-Z]{2,20}USDT)\b", text.upper())
    return symbol_match.group(1) if symbol_match else None


def _parse_side(text: str, action: str | None) -> str | None:
    if re.search(r"(多单|做多|开多|\blong\b)", text, re.IGNORECASE):
        return SIDE_LONG
    if re.search(r"(空单|做空|开空|\bshort\b)", text, re.IGNORECASE):
        return SIDE_SHORT
    del action
    return None


def _parse_manual_position_id(text: str) -> str | None:
    match = MANUAL_POSITION_ID_PATTERN.search(text)
    return match.group(1).upper() if match else None


def _parse_advice_id(text: str) -> str | None:
    match = ADVICE_ID_PATTERN.search(text)
    return match.group(1).strip() if match else None


def _parse_optional_decimal_from_pattern(pattern: re.Pattern[str], text: str, field_name: str) -> Decimal | None:
    match = pattern.search(text)
    if not match:
        return None
    try:
        return parse_decimal_value(match.group(1), field_name)
    except ValidationError:
        return None


def _parse_notional(text: str, action: str | None) -> Decimal | None:
    labeled = _parse_optional_decimal_from_pattern(NOTIONAL_LABEL_PATTERN, text, "notional_usdt")
    if labeled is not None:
        return labeled
    if action in ACTION_AMOUNT_PATTERNS:
        return _parse_optional_decimal_from_pattern(ACTION_AMOUNT_PATTERNS[action], text, "notional_usdt")
    return None


def _parse_labeled_text(text: str, labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})\s*[:：=]\s*(.+?)(?:\s+(?:备注|note|原因|reason)\s*[:：=]|$)", text)
    return match.group(1).strip()[:1000] if match else ""


def _missing_fields_for_action(
    *,
    action: str | None,
    symbol: str | None,
    side: str | None,
    price: Decimal | None,
    notional_usdt: Decimal | None,
    margin_usdt: Decimal | None,
    advice_id: str | None,
) -> tuple[str, ...]:
    missing: list[str] = []
    if action is None:
        return tuple(missing)
    if not symbol:
        missing.append("symbol")
    if not side:
        missing.append("side")
    if price is None:
        missing.append("price")
    if not advice_id:
        missing.append("advice_id")
    if action in {ACTION_OPEN_POSITION, ACTION_ADD_POSITION, ACTION_REDUCE_POSITION} and notional_usdt is None:
        missing.append("notional_usdt")
    if action in {ACTION_OPEN_POSITION, ACTION_ADD_POSITION} and margin_usdt is None:
        missing.append("margin_usdt")
    return tuple(missing)


__all__ = [
    "normalize_manual_execution_text",
    "parse_inbound_manual_execution_command",
    "parse_manual_execution_intent_text",
]
