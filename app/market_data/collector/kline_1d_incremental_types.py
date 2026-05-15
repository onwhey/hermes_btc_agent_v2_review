"""Types for stage-14 BTCUSDT 1d incremental Kline collection.

This file belongs to `app/market_data/collector`. It defines request/result
objects, request-range helpers, event type, defaults, and CLI formatting for the
1d incremental collector service. It does not request Binance, read/write MySQL
or Redis, send Hermes, call DeepSeek, repair Klines, schedule jobs, or execute
trades. The service caller is
`app/market_data/collector/kline_1d_incremental_collector.py::run_incremental_1d_collection`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.collector.types import (
    EXIT_ALERT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_BLOCKED,
    EXIT_SKIPPED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    KlineCollectStatus,
)
from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_MS,
    KLINE_1D_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
    TRIGGER_SOURCE_TO_DATA_SOURCE,
)

KLINE_1D_INCREMENTAL_EVENT_TYPE = "kline_1d_incremental"
DEFAULT_1D_INCREMENTAL_LOCK_TTL_SECONDS = 300
DEFAULT_1D_INCREMENTAL_MAX_CLOSED_COUNT = 30


@dataclass(frozen=True)
class IncrementalKline1dCollectRequest:
    """Input for one 1d incremental collection run.

    Parameters: `trigger_source` must be explicit (`cli` or `scheduler`).
    Real formal writes require `confirm_write=True`; dry-runs never write
    `market_kline_1d`. `max_closed_count` limits one incremental catch-up window
    so this service cannot silently initialize a large 1d history.
    """

    symbol: str = DEFAULT_KLINE_SYMBOL
    interval_value: str = KLINE_1D_INTERVAL_VALUE
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = False
    confirm_write: bool = False
    notify_success: bool = False
    max_closed_count: int = DEFAULT_1D_INCREMENTAL_MAX_CLOSED_COUNT
    lock_ttl_seconds: int = DEFAULT_1D_INCREMENTAL_LOCK_TTL_SECONDS
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def data_source(self) -> str:
        """Return the formal 1d Kline data source mapped from trigger source."""

        return TRIGGER_SOURCE_TO_DATA_SOURCE[self.trigger_source]


@dataclass(frozen=True)
class IncrementalKline1dRequestRange:
    """One overlapped Binance REST request range for 1d incremental collection."""

    start_open_time_ms: int
    end_open_time_ms: int
    include_current_unclosed_probe: bool = True

    @property
    def requested_closed_count(self) -> int:
        """Return the inclusive count of closed 1d open times to verify."""

        if self.end_open_time_ms < self.start_open_time_ms:
            return 0
        return ((self.end_open_time_ms - self.start_open_time_ms) // KLINE_1D_INTERVAL_MS) + 1

    @property
    def fetch_end_open_time_ms(self) -> int:
        """Return the final open time included in the REST probe window."""

        if not self.include_current_unclosed_probe:
            return self.end_open_time_ms
        return self.end_open_time_ms + KLINE_1D_INTERVAL_MS

    @property
    def limit(self) -> int:
        """Return Binance REST limit for the overlapped request window."""

        if self.fetch_end_open_time_ms < self.start_open_time_ms:
            return 0
        return ((self.fetch_end_open_time_ms - self.start_open_time_ms) // KLINE_1D_INTERVAL_MS) + 1

    @property
    def end_time_ms_for_binance(self) -> int:
        """Return Binance REST endTime including the final probed Kline close."""

        return self.fetch_end_open_time_ms + KLINE_1D_INTERVAL_MS - 1


@dataclass(frozen=True)
class IncrementalKline1dCollectResult:
    """Summary returned by the 1d incremental collector service."""

    status: KlineCollectStatus
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
        """Return whether this collection run completed successfully."""

        return self.status == KlineCollectStatus.SUCCESS and self.exit_code == EXIT_SUCCESS


def format_incremental_1d_collect_result_lines(result: IncrementalKline1dCollectResult) -> list[str]:
    """Format a 1d incremental collector result for the thin CLI entry point."""

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
    "DEFAULT_1D_INCREMENTAL_LOCK_TTL_SECONDS",
    "DEFAULT_1D_INCREMENTAL_MAX_CLOSED_COUNT",
    "EXIT_ALERT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_PERSIST_FAILED",
    "EXIT_QUALITY_BLOCKED",
    "EXIT_SKIPPED",
    "EXIT_SUCCESS",
    "EXIT_TASK_FAILED",
    "IncrementalKline1dCollectRequest",
    "IncrementalKline1dCollectResult",
    "IncrementalKline1dRequestRange",
    "KLINE_1D_INCREMENTAL_EVENT_TYPE",
    "KlineCollectStatus",
    "format_incremental_1d_collect_result_lines",
]
