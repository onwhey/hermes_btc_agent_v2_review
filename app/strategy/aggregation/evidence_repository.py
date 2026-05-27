"""Repository for stage-23F strategy evidence aggregation persistence.

This file belongs to `app/strategy/aggregation`. It reads existing
`strategy_signal_run` rows and public `strategy_signal_result.common_payload_json`
rows for one run, then inserts or updates only
`strategy_evidence_aggregation_result`.

Called by: `app/strategy/aggregation/evidence_service.py` and the stage-18
repository bridge.

External services: none. MySQL: reads strategy signal metadata/public common
payloads and writes the 23F aggregation table through the caller-owned session.
Redis: none. Hermes: none. DeepSeek/large models: none. Formal Kline impact:
none. Trading execution: none. This repository intentionally does not select or
read `strategy_payload_json`.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from app.core.time_utils import now_utc
from app.storage.mysql.models.strategy_aggregation import StrategyEvidenceAggregationResult
from app.storage.mysql.models.strategy_signal import StrategySignalResult, StrategySignalRun
from app.strategy.aggregation.evidence_types import EvidenceAggregationPersistencePayload

try:
    from sqlalchemy import select
    from sqlalchemy.orm import load_only
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]
    load_only = None  # type: ignore[assignment]


class StrategyEvidenceAggregationRepository:
    """Data access helper for stage-23F aggregation.

    Parameters: none.
    Return value: repository instance.
    Failure scenarios: database query/insert/update errors propagate to the
    service, which rolls back and returns structured failed results.
    External service access: none.
    Data impact: writes only the 23F table and never commits.
    """

    def get_strategy_signal_run(self, db_session: Any, *, run_id: str) -> Any | None:
        """Return one stage-16 strategy signal run by business id."""

        _require_sqlalchemy()
        stmt = select(StrategySignalRun).where(StrategySignalRun.run_id == run_id).limit(1)
        return db_session.execute(stmt).scalar_one_or_none()

    def list_public_strategy_signal_results(self, db_session: Any, *, run_id: str) -> tuple[Any, ...]:
        """Return all stage-16 result rows without selecting private payloads."""

        _require_sqlalchemy()
        stmt = (
            select(StrategySignalResult)
            .options(
                load_only(
                    StrategySignalResult.id,
                    StrategySignalResult.run_id,
                    StrategySignalResult.strategy_name,
                    StrategySignalResult.strategy_version,
                    StrategySignalResult.strategy_status,
                    StrategySignalResult.direction_bias,
                    StrategySignalResult.risk_level,
                    StrategySignalResult.signal_strength,
                    StrategySignalResult.reason_text,
                    StrategySignalResult.strategy_role,
                    StrategySignalResult.common_payload_json,
                    StrategySignalResult.validation_status,
                    StrategySignalResult.validation_errors_json,
                    StrategySignalResult.created_at_utc,
                )
            )
            .where(StrategySignalResult.run_id == run_id)
            .order_by(StrategySignalResult.id.asc())
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def get_existing_aggregation(self, db_session: Any, *, strategy_signal_run_id: str) -> Any | None:
        """Return the current 23F aggregation row for one strategy run."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyEvidenceAggregationResult)
            .where(StrategyEvidenceAggregationResult.strategy_signal_run_id == strategy_signal_run_id)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_latest_aggregation(self, db_session: Any, *, strategy_signal_run_id: str) -> Any | None:
        """Return the latest/current 23F aggregation row for stage-18 bridge use."""

        return self.get_existing_aggregation(db_session, strategy_signal_run_id=strategy_signal_run_id)

    def upsert_aggregation_result(
        self,
        db_session: Any,
        *,
        payload: EvidenceAggregationPersistencePayload,
    ) -> tuple[Any, str]:
        """Insert or update one 23F aggregation row without committing."""

        existing = self.get_existing_aggregation(
            db_session,
            strategy_signal_run_id=payload.aggregation.strategy_signal_run_id,
        )
        if existing is None:
            row = self._create_aggregation_result(db_session, payload=payload)
            return row, "created"
        row = self._update_aggregation_result(db_session, existing=existing, payload=payload)
        return row, "updated"

    def _create_aggregation_result(
        self,
        db_session: Any,
        *,
        payload: EvidenceAggregationPersistencePayload,
    ) -> Any:
        created_at_utc = now_utc()
        aggregation = payload.aggregation
        row = StrategyEvidenceAggregationResult(
            aggregation_id=aggregation.aggregation_id,
            strategy_signal_run_id=aggregation.strategy_signal_run_id,
            symbol=aggregation.symbol,
            base_interval=aggregation.base_interval,
            higher_interval=aggregation.higher_interval,
            status=aggregation.status.value,
            candidate_bias=aggregation.candidate_bias.value,
            candidate_confidence=aggregation.candidate_confidence,
            decision_readiness=aggregation.decision_readiness.value,
            strategy_evidence_summary_json=_json_text(aggregation.strategy_evidence_summary),
            decision_source_chain_json=_json_text(list(aggregation.decision_source_chain)),
            role_coverage_matrix_json=_json_text(aggregation.role_coverage_matrix),
            evidence_missing_json=_json_text(list(aggregation.evidence_missing)),
            strategy_conflict_summary_json=_json_text(aggregation.strategy_conflict_summary),
            participation_summary_json=_json_text(aggregation.participation_summary),
            observe_only_summary_json=_json_text(aggregation.observe_only_summary),
            risk_gate_summary_json=_json_text(aggregation.risk_gate_summary),
            model_review_focus_json=_json_text(aggregation.model_review_focus),
            not_trading_advice=aggregation.not_trading_advice,
            trace_id=aggregation.trace_id,
            trigger_source=payload.trigger_source,
            created_by=payload.created_by,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def _update_aggregation_result(
        self,
        db_session: Any,
        *,
        existing: Any,
        payload: EvidenceAggregationPersistencePayload,
    ) -> Any:
        aggregation = payload.aggregation
        existing.symbol = aggregation.symbol
        existing.base_interval = aggregation.base_interval
        existing.higher_interval = aggregation.higher_interval
        existing.status = aggregation.status.value
        existing.candidate_bias = aggregation.candidate_bias.value
        existing.candidate_confidence = aggregation.candidate_confidence
        existing.decision_readiness = aggregation.decision_readiness.value
        existing.strategy_evidence_summary_json = _json_text(aggregation.strategy_evidence_summary)
        existing.decision_source_chain_json = _json_text(list(aggregation.decision_source_chain))
        existing.role_coverage_matrix_json = _json_text(aggregation.role_coverage_matrix)
        existing.evidence_missing_json = _json_text(list(aggregation.evidence_missing))
        existing.strategy_conflict_summary_json = _json_text(aggregation.strategy_conflict_summary)
        existing.participation_summary_json = _json_text(aggregation.participation_summary)
        existing.observe_only_summary_json = _json_text(aggregation.observe_only_summary)
        existing.risk_gate_summary_json = _json_text(aggregation.risk_gate_summary)
        existing.model_review_focus_json = _json_text(aggregation.model_review_focus)
        existing.not_trading_advice = aggregation.not_trading_advice
        existing.trace_id = aggregation.trace_id
        existing.trigger_source = payload.trigger_source
        existing.created_by = payload.created_by
        existing.updated_at_utc = now_utc()
        _flush_if_possible(db_session)
        return existing


def create_default_strategy_evidence_aggregation_repository() -> StrategyEvidenceAggregationRepository:
    """Create the default stage-23F evidence aggregation repository."""

    return StrategyEvidenceAggregationRepository()


def parse_json_mapping(value: Any) -> Mapping[str, Any]:
    """Parse a repository JSON text field into a mapping for bridge callers."""

    if isinstance(value, Mapping):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def parse_json_list(value: Any) -> list[Any]:
    """Parse a repository JSON text field into a list for bridge callers."""

    if isinstance(value, list):
        return list(value)
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _json_text(value: Mapping[str, Any] | list[Any] | tuple[Any, ...]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _require_sqlalchemy() -> None:
    if select is None or load_only is None:
        raise RuntimeError("SQLAlchemy is required for strategy evidence aggregation repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = [
    "StrategyEvidenceAggregationRepository",
    "create_default_strategy_evidence_aggregation_repository",
    "parse_json_list",
    "parse_json_mapping",
]
