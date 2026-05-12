"""Typed objects for phase-11 daily 4h Kline integrity review.

This file belongs to `app/market_data/kline_integrity`.
It defines only request/result structures, status values, defaults, and shell
exit codes for the daily review service, scheduler job, and manual debug CLI.
It does not request Binance, read or write MySQL, read or write Redis, send
Hermes, call DeepSeek, generate strategy advice, or perform trading execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.core.constants import (
    DEFAULT_DAILY_KLINE_INTEGRITY_LIMIT,
    DEFAULT_DAILY_KLINE_INTEGRITY_LOCK_TTL_SECONDS,
    DEFAULT_DAILY_KLINE_INTEGRITY_NOTIFY_SUCCESS,
    DEFAULT_DAILY_KLINE_INTEGRITY_SYMBOL,
)
from app.market_data.kline_constants import KLINE_4H_INTERVAL_VALUE
from app.market_data.kline_quality.types import CHECK_TRIGGER_SOURCE_SCHEDULER

CHECK_MODE_DAILY_INTEGRITY_CHECK = "daily_integrity_check"
CHECK_MODE_MANUAL_INTEGRITY_CHECK = "manual_integrity_check"
ALLOWED_CHECK_MODES = frozenset({CHECK_MODE_DAILY_INTEGRITY_CHECK, CHECK_MODE_MANUAL_INTEGRITY_CHECK})

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_QUALITY_FAILED = 2
EXIT_ALERT_FAILED = 3
EXIT_TASK_FAILED = 4


class DailyKlineIntegrityStatus(str, Enum):
    """Daily review status values returned by the phase-11 service."""

    HEALTHY = "healthy"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class DailyKlineIntegrityCheckRequest:
    """Input for one daily official-vs-database 4h Kline review.

    Parameters: `symbol` and `interval_value` identify the reviewed formal Kline
    stream; `lookback_count` is the count of recent closed official Klines to compare;
    `check_trigger` must be explicit (`cli` or `scheduler`); and
    `notify_success` controls only the healthy notification.
    Return value: immutable request object.
    Failure scenarios: the service validates values before any external access.
    External service access and data impact: none in this value object.
    """

    symbol: str = DEFAULT_DAILY_KLINE_INTEGRITY_SYMBOL
    interval_value: str = KLINE_4H_INTERVAL_VALUE
    lookback_count: int = DEFAULT_DAILY_KLINE_INTEGRITY_LIMIT
    check_trigger: str = CHECK_TRIGGER_SOURCE_SCHEDULER
    check_mode: str = CHECK_MODE_DAILY_INTEGRITY_CHECK
    notify_success: bool = DEFAULT_DAILY_KLINE_INTEGRITY_NOTIFY_SUCCESS
    lock_ttl_seconds: int = DEFAULT_DAILY_KLINE_INTEGRITY_LOCK_TTL_SECONDS
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def requested_count(self) -> int:
        """Return the requested count of recent closed 4h Klines."""

        return self.lookback_count

    @property
    def limit(self) -> int:
        """Backward-compatible alias for the recent lookback count."""

        return self.lookback_count

    @property
    def check_trigger_source(self) -> str:
        """Return the quality-check trigger field used by phase-07 shared types."""

        return self.check_trigger


@dataclass(frozen=True)
class DailyKlineIntegrityCheckResult:
    """Summary returned by the daily integrity review service.

    The result is intentionally plain and JSON-friendly so the CLI and scheduler
    can report outcomes without reaching into repositories or alert internals.
    """

    status: DailyKlineIntegrityStatus
    exit_code: int
    trace_id: str
    message: str
    requested_count: int = 0
    checked_count: int = 0
    issue_count: int = 0
    first_issue_type: str | None = None
    first_issue_message: str | None = None
    checked_start_time: str | None = None
    checked_end_time: str | None = None
    quality_check_id: int | None = None
    alert_status: str | None = None
    lock_key: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Return whether Kline health was confirmed and all required alerts succeeded."""

        return self.status == DailyKlineIntegrityStatus.HEALTHY and self.exit_code == EXIT_SUCCESS
