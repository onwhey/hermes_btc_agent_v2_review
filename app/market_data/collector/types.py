"""Types for phase-09 4h Kline incremental collection.

This file belongs to `app/market_data/collector`.
It defines request/result objects, event type, defaults, and exit codes for the
incremental collector service and the manual debug CLI. It does not request
Binance, read/write MySQL or Redis, send Hermes, call DeepSeek, repair Klines,
or execute trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_TO_DATA_SOURCE,
)

COLLECTOR_EVENT_TYPE = "kline_4h_incremental_collect"
DEFAULT_COLLECT_LIMIT = 6
MAX_COLLECT_LIMIT = 20
DEFAULT_COLLECT_LOCK_TTL_SECONDS = 300

EXIT_SUCCESS = 0
EXIT_SKIPPED = 0
EXIT_PARAMETER_ERROR = 1
EXIT_QUALITY_BLOCKED = 2
EXIT_ALERT_FAILED = 3
EXIT_TASK_FAILED = 4
EXIT_PERSIST_FAILED = 5


class KlineCollectStatus(str, Enum):
    """Incremental collector status values persisted in `collector_event_log`."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class IncrementalKlineCollectRequest:
    """Input for one 4h incremental collection run.

    Parameters: `trigger_source` must be explicit (`cli` or `scheduler`).
    `limit` is the requested count of recent closed 4h Klines to inspect.
    Real formal writes require `confirm_write=True`; dry-runs never write
    `market_kline_4h`.
    """

    symbol: str = DEFAULT_KLINE_SYMBOL
    interval_value: str = KLINE_4H_INTERVAL_VALUE
    trigger_source: str = "cli"
    limit: int = DEFAULT_COLLECT_LIMIT
    max_limit: int = MAX_COLLECT_LIMIT
    dry_run: bool = False
    confirm_write: bool = False
    notify_success: bool = False
    lock_ttl_seconds: int = DEFAULT_COLLECT_LOCK_TTL_SECONDS
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def data_source(self) -> str:
        """Return the formal Kline data source mapped from trigger source."""

        return TRIGGER_SOURCE_TO_DATA_SOURCE[self.trigger_source]

    @property
    def requested_count(self) -> int:
        """Return the number of recent closed Klines requested for inspection."""

        return self.limit


@dataclass(frozen=True)
class IncrementalKlineCollectResult:
    """Summary returned by the incremental collector service."""

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
