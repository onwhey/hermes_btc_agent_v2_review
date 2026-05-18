"""Types for stage-17 strategy signal scheduler orchestration.

This file belongs to `app/scheduler`. It defines request/result objects and
status constants for scheduler-side orchestration after Kline collector success.
It does not call scripts, request Binance, read/write MySQL or Redis, send
Hermes, call DeepSeek or any large language model, generate final trading
advice, modify formal Kline tables, or perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_SCHEDULER,
)

STRATEGY_SIGNAL_TRIGGER_REASON_4H_SUCCESS = "after_4h_incremental_success"
STRATEGY_SIGNAL_TRIGGER_REASON_1D_SUCCESS = "after_1d_incremental_success"


class StrategySignalSchedulerStatus(str, Enum):
    """Scheduler event status values stored in stage-17 event log."""

    WAITING_UPSTREAM = "waiting_upstream"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


class StrategySignalSchedulerHermesStatus(str, Enum):
    """Compact Hermes dispatch status stored on scheduler events."""

    DISABLED = "disabled"
    NOT_REQUIRED = "not_required"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class StrategySignalSchedulerRequest:
    """Input for one post-collector strategy signal scheduler attempt.

    Parameters: `upstream_job_name` identifies the successful collector job;
    `upstream_slot_time_utc` is the scheduler slot that caused that collector
    run; `current_time_utc` is only the wall-clock time of this stage-17
    attempt. Return value: service methods return `StrategySignalSchedulerResult`.
    Failure scenarios: invalid intervals or repository/service failures are
    converted by the orchestration service.
    External effects: none in this value object.
    """

    upstream_job_name: str
    current_time_utc: datetime
    upstream_slot_time_utc: datetime
    symbol: str = DEFAULT_KLINE_SYMBOL
    base_interval_value: str = KLINE_4H_INTERVAL_VALUE
    higher_interval_value: str = KLINE_1D_INTERVAL_VALUE
    trigger_source: str = TRIGGER_SOURCE_SCHEDULER
    upstream_trace_id: str = ""
    upstream_collector_event_id: int | None = None
    upstream_latest_base_open_time_ms: int | None = None
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class StrategySignalSchedulerEventPayload:
    """Prepared values for one scheduler event row insert."""

    event_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    target_base_open_time_ms: int
    target_base_open_time_utc: datetime
    target_base_close_time_ms: int
    target_base_close_time_utc: datetime
    target_higher_open_time_ms: int | None
    target_higher_open_time_utc: datetime | None
    status: str
    trigger_source: str
    trigger_reason: str
    upstream_4h_collector_event_id: int | None = None
    upstream_1d_collector_event_id: int | None = None
    message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    trace_id: str = ""
    hermes_enabled: bool = False
    hermes_status: str | None = None
    started_at_utc: datetime | None = None
    finished_at_utc: datetime | None = None


@dataclass(frozen=True)
class StrategySignalSchedulerResult:
    """Summary returned by the stage-17 scheduler orchestration service."""

    status: StrategySignalSchedulerStatus
    event_id: str | None
    trace_id: str
    message: str
    target_base_open_time_ms: int | None = None
    run_id: str | None = None
    snapshot_id: str | None = None
    strategy_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    invalid_count: int = 0
    not_implemented_count: int = 0
    hermes_status: StrategySignalSchedulerHermesStatus | None = None
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "STRATEGY_SIGNAL_TRIGGER_REASON_1D_SUCCESS",
    "STRATEGY_SIGNAL_TRIGGER_REASON_4H_SUCCESS",
    "StrategySignalSchedulerEventPayload",
    "StrategySignalSchedulerHermesStatus",
    "StrategySignalSchedulerRequest",
    "StrategySignalSchedulerResult",
    "StrategySignalSchedulerStatus",
]
