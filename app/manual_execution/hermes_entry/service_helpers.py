"""Small helpers for stage-22B manual execution intent service.

This file belongs to `app/manual_execution/hermes_entry`. It builds DTOs,
bounded JSON payloads, and small transaction/result helpers used by the 22B
service. It does not read/write MySQL by itself, send Hermes, read Redis,
request Binance, call large language models, or perform automatic trading.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from app.manual_execution.constants import (
    ACTION_ADD_POSITION,
    ACTION_OPEN_POSITION,
    CLOSING_ACTIONS,
    MANUAL_TRIGGER_SOURCE_CLI,
    MANUAL_TRIGGER_SOURCE_HERMES,
)
from app.manual_execution.decimal_utils import decimal_to_text
from app.manual_execution.hermes_entry.constants import CREATED_BY_HERMES_ENTRY, SOURCE_CHANNEL_CLI
from app.manual_execution.hermes_entry.intent_schema import (
    IntentActionRequest,
    ManualExecutionIntentResult,
    ManualExecutionIntentServiceStatus,
    ParsedManualExecutionIntent,
    intent_blocked_exit_code,
)
from app.manual_execution.hermes_entry.templates import render_blocked_text
from app.manual_execution.schema import ManualExecutionRequest


def manual_request_from_parsed(
    *,
    parsed: ParsedManualExecutionIntent,
    trace_id: str,
    dry_run: bool,
    confirm_write: bool,
    trigger_source: str,
) -> ManualExecutionRequest:
    """Build a 22A service request from one parsed 22B intent."""

    return ManualExecutionRequest(
        action=parsed.action or "",
        advice_id=parsed.advice_id or "",
        symbol=parsed.symbol or "",
        side=parsed.side or "",
        price=parsed.price,
        notional_usdt=None if parsed.action in CLOSING_ACTIONS else parsed.notional_usdt,
        margin_usdt=parsed.margin_usdt if parsed.action in {ACTION_OPEN_POSITION, ACTION_ADD_POSITION} else None,
        manual_position_id=parsed.manual_position_id,
        reason=parsed.reason,
        note=parsed.note,
        trigger_source=trigger_source,
        dry_run=dry_run,
        confirm_write=confirm_write,
        created_by=CREATED_BY_HERMES_ENTRY,
        trace_id=trace_id,
    )


def trigger_source_for_channel(source_channel: str) -> str:
    """Return the 22A manual trigger_source for one 22B source channel."""

    return MANUAL_TRIGGER_SOURCE_CLI if source_channel == SOURCE_CHANNEL_CLI else MANUAL_TRIGGER_SOURCE_HERMES


def parsed_payload(parsed: ParsedManualExecutionIntent) -> dict[str, object]:
    """Return a bounded-JSON-friendly parsed payload snapshot."""

    return {
        "action": parsed.action,
        "symbol": parsed.symbol,
        "side": parsed.side,
        "price": decimal_to_text(parsed.price),
        "notional_usdt": decimal_to_text(parsed.notional_usdt),
        "margin_usdt": decimal_to_text(parsed.margin_usdt),
        "manual_position_id": parsed.manual_position_id,
        "advice_id": parsed.advice_id,
        "reason": parsed.reason,
        "note": parsed.note,
        "missing_fields": list(parsed.missing_fields),
    }


def with_validation_error(
    parsed: ParsedManualExecutionIntent,
    *,
    error_code: str,
    error_message: str,
) -> ParsedManualExecutionIntent:
    """Return a copy of parsed intent with validation failure metadata."""

    return ParsedManualExecutionIntent(
        action=parsed.action,
        symbol=parsed.symbol,
        side=parsed.side,
        price=parsed.price,
        notional_usdt=parsed.notional_usdt,
        margin_usdt=parsed.margin_usdt,
        manual_position_id=parsed.manual_position_id,
        advice_id=parsed.advice_id,
        reason=parsed.reason,
        note=parsed.note,
        normalized_text=parsed.normalized_text,
        missing_fields=parsed.missing_fields,
        error_code=error_code,
        error_message=error_message,
    )


def blocked_action_result(
    *,
    request: IntentActionRequest,
    error_code: str,
    error_message: str,
    intent_id: str | None = None,
) -> ManualExecutionIntentResult:
    """Build a standard blocked result for confirm/cancel operations."""

    active_intent_id = intent_id or request.intent_id
    return ManualExecutionIntentResult(
        status=ManualExecutionIntentServiceStatus.BLOCKED,
        exit_code=intent_blocked_exit_code(),
        trace_id=request.trace_id,
        intent_id=active_intent_id,
        reply_text=render_blocked_text(intent_id=active_intent_id, error_message=error_message),
        error_code=error_code,
        error_message=error_message,
    )


def manual_execution_error_message_for_user(*, error_code: str | None, error_message: str | None) -> str:
    """Map 22A service errors to Chinese user-facing 22B reminders."""

    if error_code == "manual_position_not_found":
        return "manual_position_id 不存在，请重新输入正确的 manual_position_id。"
    if error_code == "advice_not_found":
        return "advice_id 不存在，请重新输入正确的 advice_id。"
    return error_message or "22A 人工执行校验未通过。"


def json_dumps_bounded(value: Any, max_length: int) -> str:
    """Serialize bounded JSON for intent audit fields."""

    text = json.dumps(value, ensure_ascii=False, default=_json_default, separators=(",", ":"))
    return bounded_text(text, max_length)


def bounded_text(value: Any, max_length: int) -> str:
    """Return a string capped to the migration-defined field length."""

    text = str(value or "")
    return text[:max_length]


def bounded_optional_text(value: Any, max_length: int) -> str | None:
    """Return a bounded optional string."""

    text = bounded_text(value, max_length)
    return text or None


def none_if_empty(value: str | None) -> str | None:
    """Return None for blank user text fields."""

    text = (value or "").strip()
    return text or None


def commit_if_possible(db_session: Any) -> None:
    """Commit a caller-owned session when it exposes commit()."""

    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def rollback_if_possible(db_session: Any) -> None:
    """Rollback a caller-owned session when it exposes rollback()."""

    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return decimal_to_text(value)
    return str(value)


__all__ = [
    "blocked_action_result",
    "bounded_optional_text",
    "bounded_text",
    "commit_if_possible",
    "json_dumps_bounded",
    "manual_execution_error_message_for_user",
    "manual_request_from_parsed",
    "none_if_empty",
    "parsed_payload",
    "rollback_if_possible",
    "trigger_source_for_channel",
    "with_validation_error",
]
