"""DTOs for stage-21B strategy advice Hermes notification delivery.

This file belongs to `app/strategy_advice`. It defines request/result objects
for rendering 21A notification payloads, optionally writing `alert_message`,
and explicitly sending Hermes. It does not access databases, Redis, Hermes,
model providers, or trading execution capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI

STRATEGY_ADVICE_NOTIFICATION_SOURCE = "app.strategy_advice.notification_sender"

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4


class StrategyAdviceNotificationStatus(str, Enum):
    """Stable status values for one stage-21B notification attempt."""

    SUCCESS = "success"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class StrategyAdviceNotificationRequest:
    """Input for a stage-21B notification send attempt.

    Parameters: `review_id` identifies the 21A lifecycle review; `trigger_source`
    is CLI-only in 21B; `send_real_alert` must be explicit to call Hermes.
    External effects: none in this value object.
    """

    review_id: str
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = True
    confirm_write: bool = False
    send_real_alert: bool = False
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class RenderedStrategyAdviceNotification:
    """Rendered Chinese notification and its audit linkage."""

    title: str
    message: str
    notification_level: str
    severity: str
    related_type: str
    related_id: str
    payload: Mapping[str, Any]
    lifecycle_action: str
    advice_action: str
    model_status_summary: str


@dataclass(frozen=True)
class StrategyAdviceNotificationResult:
    """Compact result returned by 21B service and CLI."""

    status: StrategyAdviceNotificationStatus
    exit_code: int
    review_id: str
    trace_id: str
    related_type: str | None = None
    related_id: str | None = None
    notification_level: str | None = None
    title: str = ""
    message_preview: str = ""
    alert_message_id: int | None = None
    alert_status: str | None = None
    event_type: str | None = None
    send_real_alert: bool = False
    hermes_status: str = "not_attempted"
    dry_run: bool = True
    is_trading_signal: bool = False
    is_executable: bool = False
    auto_trading_allowed: bool = False
    error_code: str | None = None
    error_message: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


def format_strategy_advice_notification_result_lines(result: StrategyAdviceNotificationResult) -> list[str]:
    """Format compact CLI output without dumping the full notification body."""

    return [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"review_id={result.review_id}",
        f"trace_id={result.trace_id}",
        f"related_type={result.related_type or ''}",
        f"related_id={result.related_id or ''}",
        f"notification_level={result.notification_level or ''}",
        f"title={result.title}",
        f"message_preview={result.message_preview}",
        f"alert_message_id={result.alert_message_id or ''}",
        f"alert_status={result.alert_status or ''}",
        f"event_type={result.event_type or ''}",
        f"send_real_alert={str(result.send_real_alert).lower()}",
        f"hermes_status={result.hermes_status}",
        f"dry_run={str(result.dry_run).lower()}",
        f"is_trading_signal={str(result.is_trading_signal).lower()}",
        f"is_executable={str(result.is_executable).lower()}",
        f"auto_trading_allowed={str(result.auto_trading_allowed).lower()}",
        f"error_code={result.error_code or ''}",
        f"error_message={result.error_message or ''}",
    ]


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "STRATEGY_ADVICE_NOTIFICATION_SOURCE",
    "RenderedStrategyAdviceNotification",
    "StrategyAdviceNotificationRequest",
    "StrategyAdviceNotificationResult",
    "StrategyAdviceNotificationStatus",
    "format_strategy_advice_notification_result_lines",
]
