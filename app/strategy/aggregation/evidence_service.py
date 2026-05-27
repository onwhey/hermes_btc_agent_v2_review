"""Stage-23F strategy evidence aggregation service.

Call chain:

    scripts/run_strategy_evidence_aggregation.py::main
        ↓
    app/strategy/aggregation/evidence_service.py::run_strategy_evidence_aggregation
        ↓
    app/strategy/aggregation/evidence_repository.py::list_public_strategy_signal_results
        ↓
    app/strategy/aggregation/evidence_aggregator.py::aggregate_strategy_evidence
        ↓
    app/strategy/aggregation/evidence_repository.py::upsert_aggregation_result

This file belongs to `app/strategy/aggregation`. It orchestrates 23F evidence
aggregation for an already persisted stage-16 strategy run.

External services: none. MySQL: reads strategy signal run/result metadata and
writes only `strategy_evidence_aggregation_result` during confirm-write. Redis:
none. Hermes: none. DeepSeek/large models: none. Binance/account/private
trading state: none. This service never reruns strategies, never reads private
strategy payloads, never generates final advice, and never performs trading.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER
from app.strategy.aggregation.evidence_aggregator import StrategyEvidenceAggregator
from app.strategy.aggregation.evidence_config import (
    create_default_strategy_governance_provider,
)
from app.strategy.aggregation.evidence_repository import (
    create_default_strategy_evidence_aggregation_repository,
)
from app.strategy.aggregation.evidence_types import (
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    EvidenceAggregationPersistencePayload,
    EvidenceAggregationRequest,
    EvidenceAggregationRunResult,
    EvidenceAggregationStatus,
    StrategyEvidenceAggregation,
)

ALLOWED_INPUT_RUN_STATUSES = {"success", "partial_success"}
ALLOWED_EVIDENCE_AGGREGATION_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER})


class StrategyEvidenceAggregationService:
    """Coordinate stage-23F strategy evidence aggregation.

    Parameters: repository and aggregator allow test injection.
    Return value: service instance.
    Failure scenarios: invalid request, missing run/results, aggregation/config
    errors, or persistence errors become structured results.
    External effects: only confirm-write persists the 23F table and commits the
    caller-owned session.
    """

    def __init__(
        self,
        *,
        repository: Any | None = None,
        aggregator: StrategyEvidenceAggregator | None = None,
    ) -> None:
        self._repository = repository or create_default_strategy_evidence_aggregation_repository()
        self._aggregator = aggregator or StrategyEvidenceAggregator(
            governance_provider=create_default_strategy_governance_provider()
        )

    def run_strategy_evidence_aggregation(
        self,
        db_session: Any,
        *,
        request: EvidenceAggregationRequest,
    ) -> EvidenceAggregationRunResult:
        """Run 23F for one existing strategy_signal_run_id."""

        trace_id = request.trace_id or uuid.uuid4().hex
        aggregation_id = _build_aggregation_id(request.strategy_signal_run_id, trace_id=trace_id)
        invalid = _validate_request(request, aggregation_id=aggregation_id, trace_id=trace_id)
        if invalid is not None:
            return invalid

        try:
            strategy_run = self._repository.get_strategy_signal_run(
                db_session,
                run_id=request.strategy_signal_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - database read failure is a service failure.
            _rollback_if_possible(db_session)
            return _failed_result(
                request,
                aggregation_id=aggregation_id,
                trace_id=trace_id,
                message="Strategy signal run lookup failed.",
                error_message=str(exc),
            )
        if strategy_run is None:
            return _blocked_result(
                request,
                aggregation_id=aggregation_id,
                trace_id=trace_id,
                message="strategy_signal_run does not exist.",
                error_code="strategy_signal_run_not_found",
            )
        run_status = str(getattr(strategy_run, "status", "") or "")
        if run_status not in ALLOWED_INPUT_RUN_STATUSES:
            return _blocked_result(
                request,
                aggregation_id=aggregation_id,
                trace_id=trace_id,
                message="strategy_signal_run status is not allowed for 23F aggregation.",
                error_code="strategy_signal_run_status_not_allowed",
                error_message=f"status={run_status}",
            )

        try:
            strategy_results = self._repository.list_public_strategy_signal_results(
                db_session,
                run_id=request.strategy_signal_run_id,
            )
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
            return _failed_result(
                request,
                aggregation_id=aggregation_id,
                trace_id=trace_id,
                message="Strategy signal result lookup failed.",
                error_message=str(exc),
            )
        if not strategy_results:
            return _blocked_result(
                request,
                aggregation_id=aggregation_id,
                trace_id=trace_id,
                message="strategy_signal_result is empty.",
                error_code="strategy_signal_result_empty",
            )

        try:
            aggregation = self._aggregator.aggregate_strategy_evidence(
                aggregation_id=aggregation_id,
                strategy_signal_run=strategy_run,
                strategy_signal_results=tuple(strategy_results),
                trace_id=trace_id,
            )
        except Exception as exc:  # noqa: BLE001 - config/aggregation failure is a structured failure.
            _rollback_if_possible(db_session)
            return _failed_result(
                request,
                aggregation_id=aggregation_id,
                trace_id=trace_id,
                message="Strategy evidence aggregation failed.",
                error_message=str(exc),
            )

        if request.dry_run:
            return _success_result_from_aggregation(
                request,
                aggregation=aggregation,
                database_written=False,
                database_action="dry_run",
                message="dry-run completed; database was not written.",
            )

        try:
            row, database_action = self._repository.upsert_aggregation_result(
                db_session,
                payload=EvidenceAggregationPersistencePayload(
                    aggregation=aggregation,
                    trigger_source=request.trigger_source,
                    created_by=request.created_by,
                ),
            )
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
            return _failed_result(
                request,
                aggregation_id=aggregation.aggregation_id,
                trace_id=trace_id,
                message="Strategy evidence aggregation persistence failed.",
                error_message=str(exc),
            )

        persisted_aggregation = _with_persisted_id(aggregation, aggregation_id=str(getattr(row, "aggregation_id", "")))
        return _success_result_from_aggregation(
            request,
            aggregation=persisted_aggregation,
            database_written=True,
            database_action=database_action,
            message="strategy evidence aggregation written.",
        )


def run_strategy_evidence_aggregation(
    *,
    db_session: Any,
    request: EvidenceAggregationRequest,
) -> EvidenceAggregationRunResult:
    """Convenience function using the default 23F service."""

    return StrategyEvidenceAggregationService().run_strategy_evidence_aggregation(
        db_session,
        request=request,
    )


def _validate_request(
    request: EvidenceAggregationRequest,
    *,
    aggregation_id: str,
    trace_id: str,
) -> EvidenceAggregationRunResult | None:
    if not request.strategy_signal_run_id.strip():
        return EvidenceAggregationRunResult(
            status=EvidenceAggregationStatus.BLOCKED,
            exit_code=EXIT_PARAMETER_ERROR,
            aggregation_id=aggregation_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            trace_id=trace_id,
            database_written=False,
            database_action="none",
            message="strategy_signal_run_id is required.",
            error_code="strategy_signal_run_id_required",
        )
    if request.trigger_source not in ALLOWED_EVIDENCE_AGGREGATION_TRIGGER_SOURCES:
        return EvidenceAggregationRunResult(
            status=EvidenceAggregationStatus.BLOCKED,
            exit_code=EXIT_PARAMETER_ERROR,
            aggregation_id=aggregation_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            trace_id=trace_id,
            database_written=False,
            database_action="none",
            message="trigger_source supports only cli or scheduler for 23F aggregation.",
            error_code="trigger_source_not_allowed",
        )
    if request.dry_run == request.confirm_write:
        return EvidenceAggregationRunResult(
            status=EvidenceAggregationStatus.BLOCKED,
            exit_code=EXIT_PARAMETER_ERROR,
            aggregation_id=aggregation_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            trace_id=trace_id,
            database_written=False,
            database_action="none",
            message="Choose exactly one of dry-run or confirm-write.",
            error_code="write_mode_invalid",
        )
    return None


def _success_result_from_aggregation(
    request: EvidenceAggregationRequest,
    *,
    aggregation: StrategyEvidenceAggregation,
    database_written: bool,
    database_action: str,
    message: str,
) -> EvidenceAggregationRunResult:
    exit_code = EXIT_SUCCESS
    if aggregation.status in {EvidenceAggregationStatus.INSUFFICIENT_EVIDENCE, EvidenceAggregationStatus.BLOCKED}:
        exit_code = EXIT_BLOCKED
    return EvidenceAggregationRunResult(
        status=aggregation.status,
        exit_code=exit_code,
        aggregation_id=aggregation.aggregation_id,
        strategy_signal_run_id=request.strategy_signal_run_id,
        trace_id=aggregation.trace_id,
        database_written=database_written,
        database_action=database_action,
        candidate_bias=aggregation.candidate_bias,
        candidate_confidence=aggregation.candidate_confidence,
        decision_readiness=aggregation.decision_readiness,
        message=message,
        details=aggregation.to_jsonable(),
    )


def _blocked_result(
    request: EvidenceAggregationRequest,
    *,
    aggregation_id: str,
    trace_id: str,
    message: str,
    error_code: str,
    error_message: str | None = None,
) -> EvidenceAggregationRunResult:
    return EvidenceAggregationRunResult(
        status=EvidenceAggregationStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        aggregation_id=aggregation_id,
        strategy_signal_run_id=request.strategy_signal_run_id,
        trace_id=trace_id,
        database_written=False,
        database_action="none",
        message=message,
        error_code=error_code,
        error_message=error_message,
    )


def _failed_result(
    request: EvidenceAggregationRequest,
    *,
    aggregation_id: str,
    trace_id: str,
    message: str,
    error_message: str,
) -> EvidenceAggregationRunResult:
    return EvidenceAggregationRunResult(
        status=EvidenceAggregationStatus.FAILED,
        exit_code=EXIT_FAILED,
        aggregation_id=aggregation_id,
        strategy_signal_run_id=request.strategy_signal_run_id,
        trace_id=trace_id,
        database_written=False,
        database_action="none",
        message=message,
        error_message=error_message,
    )


def _with_persisted_id(
    aggregation: StrategyEvidenceAggregation,
    *,
    aggregation_id: str,
) -> StrategyEvidenceAggregation:
    if not aggregation_id or aggregation_id == aggregation.aggregation_id:
        return aggregation
    return StrategyEvidenceAggregation(
        aggregation_id=aggregation_id,
        strategy_signal_run_id=aggregation.strategy_signal_run_id,
        symbol=aggregation.symbol,
        base_interval=aggregation.base_interval,
        higher_interval=aggregation.higher_interval,
        status=aggregation.status,
        candidate_bias=aggregation.candidate_bias,
        candidate_confidence=aggregation.candidate_confidence,
        decision_readiness=aggregation.decision_readiness,
        strategy_evidence_summary=aggregation.strategy_evidence_summary,
        decision_source_chain=aggregation.decision_source_chain,
        role_coverage_matrix=aggregation.role_coverage_matrix,
        evidence_missing=aggregation.evidence_missing,
        strategy_conflict_summary=aggregation.strategy_conflict_summary,
        participation_summary=aggregation.participation_summary,
        observe_only_summary=aggregation.observe_only_summary,
        risk_gate_summary=aggregation.risk_gate_summary,
        model_review_focus=aggregation.model_review_focus,
        not_trading_advice=aggregation.not_trading_advice,
        trace_id=aggregation.trace_id,
    )


def _build_aggregation_id(strategy_signal_run_id: str, *, trace_id: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in strategy_signal_run_id.strip())
    return f"SEA-{cleaned}-{trace_id[:12]}"


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = [
    "ALLOWED_EVIDENCE_AGGREGATION_TRIGGER_SOURCES",
    "StrategyEvidenceAggregationService",
    "run_strategy_evidence_aggregation",
]
