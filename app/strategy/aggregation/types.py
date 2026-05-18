"""Types for stage-18 strategy aggregation and material packs.

This file belongs to `app/strategy/aggregation`. It defines only enums,
version constants, request/result DTOs, and persistence payloads for strategy
aggregation and deterministic analysis material packs.

Called by: `app/strategy/aggregation/service.py`,
`scripts/run_strategy_aggregation.py`, scheduler integration tests, and the
stage-18 repository.

External services: none. MySQL: none in this file. Redis: none. Hermes: none
in this file. DeepSeek/large models: none. Trading execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)

AGGREGATION_VERSION = "aggregation_v1"
MATERIAL_SCHEMA_VERSION = "material_schema_v1"
INDICATOR_VERSION = "indicator_v1"
CANDIDATE_SCENARIO_VERSION = "candidate_scenario_v1"

STRATEGY_AGGREGATION_EVENT_SOURCE = "app.strategy.aggregation.service"

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4


class StrategyAggregationStatus(str, Enum):
    """Status values for one stage-18 aggregation run."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


class CandidateDirection(str, Enum):
    """Candidate direction produced by aggregation; never final advice."""

    LONG = "long"
    SHORT = "short"
    WAIT = "wait"
    STOP_TRADING = "stop_trading"


class CandidateDirectionConfidence(str, Enum):
    """Coarse deterministic confidence for candidate consensus only."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AggregationRiskLevel(str, Enum):
    """Aggregated risk level derived from strategy signals and material."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"
    UNKNOWN = "unknown"


class RiskGateStatus(str, Enum):
    """Risk-gate outcome for candidate generation."""

    PASS = "pass"
    CAUTION = "caution"
    BLOCKED_BY_VOLATILITY = "blocked_by_volatility"
    BLOCKED_BY_CONFLICT = "blocked_by_conflict"
    INSUFFICIENT_DATA = "insufficient_data"


