"""Schemas for stage-22B manual execution Hermes/WeChat entry.

This file belongs to `app/manual_execution/hermes_entry`. It defines DTOs for
inbound manual execution messages, parsed confirmation intents, persistence
payloads, and service results. It does not access MySQL, Redis, Hermes,
Binance, model providers, or any automatic trading capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping
from uuid import uuid4

from app.manual_execution.hermes_entry.constants import (
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_SUCCESS,
    INBOUND_COMMAND_CREATE,
    SOURCE_CHANNEL_HERMES,
)


class ManualExecutionIntentServiceStatus(str, Enum):
    """Service outcome for one 22B intent operation."""

    PENDING_CONFIRMATION = "pending_confirmation"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PARSE_FAILED = "parse_failed"
    VALIDATION_FAILED = "validation_failed"
    BLOCKED = "blocked"
    DRY_RUN = "dry_run"
    FAILED = "failed"


@dataclass(frozen=True)
class ParsedManualExecutionIntent:
    """Parsed manual execution fields extracted by the deterministic parser."""

    action: str | None
    symbol: str | None
    side: str | None
    price: Decimal | None
    notional_usdt: Decimal | None = None
    margin_usdt: Decimal | None = None
    manual_position_id: str | None = None
    advice_id: str | None = None
    reason: str = ""
    note: str = ""
    normalized_text: str = ""
    missing_fields: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class InboundManualExecutionMessage:
    """Inbound Hermes/WeChat message request for 22B.

    Parameters: `text` is the user message; source fields identify the inbound
    channel and message without storing secrets.
    Return value: immutable DTO for the 22B service.
    Failure scenarios: invalid text or disabled entry is handled by service.
    External services: none. Data impact: none.
    """

    text: str
    source_channel: str = SOURCE_CHANNEL_HERMES
    source_message_id: str | None = None
    source_user_id: str | None = None
    trigger_source: str = "hermes"
    command: str = INBOUND_COMMAND_CREATE
    intent_id: str | None = None
    dry_run: bool = False
    confirm_write: bool = True
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class IntentActionRequest:
    """Request to confirm or cancel one MEI intent."""

    intent_id: str
    source_channel: str = SOURCE_CHANNEL_HERMES
    source_message_id: str | None = None
    source_user_id: str | None = None
    dry_run: bool = False
    confirm_write: bool = True
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class ManualExecutionIntentPersistencePayload:
    """Persistence payload for creating one 22B confirmation intent row."""

    intent_id: str
    status: str
    source_channel: str
    source_message_id: str | None
    source_user_id: str | None
    raw_text: str
    normalized_text: str
    parsed_action: str | None
    parsed_symbol: str | None
    parsed_side: str | None
    parsed_manual_position_id: str | None
    parsed_advice_id: str | None
    parsed_price: Decimal | None
    parsed_notional_usdt: Decimal | None
    parsed_margin_usdt: Decimal | None
    parsed_reason: str | None
    parsed_note: str | None
    parsed_payload_json: str
    validation_status: str
    validation_error_code: str | None
    validation_error_message: str | None
    missing_fields_json: str
    dry_run_snapshot_json: str
    expires_at_utc: datetime
    trace_id: str
    created_at_utc: datetime
    updated_at_utc: datetime


@dataclass(frozen=True)
class ManualExecutionIntentResult:
    """Result returned by 22B create/confirm/cancel operations."""

    status: ManualExecutionIntentServiceStatus
    exit_code: int
    trace_id: str
    intent_id: str | None = None
    intent_database_written: bool = False
    manual_execution_database_written: bool = False
    manual_position_id: str | None = None
    execution_id: str | None = None
    expires_at_utc: datetime | None = None
    reply_text: str = ""
    alert_status: str | None = None
    idempotent: bool = False
    error_code: str | None = None
    error_message: str | None = None
    parsed_payload: Mapping[str, object] = field(default_factory=dict)
    dry_run_snapshot: Mapping[str, object] = field(default_factory=dict)


def intent_success_exit_code() -> int:
    """Return the successful CLI exit code for 22B operations."""

    return EXIT_SUCCESS


def intent_blocked_exit_code() -> int:
    """Return the blocked CLI exit code for 22B operations."""

    return EXIT_BLOCKED


def intent_failed_exit_code() -> int:
    """Return the failed CLI exit code for 22B operations."""

    return EXIT_FAILED


def format_manual_execution_intent_result_lines(result: ManualExecutionIntentResult) -> list[str]:
    """Format a compact CLI response for one 22B intent operation."""

    lines = [
        f"status={result.status.value}",
        f"intent_database_written={str(result.intent_database_written).lower()}",
        f"manual_execution_database_written={str(result.manual_execution_database_written).lower()}",
        f"trace_id={result.trace_id}",
    ]
    if result.intent_id:
        lines.append(f"intent_id={result.intent_id}")
    if result.manual_position_id:
        lines.append(f"manual_position_id={result.manual_position_id}")
    if result.execution_id:
        lines.append(f"execution_id={result.execution_id}")
    if result.idempotent:
        lines.append("idempotent=true")
    if result.alert_status:
        lines.append(f"hermes_reply_status={result.alert_status}")
    if result.error_code:
        lines.append(f"error_code={result.error_code}")
    if result.error_message:
        lines.append(f"error_message={result.error_message}")
    if result.reply_text:
        lines.append("reply_text:")
        lines.extend(f"  {line}" for line in result.reply_text.splitlines())
    return lines


__all__ = [
    "InboundManualExecutionMessage",
    "IntentActionRequest",
    "ManualExecutionIntentPersistencePayload",
    "ManualExecutionIntentResult",
    "ManualExecutionIntentServiceStatus",
    "ParsedManualExecutionIntent",
    "format_manual_execution_intent_result_lines",
    "intent_blocked_exit_code",
    "intent_failed_exit_code",
    "intent_success_exit_code",
]
