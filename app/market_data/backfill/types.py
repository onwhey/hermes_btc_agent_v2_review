"""Typed request and result objects for manual 4h Kline backfill.

This file belongs to `app/market_data/backfill`.
It defines the phase-08 request, request ranges, result status values, and shell
exit codes used by the manual backfill CLI and service.
It does not access Binance, MySQL, Redis, Hermes, DeepSeek, or trading systems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import (
    DATA_SOURCE_BINANCE_REST_BY_CLI,
    DEFAULT_KLINE_SYMBOL,
    KLINE_4H_INTERVAL_MS,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)

BACKFILL_EVENT_TYPE = "manual_backfill_4h"
DEFAULT_BACKFILL_LIMIT_PER_REQUEST = 500
MAX_BACKFILL_KLINE_COUNT = 500
DEFAULT_BACKFILL_LOCK_TTL_SECONDS = 1800

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_QUALITY_BLOCKED = 2
EXIT_ALERT_FAILED = 3
EXIT_TASK_FAILED = 4
EXIT_PERSIST_FAILED = 5


class KlineBackfillStatus(str, Enum):
    """Manual backfill task status values persisted into collector_event_log."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ManualKlineBackfillRequest:
    """Input for one user-triggered 4h Kline backfill.

    Parameters: open-time bounds are inclusive UTC millisecond values. The caller
    must explicitly pass `trigger_source=cli`; `confirm_write` must be true unless
    this is a dry-run. The service validates all values again before any external
    access or formal Kline write.
    """

    symbol: str = DEFAULT_KLINE_SYMBOL
    interval_value: str = KLINE_4H_INTERVAL_VALUE
    start_open_time_ms: int = 0
    end_open_time_ms: int = 0
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = False
    confirm_write: bool = False
    notify_success: bool = False
    limit_per_request: int = DEFAULT_BACKFILL_LIMIT_PER_REQUEST
    max_kline_count: int = MAX_BACKFILL_KLINE_COUNT
    lock_ttl_seconds: int = DEFAULT_BACKFILL_LOCK_TTL_SECONDS
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def data_source(self) -> str:
        """Return the only data source allowed for phase-08 manual CLI writes."""

        return DATA_SOURCE_BINANCE_REST_BY_CLI

    @property
    def requested_count(self) -> int:
        """Return the inclusive number of 4h open times in this request."""

        if self.end_open_time_ms < self.start_open_time_ms:
            return 0
        return ((self.end_open_time_ms - self.start_open_time_ms) // KLINE_4H_INTERVAL_MS) + 1


@dataclass(frozen=True)
class BackfillKlineRequestRange:
    """One Binance REST request range for a bounded 4h backfill."""

    start_open_time_ms: int
    end_open_time_ms: int
    limit: int

    @property
    def end_time_ms_for_binance(self) -> int:
        """Return Binance REST endTime including the target Kline close time."""

        return self.end_open_time_ms + KLINE_4H_INTERVAL_MS - 1


@dataclass(frozen=True)
class ManualKlineBackfillResult:
    """Summary returned by `run_manual_4h_backfill`.

    The result is intentionally plain and JSON-friendly so the CLI can print it
    without reaching into repositories or service internals.
    """

    status: KlineBackfillStatus
    exit_code: int
    trace_id: str
    message: str
    requested_count: int = 0
    fetched_count: int = 0
    parsed_count: int = 0
    closed_count: int = 0
    filtered_unclosed_count: int = 0
    writable_count: int = 0
    inserted_count: int = 0
    skipped_existing_count: int = 0
    issue_count: int = 0
    first_issue_type: str | None = None
    first_issue_message: str | None = None
    event_log_id: int | None = None
    quality_check_id: int | None = None
    alert_status: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Return whether the task completed successfully."""

        return self.status == KlineBackfillStatus.SUCCESS and self.exit_code == EXIT_SUCCESS

