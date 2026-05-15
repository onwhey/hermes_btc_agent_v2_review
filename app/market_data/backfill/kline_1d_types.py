"""Typed request and result objects for manual BTCUSDT 1d Kline backfill.

This file belongs to `app/market_data/backfill`.
It defines stage-14-2 request ranges, result values, and CLI formatting for the
manual 1d backfill entry point. It does not access Binance, MySQL, Redis,
Hermes, DeepSeek, scheduler jobs, or any trading system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.backfill.types import (
    EXIT_ALERT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_BLOCKED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
)
from app.market_data.kline_constants import (
    DATA_SOURCE_BINANCE_REST_BY_CLI,
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_MS,
    KLINE_1D_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)

BACKFILL_1D_EVENT_TYPE = "manual_backfill_1d"
DEFAULT_1D_BACKFILL_LIMIT_PER_REQUEST = 500
MAX_1D_BACKFILL_KLINE_COUNT = 2_000
DEFAULT_1D_BACKFILL_LOCK_TTL_SECONDS = 1800


class Kline1dBackfillStatus(str, Enum):
    """Manual 1d backfill task status values persisted into collector_event_log."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ManualKline1dBackfillRequest:
    """Input for one user-triggered 1d Kline backfill.

    Parameters: open-time bounds are inclusive UTC millisecond values and must
    align to UTC 00:00:00. The caller must explicitly pass `trigger_source=cli`.
    `confirm_write` must be true unless this is a dry-run.
    """

    symbol: str = DEFAULT_KLINE_SYMBOL
    interval_value: str = KLINE_1D_INTERVAL_VALUE
    start_open_time_ms: int = 0
    end_open_time_ms: int = 0
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = False
    confirm_write: bool = False
    notify_success: bool = False
    limit_per_request: int = DEFAULT_1D_BACKFILL_LIMIT_PER_REQUEST
    max_kline_count: int = MAX_1D_BACKFILL_KLINE_COUNT
    lock_ttl_seconds: int = DEFAULT_1D_BACKFILL_LOCK_TTL_SECONDS
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def data_source(self) -> str:
        """Return the only data source allowed for manual CLI 1d writes."""

        return DATA_SOURCE_BINANCE_REST_BY_CLI

    @property
    def requested_count(self) -> int:
        """Return the inclusive number of 1d open times in this request."""

        if self.end_open_time_ms < self.start_open_time_ms:
            return 0
        return ((self.end_open_time_ms - self.start_open_time_ms) // KLINE_1D_INTERVAL_MS) + 1


@dataclass(frozen=True)
class Backfill1dKlineRequestRange:
    """One Binance REST request range for a bounded 1d backfill."""

    start_open_time_ms: int
    end_open_time_ms: int
    limit: int

    @property
    def end_time_ms_for_binance(self) -> int:
        """Return Binance REST endTime including the target 1d Kline close time."""

        return self.end_open_time_ms + KLINE_1D_INTERVAL_MS - 1


@dataclass(frozen=True)
class ManualKline1dBackfillResult:
    """Summary returned by `run_manual_1d_backfill`.

    The result is intentionally plain and JSON-friendly so the CLI can print it
    without reaching into repositories or service internals.
    """

    status: Kline1dBackfillStatus
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

        return self.status == Kline1dBackfillStatus.SUCCESS and self.exit_code == EXIT_SUCCESS


def format_manual_1d_backfill_result_lines(result: ManualKline1dBackfillResult) -> list[str]:
    """Format a 1d backfill result for the thin CLI entry point."""

    lines = [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"trace_id={result.trace_id}",
        f"message={result.message}",
        (
            "counts="
            f"requested:{result.requested_count},fetched:{result.fetched_count},"
            f"parsed:{result.parsed_count},closed:{result.closed_count},"
            f"filtered_unclosed:{result.filtered_unclosed_count},"
            f"writable:{result.writable_count},inserted:{result.inserted_count},"
            f"skipped_existing:{result.skipped_existing_count},issues:{result.issue_count}"
        ),
    ]
    if result.first_issue_type or result.first_issue_message:
        lines.append(
            f"first_issue={result.first_issue_type or ''}; message={result.first_issue_message or ''}"
        )
    if result.alert_status:
        lines.append(f"alert_status={result.alert_status}")
    return lines


__all__ = [
    "BACKFILL_1D_EVENT_TYPE",
    "Backfill1dKlineRequestRange",
    "DEFAULT_1D_BACKFILL_LIMIT_PER_REQUEST",
    "EXIT_ALERT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_PERSIST_FAILED",
    "EXIT_QUALITY_BLOCKED",
    "EXIT_SUCCESS",
    "EXIT_TASK_FAILED",
    "Kline1dBackfillStatus",
    "ManualKline1dBackfillRequest",
    "ManualKline1dBackfillResult",
    "format_manual_1d_backfill_result_lines",
]
