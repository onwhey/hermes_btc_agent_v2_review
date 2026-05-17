"""DTOs and enums for the stage-16 strategy signal framework.

This file belongs to `app/strategy`. It defines structured inputs, independent
strategy signals, run requests, run results, and snapshot-resolution DTOs.
It does not access external services, read or write MySQL, read or write Redis,
send Hermes, call DeepSeek or any large language model, read account/position
state, generate final trading advice, or perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.core.constants import (
    DEFAULT_MARKET_CONTEXT_1D_LOOKBACK_COUNT,
    DEFAULT_MARKET_CONTEXT_4H_LOOKBACK_COUNT,
)
from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)

STRATEGY_SIGNAL_EVENT_SOURCE = "app.strategy.signal_service"

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4


class StrategyRunStatus(str, Enum):
    """Status for one strategy signal run batch."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    BLOCKED = "blocked"
    FAILED = "failed"


class StrategySignalStatus(str, Enum):
    """Status for one independent strategy output."""

    SUCCESS = "success"
    NO_SIGNAL = "no_signal"
    INVALID = "invalid"
    NOT_IMPLEMENTED = "not_implemented"
    FAILED = "failed"


class DirectionBias(str, Enum):
    """Directional bias emitted by one strategy; this is not a trade instruction."""

    BULLISH_BIAS = "bullish_bias"
    BEARISH_BIAS = "bearish_bias"
    NEUTRAL = "neutral"
    MIXED = "mixed"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class RiskLevel(str, Enum):
    """Risk classification emitted by one strategy."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class StrategyEvaluationInput:
    """Single immutable input object for all strategies.

    Parameters: fields describe the already restored MarketContextSnapshot
    window and its formal Kline rows.
    Return value: immutable DTO.
    Failure scenarios: construction itself does not query data; callers must
    validate snapshot restoration before creating it.
    External service access: none.
    Data impact: none; strategies must not query Klines outside this input.
    """

    snapshot_id: str
    symbol: str
    base_interval_value: str
    higher_interval_value: str
    base_klines: tuple[Any, ...]
    higher_klines: tuple[Any, ...]
    lookback_base_count: int
    lookback_higher_count: int
    latest_base_open_time_ms: int
    latest_higher_open_time_ms: int
    base_start_open_time_ms: int
    base_end_open_time_ms: int
    higher_start_open_time_ms: int
    higher_end_open_time_ms: int
    base_quality_check_id: int | None
    higher_quality_check_id: int | None
    trace_id: str
    evaluated_at_utc: datetime


@dataclass(frozen=True)
class StrategySignal:
    """Independent signal emitted by one strategy.

    This object is not a final trading suggestion and must not contain entry,
    exit, take-profit, stop-loss, position-size, leverage, or execution fields.
    """

    strategy_name: str
    strategy_version: str
    strategy_status: StrategySignalStatus
    direction_bias: DirectionBias = DirectionBias.UNKNOWN
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    signal_strength: float = 0.0
    reason_codes: tuple[str, ...] = ()
    reason_text: str = ""
    metrics: Mapping[str, Any] = field(default_factory=dict)
    debug_info: Mapping[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    error_message: str | None = None


@dataclass(frozen=True)
class StrategyRunnerResult:
    """Structured result returned by `StrategyRunner`."""

    status: StrategyRunStatus
    signals: tuple[StrategySignal, ...]
    message: str
    blocked_reason: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class SnapshotResolveResult:
    """Result of resolving or creating a usable MarketContextSnapshot."""

    status: StrategyRunStatus
    snapshot_id: str | None
    message: str
    blocked_reason: str | None = None
    error_message: str | None = None
    reused_existing_snapshot: bool = False
    created_new_snapshot: bool = False
    trace_id: str = ""


@dataclass(frozen=True)
class StrategySignalRunRequest:
    """Input request for `StrategySignalService`.

    Exactly one of `snapshot_id` or `ensure_latest_snapshot` must be supplied.
    Dry-runs never write strategy signal tables and do not lazily create
    MarketContextSnapshot rows through ensure-latest. Non-dry-run persistence
    requires `confirm_write=True`.
    """

    snapshot_id: str | None = None
    ensure_latest_snapshot: bool = False
    symbol: str = DEFAULT_KLINE_SYMBOL
    base_interval_value: str = KLINE_4H_INTERVAL_VALUE
    higher_interval_value: str = KLINE_1D_INTERVAL_VALUE
    lookback_base_count: int = DEFAULT_MARKET_CONTEXT_4H_LOOKBACK_COUNT
    lookback_higher_count: int = DEFAULT_MARKET_CONTEXT_1D_LOOKBACK_COUNT
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = False
    confirm_write: bool = False
    created_by: str = "cli"
    current_time_ms: int | None = None
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class StrategySignalRunResult:
    """Service result returned to CLI and tests."""

    status: StrategyRunStatus
    exit_code: int
    run_id: str
    trace_id: str
    snapshot_id: str | None = None
    message: str = ""
    blocked_reason: str | None = None
    error_message: str | None = None
    strategy_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    invalid_count: int = 0
    not_implemented_count: int = 0
    run_row_id: int | None = None
    signals: tuple[StrategySignal, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyRunPersistencePayload:
    """Database payload for one strategy signal run row.

    The payload contains only batch metadata and counts. It intentionally does
    not contain restored Kline arrays, final advice, external model output, or
    private trading state.
    """

    run_id: str
    snapshot_id: str | None
    symbol: str
    base_interval_value: str
    higher_interval_value: str
    status: StrategyRunStatus
    trigger_source: str
    strategy_count: int
    success_count: int
    failed_count: int
    invalid_count: int
    not_implemented_count: int
    blocked_reason: str | None
    error_message: str | None
    trace_id: str
    started_at_utc: datetime
    finished_at_utc: datetime


@dataclass(frozen=True)
class StrategySignalPersistencePayload:
    """Database payload for one independent strategy signal result row."""

    run_id: str
    snapshot_id: str
    symbol: str
    base_interval_value: str
    higher_interval_value: str
    signal: StrategySignal
    trace_id: str


class StrategyConfigError(ValueError):
    """Raised when strategy registry configuration is invalid."""


class StrategyInputBuildError(RuntimeError):
    """Raised when a snapshot cannot produce StrategyEvaluationInput."""


def format_strategy_signal_run_result_lines(result: StrategySignalRunResult) -> list[str]:
    """Format a compact CLI summary without payloads, Kline arrays, or advice."""

    lines = [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"run_id={result.run_id}",
        f"snapshot_id={result.snapshot_id or ''}",
        f"strategy_count={result.strategy_count}",
        f"success_count={result.success_count}",
        f"failed_count={result.failed_count}",
        f"invalid_count={result.invalid_count}",
        f"not_implemented_count={result.not_implemented_count}",
        f"trace_id={result.trace_id}",
        f"message={result.message}",
    ]
    if result.blocked_reason:
        lines.append(f"blocked_reason={result.blocked_reason}")
    if result.error_message:
        lines.append(f"error_message={result.error_message}")
    return lines


__all__ = [
    "DirectionBias",
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "RiskLevel",
    "STRATEGY_SIGNAL_EVENT_SOURCE",
    "SnapshotResolveResult",
    "StrategyConfigError",
    "StrategyEvaluationInput",
    "StrategyInputBuildError",
    "StrategyRunPersistencePayload",
    "StrategyRunStatus",
    "StrategyRunnerResult",
    "StrategySignalPersistencePayload",
    "StrategySignal",
    "StrategySignalRunRequest",
    "StrategySignalRunResult",
    "StrategySignalStatus",
    "format_strategy_signal_run_result_lines",
]