class ConflictLevel(str, Enum):
    """Conflict severity among independent strategy signals."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StrategyAggregationHermesStatus(str, Enum):
    """Compact Hermes dispatch status stored on aggregation rows."""

    DISABLED = "disabled"
    NOT_REQUIRED = "not_required"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class StrategyAggregationRequest:
    """Input for one stage-18 aggregation attempt.

    Parameters: `strategy_signal_run_id` identifies a pre-existing stage-16
    run; `trigger_source` is `cli` or `scheduler`; dry-run is the safe default.
    Return value: `StrategyAggregationResult` from the service.
    Failure scenarios: invalid parameters, missing stage-16 rows, invalid
    snapshot windows, or persistence failures are converted to structured
    results by the service.
    External effects: none in this value object.
    """

    strategy_signal_run_id: str
    symbol: str = DEFAULT_KLINE_SYMBOL
    base_interval_value: str = KLINE_4H_INTERVAL_VALUE
    higher_interval_value: str = KLINE_1D_INTERVAL_VALUE
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = True
    confirm_write: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class StrategyVoteSummary:
    """Normalized strategy-vote summary used by deterministic aggregation.

    The summary preserves each independent strategy viewpoint. It does not
    rewrite stage-16 results and does not turn candidate directions into
    execution instructions.
    """

    effective_strategy_count: int
    long_strategies: tuple[Mapping[str, Any], ...] = ()
    short_strategies: tuple[Mapping[str, Any], ...] = ()
    neutral_strategies: tuple[Mapping[str, Any], ...] = ()
    risk_strategies: tuple[Mapping[str, Any], ...] = ()
    not_implemented_strategies: tuple[Mapping[str, Any], ...] = ()
    failed_strategies: tuple[Mapping[str, Any], ...] = ()
    invalid_strategies: tuple[Mapping[str, Any], ...] = ()
    long_strength: float = 0.0
    short_strength: float = 0.0
    max_risk_level: AggregationRiskLevel = AggregationRiskLevel.UNKNOWN


@dataclass(frozen=True)
class AggregationDecision:
    """Deterministic candidate decision before material-pack persistence."""

    candidate_direction: CandidateDirection
    candidate_direction_confidence: CandidateDirectionConfidence
    risk_level: AggregationRiskLevel
    risk_gate_status: RiskGateStatus
    conflict_level: ConflictLevel
    direction_consensus: str
    message: str


@dataclass(frozen=True)
class MaterialPackBuildResult:
    """Structured material-pack payload built from snapshot Kline windows."""

    material_json: Mapping[str, Any]
    question_json: Mapping[str, Any]
    validation_plan_json: Mapping[str, Any]
    data_window_json: Mapping[str, Any]
    future_leakage_guard_json: Mapping[str, Any]
    summary_json: Mapping[str, Any]


@dataclass(frozen=True)
class StrategyAggregationResult:
    """Compact service result returned to CLI, scheduler, and tests."""

    status: StrategyAggregationStatus
    exit_code: int
    aggregation_run_id: str
    material_pack_id: str | None
    strategy_signal_run_id: str
    trace_id: str
    snapshot_id: str | None = None
    candidate_direction: CandidateDirection | None = None
    candidate_direction_confidence: CandidateDirectionConfidence | None = None
    risk_level: AggregationRiskLevel | None = None
    risk_gate_status: RiskGateStatus | None = None
    conflict_level: ConflictLevel | None = None
    input_strategy_count: int = 0
    input_success_count: int = 0
    input_failed_count: int = 0
    input_invalid_count: int = 0
    input_not_implemented_count: int = 0
    effective_strategy_count: int = 0
    message: str = ""
    error_code: str | None = None
    error_message: str | None = None
    hermes_status: StrategyAggregationHermesStatus | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyAggregationPersistencePayload:
    """Repository payload for one `strategy_aggregation_run` row.

    It stores aggregation metadata, candidate scenario JSON, evidence JSON,
    conflict JSON, and Hermes status fields. It does not include final advice,
    large-model output, private trading state, or Kline modifications.
    """

    aggregation_run_id: str
    strategy_signal_run_id: str
    snapshot_id: str | None
    symbol: str
    base_interval: str
    higher_interval: str
    aggregation_version: str
    material_schema_version: str
    indicator_version: str
    candidate_scenario_version: str
    status: StrategyAggregationStatus
    input_strategy_count: int
    input_success_count: int
    input_failed_count: int
    input_invalid_count: int
    input_not_implemented_count: int
    effective_strategy_count: int
    candidate_direction: str | None
    candidate_direction_confidence: str | None
    risk_level: str | None
    risk_gate_status: str | None
    conflict_level: str | None
    direction_consensus: str | None
    long_strategies_json: Mapping[str, Any]
    short_strategies_json: Mapping[str, Any]
    neutral_strategies_json: Mapping[str, Any]
    supporting_strategies_json: Mapping[str, Any]
    opposing_strategies_json: Mapping[str, Any]
    risk_strategies_json: Mapping[str, Any]
    not_implemented_strategies_json: Mapping[str, Any]
    failed_strategies_json: Mapping[str, Any]
    invalid_strategies_json: Mapping[str, Any]
    candidate_scenarios_json: Mapping[str, Any]
    summary_json: Mapping[str, Any]
    evidence_json: Mapping[str, Any]
    conflict_json: Mapping[str, Any]
    validation_plan_json: Mapping[str, Any]
    message: str
    error_code: str | None
    error_message: str | None
    trace_id: str
    trigger_source: str
    created_by: str
    hermes_enabled: bool
    hermes_status: str | None
    hermes_message: str | None
    hermes_error: str | None
    hermes_sent_at_utc: datetime | None


@dataclass(frozen=True)
class AnalysisMaterialPackPersistencePayload:
    """Repository payload for one `analysis_material_pack` row."""

    material_pack_id: str
    aggregation_run_id: str
    strategy_signal_run_id: str
    snapshot_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    aggregation_version: str
    material_schema_version: str
    indicator_version: str
    candidate_scenario_version: str
    status: StrategyAggregationStatus
    material_json: Mapping[str, Any]
    question_json: Mapping[str, Any]
    validation_plan_json: Mapping[str, Any]
    summary_json: Mapping[str, Any]
    data_window_json: Mapping[str, Any]
    future_leakage_guard_json: Mapping[str, Any]
    trace_id: str
    created_by: str


def format_strategy_aggregation_result_lines(result: StrategyAggregationResult) -> list[str]:
    """Format compact CLI output without full material JSON or Kline arrays."""

    return [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"aggregation_run_id={result.aggregation_run_id}",
        f"material_pack_id={result.material_pack_id or ''}",
        f"strategy_signal_run_id={result.strategy_signal_run_id}",
        f"snapshot_id={result.snapshot_id or ''}",
        f"candidate_direction={result.candidate_direction.value if result.candidate_direction else ''}",
        f"risk_level={result.risk_level.value if result.risk_level else ''}",
        f"risk_gate_status={result.risk_gate_status.value if result.risk_gate_status else ''}",
        f"conflict_level={result.conflict_level.value if result.conflict_level else ''}",
        f"message={result.message}",
        f"error_message={result.error_message or ''}",
    ]


__all__ = [
    "AGGREGATION_VERSION",
    "CANDIDATE_SCENARIO_VERSION",
    "INDICATOR_VERSION",
    "MATERIAL_SCHEMA_VERSION",
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "AggregationDecision",
    "AggregationRiskLevel",
    "AnalysisMaterialPackPersistencePayload",
    "CandidateDirection",
    "CandidateDirectionConfidence",
    "ConflictLevel",
    "MaterialPackBuildResult",
    "RiskGateStatus",
    "STRATEGY_AGGREGATION_EVENT_SOURCE",
    "StrategyAggregationHermesStatus",
    "StrategyAggregationPersistencePayload",
    "StrategyAggregationRequest",
    "StrategyAggregationResult",
    "StrategyAggregationStatus",
    "StrategyVoteSummary",
    "format_strategy_aggregation_result_lines",
]
