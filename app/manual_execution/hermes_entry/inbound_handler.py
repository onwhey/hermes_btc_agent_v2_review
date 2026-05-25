"""Hermes inbound adapter for stage-22B manual execution intent entry.

This file belongs to `app/manual_execution/hermes_entry`. It normalizes an
already-authenticated Hermes/WeChat inbound payload into the 22B intent service.
The project currently has no HTTP router in this repository, so web frameworks
can call this app-layer function from their own route adapter.

It does not write business tables directly, send Hermes directly, read Redis,
request Binance, call large language models, modify Kline tables, or perform
automatic trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hmac import compare_digest
from typing import Any, Mapping
from uuid import uuid4

from app.core.config import AppSettings, get_settings
from app.core.exceptions import ValidationError
from app.manual_execution.hermes_entry.constants import SOURCE_CHANNEL_HERMES
from app.manual_execution.hermes_entry.intent_schema import InboundManualExecutionMessage, ManualExecutionIntentResult
from app.manual_execution.hermes_entry.intent_service import (
    ManualExecutionIntentService,
    handle_inbound_manual_execution_message,
)


@dataclass(frozen=True)
class HermesManualExecutionInboundPayload:
    """Sanitized inbound message payload for stage-22B.

    Parameters: `text` is the user-visible message; `provided_secret` is an
    adapter-supplied secret token already extracted from transport metadata.
    Return value: immutable DTO.
    Failure scenarios: invalid secret blocks before intent creation.
    External services: none. Data impact: none.
    """

    text: str
    provided_secret: str | None = None
    source_channel: str = SOURCE_CHANNEL_HERMES
    source_message_id: str | None = None
    source_user_id: str | None = None
    raw_payload_summary: Mapping[str, object] = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: uuid4().hex)


def handle_hermes_manual_execution_inbound_payload(
    *,
    db_session: Any,
    payload: HermesManualExecutionInboundPayload,
    settings: AppSettings | None = None,
    service: ManualExecutionIntentService | None = None,
) -> ManualExecutionIntentResult:
    """Validate inbound secret and delegate to the 22B intent service."""

    active_settings = settings or get_settings()
    _validate_inbound_secret(payload.provided_secret, settings=active_settings)
    message = InboundManualExecutionMessage(
        text=payload.text,
        source_channel=payload.source_channel,
        source_message_id=payload.source_message_id,
        source_user_id=payload.source_user_id,
        trigger_source="hermes",
        confirm_write=True,
        dry_run=False,
        trace_id=payload.trace_id,
    )
    return handle_inbound_manual_execution_message(
        db_session=db_session,
        message=message,
        service=service,
    )


def _validate_inbound_secret(provided_secret: str | None, *, settings: AppSettings) -> None:
    expected = settings.hermes_secret
    if not expected:
        raise ValidationError("Hermes inbound secret is not configured")
    if provided_secret is None or not compare_digest(str(provided_secret), expected):
        raise ValidationError("Hermes inbound secret is invalid")


__all__ = [
    "HermesManualExecutionInboundPayload",
    "handle_hermes_manual_execution_inbound_payload",
]
