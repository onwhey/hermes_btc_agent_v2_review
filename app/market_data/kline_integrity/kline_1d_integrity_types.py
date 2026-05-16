"""Types for stage-14 BTCUSDT 1d daily Kline integrity checks.

This file belongs to `app/market_data/kline_integrity`. It defines request and
result objects for the read-only 1d daily review service. It does not request
Binance, read/write MySQL, read/write Redis, send Hermes, call DeepSeek, repair
Klines, schedule jobs, generate advice, or perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.core.constants import (
    DEFAULT_DAILY_KLINE_1D_INTEGRITY_LIMIT,
    DEFAULT_DAILY_KLINE_1D_INTEGRITY_LOCK_TTL_SECONDS,
    DEFAULT_DAILY_KLINE_1D_INTEGRITY_NOTIFY_SUCCESS,
    DEFAULT_DAILY_KLINE_1D_INTEGRITY_SYMBOL,
)
from app.market_data.kline_constants import (
    KLINE_1D_INTERVAL_VALUE,
    TRIGGER_SOURCE_SCHEDULER,
    TRIGGER_SOURCE_TO_DATA_SOURCE,
)

KLINE_1D_INTEGRITY_EVENT_TYPE = "kline_1d_integrity_check"
CHECK_TYPE_DAILY_KLINE_1D_INTEGRITY = "daily_kline_1d_integrity"
CHECK_MODE_DAILY_1D_INTEGRITY_CHECK = "daily_1d_integrity_check"

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_QUALITY_FAILED = 2
EXIT_ALERT_FAILED = 3
EXIT_TASK_FAILED = 4


class DailyKline1dIntegrityStatus(str, Enum):
    """Status values returned by the read-only 1d daily review service."""

    HEALTHY = "healthy"
    WARNING = "warning"
    FAILED = "failed"
    BLOCKED = "blocked"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class DailyKline1dIntegrityCheckRequest:
    """Input for one read-only BTCUSDT 1d daily review."""

    symbol: str = DEFAULT_DAILY_KLINE_1D_INTEGRITY_SYMBOL
    interval_value: str = KLINE_1D_INTERVAL_VALUE
    lookback_count: int = DEFAULT_DAILY_KLINE_1D_INTEGRITY_LIMIT
    check_trigger: str = TRIGGER_SOURCE_SCHEDULER
    check_mode: str = CHECK_MODE_DAILY_1D_INTEGRITY_CHECK
    notify_success: bool = DEFAULT_DAILY_KLINE_1D_INTEGRITY_NOTIFY_SUCCESS
    lock_ttl_seconds: int = DEFAULT_DAILY_KLINE_1D_INTEGRITY_LOCK_TTL_SECONDS
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def requested_count(self) -> int:
        """Return the requested number of recent formal 1d rows to inspect."""

        return self.lookback_count

    @property
    def data_source(self) -> str:
        """Return the audit data-source value mapped from trigger source."""

        return TRIGGER_SOURCE_TO_DATA_SOURCE[self.check_trigger]

    @property
    def check_trigger_source(self) -> str:
        """Return the trigger field name used by shared quality records."""

        return self.check_trigger


@dataclass(frozen=True)
class DailyKline1dIntegrityCheckResult:
    """Summary returned by the read-only 1d integrity service."""

    status: DailyKline1dIntegrityStatus
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
    latest_open_time_ms: int | None = None
    expected_latest_open_time_ms: int | None = None
    quality_check_id: int | None = None
    event_log_id: int | None = None
    alert_status: str | None = None
    lock_key: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Return whether 1d health was confirmed and alerts did not fail."""

        return self.status == DailyKline1dIntegrityStatus.HEALTHY and self.exit_code == EXIT_SUCCESS


__all__ = [
    "CHECK_MODE_DAILY_1D_INTEGRITY_CHECK",
    "CHECK_TYPE_DAILY_KLINE_1D_INTEGRITY",
    "EXIT_ALERT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_QUALITY_FAILED",
    "EXIT_SUCCESS",
    "EXIT_TASK_FAILED",
    "DailyKline1dIntegrityCheckRequest",
    "DailyKline1dIntegrityCheckResult",
    "DailyKline1dIntegrityStatus",
    "KLINE_1D_INTEGRITY_EVENT_TYPE",
]
