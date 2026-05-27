"""Stage-23F strategy evidence aggregation DTOs and constants.

This file belongs to `app/strategy/aggregation`. It defines only immutable
request/result/persistence value objects for the 23F strategy-domain evidence
aggregation layer.

Called by: `app/strategy/aggregation/evidence_aggregator.py`,
`app/strategy/aggregation/evidence_repository.py`,
`app/strategy/aggregation/evidence_service.py`, tests, and
`scripts/run_strategy_evidence_aggregation.py`.

External services: none. MySQL: none in this file. Redis: none. Hermes: none.
DeepSeek/large models: none. Trading execution: none. This module never reads
private strategy payloads, never generates final advice, and never touches
formal Kline tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI

EVIDENCE_AGGREGATION_VERSION = "strategy_evidence_aggregation_v1"

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4


class EvidenceAggregationStatus(str, Enum):
    """Status values for one stage-23F aggregation attempt."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    BLOCKED = "blocked"
    FAILED = "failed"


class ParticipationMode(str, Enum):
    """Configured participation levels for a strategy result."""

    OBSERVE_ONLY = "observe_only"
    EVIDENCE_ONLY = "evidence_only"
    ADVISORY = "advisory"
    DECISION_PARTICIPANT = "decision_participant"


class CandidateBias(str, Enum):
    """Strategy-domain candidate bias values; never final trading advice."""

    LONG = "long"
    SHORT = "short"
    WAIT = "wait"
    NEUTRAL = "neutral"
    CONFLICT = "conflict"
    BLOCKED = "blocked"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class DecisionReadiness(str, Enum):
    """Readiness for later model/advice review, not trade permission."""

    READY_FOR_MODEL_REVIEW = "ready_for_model_review"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"
    WAIT_FOR_CONFIRMATION = "wait_for_confirmation"
    BLOCKED_BY_RISK = "blocked_by_risk"
    CONFLICT_REQUIRES_REVIEW = "conflict_requires_review"
    NOT_READY = "not_ready"


@dataclass(frozen=True)
class StrategyGovernance:
    """Governance metadata loaded from strategy config for one strategy.

    Parameters: fields mirror the 23F plan's governance controls.
    Return value: immutable metadata used by the aggregator.
    Failure scenarios: invalid config is normalized conservatively by the
    config loader before this object is created.
    External effects: none.
    """

    strategy_name: str
    strategy_role: str
    provides: tuple[str, ...] = ()
    enabled: bool = True
    maturity_stage: str = "experimental"
    participation_mode: str = ParticipationMode.OBSERVE_ONLY.value
    decision_weight: Decimal = Decimal("0")
    can_veto: bool = False
    veto_scope: str = "none"
    notification_required: bool = True


@dataclass(frozen=True)
class EvidenceAggregationRequest:
    """Input for one stage-23F evidence aggregation attempt.

    Parameters: `strategy_signal_run_id` identifies an existing stage-16 run.
    `dry_run` is read-only; `confirm_write` allows writing the 23F table.
    Return value: `EvidenceAggregationRunResult` from the service.
    Failure scenarios: invalid parameters, missing run/results, config lookup
    errors, and persistence failures are converted to structured results.
    External effects: none in this value object.
    """

    strategy_signal_run_id: str
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = True
    confirm_write: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class StrategyEvidenceAggregation:
    """In-memory stage-23F aggregation output.

    It contains strategy-domain evidence summaries and candidate bias only.
    It never contains final advice, trade setup, entry/stop/target prices,
    private strategy payloads, Kline windows, model output, or trading data.
    """

    aggregation_id: str
    strategy_signal_run_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    status: EvidenceAggregationStatus
    candidate_bias: CandidateBias
    candidate_confidence: Decimal
    decision_readiness: DecisionReadiness
    strategy_evidence_summary: Mapping[str, Any]
    decision_source_chain: tuple[Mapping[str, Any], ...]
    role_coverage_matrix: Mapping[str, Any]
    evidence_missing: tuple[Mapping[str, Any], ...]
    strategy_conflict_summary: Mapping[str, Any]
    participation_summary: Mapping[str, Any]
    observe_only_summary: Mapping[str, Any]
    risk_gate_summary: Mapping[str, Any]
    model_review_focus: Mapping[str, Any]
    not_trading_advice: bool
    trace_id: str

    def to_jsonable(self) -> Mapping[str, Any]:
        """Return a compact JSON-ready representation for dry-run output."""

        return {
            "aggregation_id": self.aggregation_id,
            "strategy_signal_run_id": self.strategy_signal_run_id,
            "symbol": self.symbol,
            "base_interval": self.base_interval,
            "higher_interval": self.higher_interval,
            "status": self.status.value,
            "candidate_bias": self.candidate_bias.value,
            "candidate_confidence": str(self.candidate_confidence),
            "decision_readiness": self.decision_readiness.value,
            "strategy_evidence_summary": dict(self.strategy_evidence_summary),
            "decision_source_chain": list(self.decision_source_chain),
            "role_coverage_matrix": dict(self.role_coverage_matrix),
            "evidence_missing": list(self.evidence_missing),
            "strategy_conflict_summary": dict(self.strategy_conflict_summary),
            "participation_summary": dict(self.participation_summary),
            "observe_only_summary": dict(self.observe_only_summary),
            "risk_gate_summary": dict(self.risk_gate_summary),
            "model_review_focus": dict(self.model_review_focus),
            "not_trading_advice": self.not_trading_advice,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class EvidenceAggregationPersistencePayload:
    """Repository payload for one `strategy_evidence_aggregation_result` row."""

    aggregation: StrategyEvidenceAggregation
    trigger_source: str
    created_by: str


@dataclass(frozen=True)
class EvidenceAggregationRunResult:
    """Compact service result returned to CLI and tests."""

    status: EvidenceAggregationStatus
    exit_code: int
    aggregation_id: str
    strategy_signal_run_id: str
    trace_id: str
    database_written: bool
    database_action: str
    candidate_bias: CandidateBias | None = None
    candidate_confidence: Decimal | None = None
    decision_readiness: DecisionReadiness | None = None
    message: str = ""
    error_code: str | None = None
    error_message: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


def format_evidence_aggregation_result_lines(result: EvidenceAggregationRunResult) -> list[str]:
    """Format compact CLI output without full JSON summaries."""

    return [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"aggregation_id={result.aggregation_id}",
        f"strategy_signal_run_id={result.strategy_signal_run_id}",
        f"trace_id={result.trace_id}",
        f"database_written={str(result.database_written).lower()}",
        f"database_action={result.database_action}",
        f"candidate_bias={result.candidate_bias.value if result.candidate_bias else ''}",
        f"candidate_confidence={str(result.candidate_confidence) if result.candidate_confidence is not None else ''}",
        f"decision_readiness={result.decision_readiness.value if result.decision_readiness else ''}",
        f"message={result.message}",
        f"error_code={result.error_code or ''}",
        f"error_message={result.error_message or ''}",
    ]


__all__ = [
    "CandidateBias",
    "DecisionReadiness",
    "EVIDENCE_AGGREGATION_VERSION",
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "EvidenceAggregationPersistencePayload",
    "EvidenceAggregationRequest",
    "EvidenceAggregationRunResult",
    "EvidenceAggregationStatus",
    "ParticipationMode",
    "StrategyEvidenceAggregation",
    "StrategyGovernance",
    "format_evidence_aggregation_result_lines",
]
