"""Repository for stage-18 strategy aggregation persistence.

This file belongs to `app/strategy/aggregation`. It reads existing
`strategy_signal_run` / `strategy_signal_result` rows, delegates read-only
snapshot window restoration to the stage-15 repository, and writes only
`strategy_aggregation_run` plus `analysis_material_pack`.

Called by: `app/strategy/aggregation/service.py`.

External services: none. MySQL: reads strategy signal tables and writes stage-18
tables through the caller-owned session. Redis: none. Hermes: none. DeepSeek /
large models: none. Formal Kline impact: read-only through snapshot restoration;
this repository never writes `market_kline_4h` or `market_kline_1d`.
Trading execution: none.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from app.core.time_utils import now_utc
from app.market_context.snapshot_repository import (
    MarketContextSnapshotRepository,
    create_default_market_context_snapshot_repository,
)
from app.storage.mysql.models.strategy_aggregation import AnalysisMaterialPack, StrategyAggregationRun
from app.storage.mysql.models.strategy_signal import StrategySignalResult, StrategySignalRun
from app.strategy.aggregation.types import (
    AnalysisMaterialPackPersistencePayload,
    StrategyAggregationPersistencePayload,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class StrategyAggregationRepository:
    """Data access helper for stage-18 aggregation.

    Parameters: optional snapshot repository for test injection.
    Return value: repository instance.
    Failure scenarios: database query/insert/update errors propagate to the
    service, which rolls back and returns structured failed results.
    External service access: none.
    Data impact: writes only stage-18 tables and never commits.
    """

    def __init__(self, *, snapshot_repository: MarketContextSnapshotRepository | Any | None = None) -> None:
        self._snapshot_repository = snapshot_repository or create_default_market_context_snapshot_repository()

    def get_strategy_signal_run(self, db_session: Any, *, run_id: str) -> Any | None:
        """Return one stage-16 strategy signal run by business id."""

        _require_sqlalchemy()
        stmt = select(StrategySignalRun).where(StrategySignalRun.run_id == run_id).limit(1)
        return db_session.execute(stmt).scalar_one_or_none()

    def list_strategy_signal_results(self, db_session: Any, *, run_id: str) -> tuple[Any, ...]:
        """Return all independent strategy signal rows for one run."""

        _require_sqlalchemy()
        stmt = (
            select(StrategySignalResult)
            .where(StrategySignalResult.run_id == run_id)
            .order_by(StrategySignalResult.id.asc())
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def restore_snapshot_kline_windows(self, db_session: Any, *, snapshot_id: str) -> Any:
        """Restore the stage-15 snapshot Kline windows in read-only mode."""

        return self._snapshot_repository.restore_snapshot_kline_windows(db_session, snapshot_id=snapshot_id)

    def get_existing_aggregation(
        self,
        db_session: Any,
        *,
        strategy_signal_run_id: str,
        aggregation_version: str,
        material_schema_version: str,
        indicator_version: str,
        candidate_scenario_version: str,
        statuses: tuple[str, ...] | None = None,
    ) -> Any | None:
        """Return an existing versioned stage-18 aggregation row if present.

        `statuses` lets the service look only for final success material packs.
        Blocked or failed audit attempts must remain rerunnable and therefore
        must not be treated as idempotent final rows.
        """

        _require_sqlalchemy()
        stmt = (
            select(StrategyAggregationRun)
            .where(StrategyAggregationRun.strategy_signal_run_id == strategy_signal_run_id)
            .where(StrategyAggregationRun.aggregation_version == aggregation_version)
            .where(StrategyAggregationRun.material_schema_version == material_schema_version)
            .where(StrategyAggregationRun.indicator_version == indicator_version)
            .where(StrategyAggregationRun.candidate_scenario_version == candidate_scenario_version)
        )
        if statuses is not None:
            stmt = stmt.where(StrategyAggregationRun.status.in_(statuses))
        stmt = stmt.order_by(StrategyAggregationRun.id.desc()).limit(1)
        return db_session.execute(stmt).scalar_one_or_none()

    def get_material_pack_by_aggregation_run_id(
        self,
        db_session: Any,
        *,
        aggregation_run_id: str,
    ) -> Any | None:
        """Return the material pack linked to one aggregation run."""

        _require_sqlalchemy()
        stmt = (
            select(AnalysisMaterialPack)
            .where(AnalysisMaterialPack.aggregation_run_id == aggregation_run_id)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def create_aggregation_run(
        self,
        db_session: Any,
        *,
        payload: StrategyAggregationPersistencePayload,
    ) -> StrategyAggregationRun:
        """Insert one `strategy_aggregation_run` row without committing."""

        created_at_utc = now_utc()
        row = StrategyAggregationRun(
            aggregation_run_id=payload.aggregation_run_id,
            strategy_signal_run_id=payload.strategy_signal_run_id,
            snapshot_id=payload.snapshot_id,
            symbol=payload.symbol,
            base_interval=payload.base_interval,
            higher_interval=payload.higher_interval,
            aggregation_version=payload.aggregation_version,
            material_schema_version=payload.material_schema_version,
            indicator_version=payload.indicator_version,
            candidate_scenario_version=payload.candidate_scenario_version,
            status=payload.status.value,
            input_strategy_count=payload.input_strategy_count,
            input_success_count=payload.input_success_count,
            input_failed_count=payload.input_failed_count,
            input_invalid_count=payload.input_invalid_count,
            input_not_implemented_count=payload.input_not_implemented_count,
            effective_strategy_count=payload.effective_strategy_count,
            analysis_hypothesis_direction=payload.analysis_hypothesis_direction,
            analysis_hypothesis_confidence=payload.analysis_hypothesis_confidence,
            analysis_hypothesis_semantics=payload.analysis_hypothesis_semantics,
            direction_projection_source=payload.direction_projection_source,
            stop_trading_source=payload.stop_trading_source,
            risk_gate_projection_source=payload.risk_gate_projection_source,
            is_strategy_signal=payload.is_strategy_signal,
            is_trading_advice=payload.is_trading_advice,
            is_executable=payload.is_executable,
            strategy_logic_implemented=payload.strategy_logic_implemented,
            promotion_allowed=payload.promotion_allowed,
            promotion_requires_future_strategy_and_llm_stage=(
                payload.promotion_requires_future_strategy_and_llm_stage
            ),
            risk_level=payload.risk_level,
            risk_gate_status=payload.risk_gate_status,
            conflict_level=payload.conflict_level,
            direction_consensus=payload.direction_consensus,
            long_strategies_json=_json_text(payload.long_strategies_json),
            short_strategies_json=_json_text(payload.short_strategies_json),
            neutral_strategies_json=_json_text(payload.neutral_strategies_json),
            supporting_strategies_json=_json_text(payload.supporting_strategies_json),
            opposing_strategies_json=_json_text(payload.opposing_strategies_json),
            risk_strategies_json=_json_text(payload.risk_strategies_json),
            not_implemented_strategies_json=_json_text(payload.not_implemented_strategies_json),
            failed_strategies_json=_json_text(payload.failed_strategies_json),
            invalid_strategies_json=_json_text(payload.invalid_strategies_json),
            candidate_scenarios_json=_json_text(payload.candidate_scenarios_json),
            summary_json=_json_text(payload.summary_json),
            evidence_json=_json_text(payload.evidence_json),
            conflict_json=_json_text(payload.conflict_json),
            validation_plan_json=_json_text(payload.validation_plan_json),
            message=payload.message,
            error_code=payload.error_code,
            error_message=payload.error_message,
            trace_id=payload.trace_id,
            trigger_source=payload.trigger_source,
            created_by=payload.created_by,
            hermes_enabled=payload.hermes_enabled,
            hermes_status=payload.hermes_status,
            hermes_message=payload.hermes_message,
            hermes_error=payload.hermes_error,
            hermes_sent_at_utc=payload.hermes_sent_at_utc,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def create_material_pack(
        self,
        db_session: Any,
        *,
        payload: AnalysisMaterialPackPersistencePayload,
    ) -> AnalysisMaterialPack:
        """Insert one `analysis_material_pack` row without committing."""

        created_at_utc = now_utc()
        row = AnalysisMaterialPack(
            material_pack_id=payload.material_pack_id,
            aggregation_run_id=payload.aggregation_run_id,
            strategy_signal_run_id=payload.strategy_signal_run_id,
            snapshot_id=payload.snapshot_id,
            symbol=payload.symbol,
            base_interval=payload.base_interval,
            higher_interval=payload.higher_interval,
            aggregation_version=payload.aggregation_version,
            material_schema_version=payload.material_schema_version,
            indicator_version=payload.indicator_version,
            candidate_scenario_version=payload.candidate_scenario_version,
            material_version_key=_material_version_key(payload),
            status=payload.status.value,
            material_json=_json_text(payload.material_json),
            question_json=_json_text(payload.question_json),
            validation_plan_json=_json_text(payload.validation_plan_json),
            summary_json=_json_text(payload.summary_json),
            data_window_json=_json_text(payload.data_window_json),
            future_leakage_guard_json=_json_text(payload.future_leakage_guard_json),
            trace_id=payload.trace_id,
            created_by=payload.created_by,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def record_hermes_result(
        self,
        db_session: Any,
        aggregation_row: Any,
        *,
        hermes_status: str,
        hermes_message: str | None,
        hermes_error: str | None,
        hermes_sent_at_utc: Any | None,
    ) -> Any:
        """Store Hermes dispatch outcome without changing aggregation status."""

        aggregation_row.hermes_status = hermes_status
        aggregation_row.hermes_message = hermes_message
        aggregation_row.hermes_error = hermes_error
        aggregation_row.hermes_sent_at_utc = hermes_sent_at_utc
        aggregation_row.updated_at_utc = now_utc()
        _flush_if_possible(db_session)
        return aggregation_row


def create_default_strategy_aggregation_repository() -> StrategyAggregationRepository:
    """Create the default stage-18 aggregation repository."""

    return StrategyAggregationRepository()


def _json_text(value: Mapping[str, Any] | list[Any] | tuple[Any, ...]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _material_version_key(payload: AnalysisMaterialPackPersistencePayload) -> str:
    """Return a short deterministic uniqueness key for one final material pack."""

    raw_key = "|".join(
        (
            payload.strategy_signal_run_id,
            payload.aggregation_version,
            payload.material_schema_version,
            payload.indicator_version,
            payload.candidate_scenario_version,
        )
    )
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for strategy aggregation repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = [
    "StrategyAggregationRepository",
    "create_default_strategy_aggregation_repository",
]
