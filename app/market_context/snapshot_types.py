"""Types for stage-15 MarketContextSnapshot generation.

This file belongs to `app/market_context`. It defines request/result DTOs,
status values, quality report objects, and CLI formatting helpers for the
4h + 1d market fact snapshot.
It does not request Binance, write MySQL, write Redis, send Hermes, call
DeepSeek or any large language model, generate strategy advice, read private
trading state, or perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)

MARKET_CONTEXT_EVENT_SOURCE = "app.market_context.snapshot_service"
DEFAULT_MARKET_CONTEXT_4H_LOOKBACK_COUNT = 180
DEFAULT_MARKET_CONTEXT_1D_LOOKBACK_COUNT = 365
MIN_MARKET_CONTEXT_4H_LOOKBACK_COUNT = 1
MIN_MARKET_CONTEXT_1D_LOOKBACK_COUNT = 1

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_ALERT_FAILED = 3
EXIT_FAILED = 4


class MarketContextSnapshotStatus(str, Enum):
    """Snapshot generation status values persisted in `market_context_snapshot`."""

    CREATED = "created"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class MarketContextSnapshotRequest:
    """Input for one MarketContextSnapshot generation attempt.

    Parameters: caller must explicitly provide trigger source, dry-run/write
    mode, and 4h/1d lookback counts. Real snapshot persistence requires
    `confirm_write=True`; dry-runs never write snapshot or Kline-reference rows.
    """

    symbol: str = DEFAULT_KLINE_SYMBOL
    base_interval_value: str = KLINE_4H_INTERVAL_VALUE
    higher_interval_value: str = KLINE_1D_INTERVAL_VALUE
    trigger_source: str = TRIGGER_SOURCE_CLI
    lookback_4h_count: int = DEFAULT_MARKET_CONTEXT_4H_LOOKBACK_COUNT
    lookback_1d_count: int = DEFAULT_MARKET_CONTEXT_1D_LOOKBACK_COUNT
    dry_run: bool = False
    confirm_write: bool = False
    notify_on_blocked: bool = False
    notify_on_failed: bool = False
    created_by: str = "cli"
    current_time_ms: int | None = None
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class SnapshotKlineRef:
    """One formal Kline referenced by a market context snapshot."""

    symbol: str
    interval_value: str
    market_kline_id: int
    open_time_ms: int
    open_time_utc_text: str
    sequence_no: int


@dataclass(frozen=True)
class SnapshotPersistencePayload:
    """Repository input containing snapshot metadata, payload JSON, and refs."""

    snapshot_id: str
    status: MarketContextSnapshotStatus
    symbol: str
    base_interval_value: str
    higher_interval_value: str
    blocked_reason: str | None = None
    error_message: str | None = None
    latest_4h_open_time_ms: int | None = None
    latest_1d_open_time_ms: int | None = None
    lookback_4h_count: int = 0
    lookback_1d_count: int = 0
    actual_4h_count: int = 0
    actual_1d_count: int = 0
    start_4h_open_time_ms: int | None = None
    end_4h_open_time_ms: int | None = None
    start_1d_open_time_ms: int | None = None
    end_1d_open_time_ms: int | None = None
    latest_4h_data_quality_status: str | None = None
    latest_1d_data_quality_status: str | None = None
    latest_4h_collector_event_id: int | None = None
    latest_1d_collector_event_id: int | None = None
    latest_4h_quality_check_id: int | None = None
    latest_1d_quality_check_id: int | None = None
    snapshot_payload_json: str = "{}"
    created_by: str = "cli"
    trigger_source: str = TRIGGER_SOURCE_CLI
    trace_id: str = ""
    refs: tuple[SnapshotKlineRef, ...] = ()


@dataclass(frozen=True)
class MarketContextSnapshotResult:
    """Summary returned by the MarketContextSnapshot service."""

    status: MarketContextSnapshotStatus
    exit_code: int
    trace_id: str
    snapshot_id: str | None = None
    message: str = ""
    blocked_reason: str | None = None
    error_message: str | None = None
    lookback_4h_count: int = 0
    lookback_1d_count: int = 0
    actual_4h_count: int = 0
    actual_1d_count: int = 0
    latest_4h_open_time_utc: str | None = None
    latest_1d_open_time_utc: str | None = None
    snapshot_row_id: int | None = None
    kline_ref_count: int = 0
    alert_status: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def created(self) -> bool:
        """Return whether the snapshot was successfully created."""

        return self.status == MarketContextSnapshotStatus.CREATED and self.exit_code == EXIT_SUCCESS


def format_market_context_snapshot_result_lines(result: MarketContextSnapshotResult) -> list[str]:
    """Format a snapshot result for the thin CLI entry point.

    The formatter intentionally prints only a compact summary. It never prints
    the full payload or full Kline arrays.
    """

    lines = [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"trace_id={result.trace_id}",
        f"snapshot_id={result.snapshot_id or ''}",
        f"message={result.message}",
        (
            "counts="
            f"lookback_4h:{result.lookback_4h_count},actual_4h:{result.actual_4h_count},"
            f"lookback_1d:{result.lookback_1d_count},actual_1d:{result.actual_1d_count},"
            f"kline_refs:{result.kline_ref_count}"
        ),
        f"latest_4h_open_time_utc={result.latest_4h_open_time_utc or ''}",
        f"latest_1d_open_time_utc={result.latest_1d_open_time_utc or ''}",
    ]
    if result.blocked_reason:
        lines.append(f"blocked_reason={result.blocked_reason}")
    if result.error_message:
        lines.append(f"error_message={result.error_message}")
    if result.alert_status:
        lines.append(f"alert_status={result.alert_status}")
    return lines


__all__ = [
    "DEFAULT_MARKET_CONTEXT_1D_LOOKBACK_COUNT",
    "DEFAULT_MARKET_CONTEXT_4H_LOOKBACK_COUNT",
    "EXIT_ALERT_FAILED",
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "MIN_MARKET_CONTEXT_1D_LOOKBACK_COUNT",
    "MIN_MARKET_CONTEXT_4H_LOOKBACK_COUNT",
    "MARKET_CONTEXT_EVENT_SOURCE",
    "MarketContextSnapshotRequest",
    "MarketContextSnapshotResult",
    "MarketContextSnapshotStatus",
    "SnapshotKlineRef",
    "SnapshotPersistencePayload",
    "format_market_context_snapshot_result_lines",
]
