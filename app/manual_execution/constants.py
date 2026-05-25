"""Stage-22A manual execution constants.

This file belongs to `app/manual_execution`. It defines allowed manual
feedback values and fixed safety labels. It does not read/write MySQL, read
Redis, send Hermes, call DeepSeek, request Binance, or perform automatic
trading.
"""

from __future__ import annotations

MANUAL_TRIGGER_SOURCE_CLI = "cli"

SIDE_LONG = "long"
SIDE_SHORT = "short"
ALLOWED_MANUAL_SIDES = frozenset({SIDE_LONG, SIDE_SHORT})

POSITION_STATUS_OPEN = "open"
POSITION_STATUS_CLOSED = "closed"
ALLOWED_MANUAL_POSITION_STATUSES = frozenset({POSITION_STATUS_OPEN, POSITION_STATUS_CLOSED})

ACTION_OPEN_POSITION = "open_position"
ACTION_ADD_POSITION = "add_position"
ACTION_REDUCE_POSITION = "reduce_position"
ACTION_CLOSE_POSITION = "close_position"
ACTION_TAKE_PROFIT = "take_profit"
ACTION_STOP_LOSS = "stop_loss"

OPENING_ACTIONS = frozenset({ACTION_OPEN_POSITION, ACTION_ADD_POSITION})
CLOSING_ACTIONS = frozenset({ACTION_CLOSE_POSITION, ACTION_TAKE_PROFIT, ACTION_STOP_LOSS})
ALLOWED_EXECUTION_ACTIONS = frozenset(
    {
        ACTION_OPEN_POSITION,
        ACTION_ADD_POSITION,
        ACTION_REDUCE_POSITION,
        ACTION_CLOSE_POSITION,
        ACTION_TAKE_PROFIT,
        ACTION_STOP_LOSS,
    }
)

REVIEW_STATUS_NOT_REVIEWED = "not_reviewed"

RESOLUTION_DIRECT = "direct"
RESOLUTION_AUTO_SINGLE_OPEN_POSITION = "auto_single_open_position"
RESOLUTION_NOT_REQUIRED_NEW_POSITION = "not_required_new_position"
RESOLUTION_UNIQUE_BY_ADVICE_ID = "unique_by_advice_id"
RESOLUTION_NOT_UNIQUE = "not_unique"
RESOLUTION_NOT_FOUND = "not_found"

MANUAL_DISCIPLINE_EFFECTIVE_LEVERAGE_LIMIT = "5"

EXIT_SUCCESS = 0
EXIT_BLOCKED = 2
EXIT_FAILED = 1
EXIT_PARAMETER_ERROR = 64

