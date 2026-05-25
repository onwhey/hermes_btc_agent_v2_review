"""Constants for stage-22B manual execution Hermes/WeChat entry.

This file belongs to `app/manual_execution/hermes_entry`. It defines fixed
intent statuses and inbound command labels. It does not read/write MySQL, read
Redis, send Hermes, call model providers, request Binance, or perform
automatic trading.
"""

from __future__ import annotations

INTENT_STATUS_PENDING_CONFIRMATION = "pending_confirmation"
INTENT_STATUS_CONFIRMED = "confirmed"
INTENT_STATUS_EXECUTED = "executed"
INTENT_STATUS_CANCELLED = "cancelled"
INTENT_STATUS_EXPIRED = "expired"
INTENT_STATUS_PARSE_FAILED = "parse_failed"
INTENT_STATUS_VALIDATION_FAILED = "validation_failed"
INTENT_STATUS_EXECUTION_FAILED = "execution_failed"
INTENT_STATUS_FAILED = "failed"

TERMINAL_INTENT_STATUSES = frozenset(
    {
        INTENT_STATUS_EXECUTED,
        INTENT_STATUS_CANCELLED,
        INTENT_STATUS_EXPIRED,
        INTENT_STATUS_PARSE_FAILED,
        INTENT_STATUS_VALIDATION_FAILED,
        INTENT_STATUS_EXECUTION_FAILED,
        INTENT_STATUS_FAILED,
    }
)

VALIDATION_STATUS_OK = "ok"
VALIDATION_STATUS_BLOCKED = "blocked"

SOURCE_CHANNEL_CLI = "cli"
SOURCE_CHANNEL_HERMES = "hermes"
SOURCE_CHANNEL_WECHAT = "wechat"
ALLOWED_SOURCE_CHANNELS = frozenset({SOURCE_CHANNEL_CLI, SOURCE_CHANNEL_HERMES, SOURCE_CHANNEL_WECHAT})

INBOUND_COMMAND_CREATE = "create_intent"
INBOUND_COMMAND_CONFIRM = "confirm_intent"
INBOUND_COMMAND_CANCEL = "cancel_intent"

CREATED_BY_HERMES_ENTRY = "hermes_entry"

EXIT_SUCCESS = 0
EXIT_BLOCKED = 2
EXIT_FAILED = 1
EXIT_PARAMETER_ERROR = 64
