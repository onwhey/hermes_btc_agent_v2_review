"""Repository for stage-20A model review aggregation persistence.

This file belongs to `app/model_review_aggregation`. It reads only stage-18
`analysis_material_pack`, stage-19 `model_analysis_run`, and stage-19
`model_analysis_result` rows, then writes only the stage-20A
`model_review_aggregation_run` row.

Called by `app/model_review_aggregation/service.py`.
External services: none. MySQL: reads/writes through the caller-owned session
and never commits. Redis: none. Hermes: none. Real model calls: none. Formal
Kline impact: none. Trading execution: none.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.core.time_utils import now_utc
from app.model_review_aggregation.models import (
    AnalysisMaterialPack,
    ModelAnalysisResult as ModelAnalysisResultRow,
    ModelAnalysisRun,
    ModelReviewAggregationRun,
)
from app.model_review_aggregation.schema import (
    ModelReviewAggregationPersistencePayload,
    json_text,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class ModelReviewAggregationRepository:
    """Data access helper for stage-20A aggregation and reuse decisions.

    Parameters: none.
    Return value: repository instance.
    Failure scenarios: database query/insert errors propagate to the service,
    which converts them into structured failures.
    External service access: none.
    Data impact: writes only stage-20A rows and never commits.
    """

    def get_material_pack_by_id(self, db_session: Any, *, material_pack_id: str) -> Any | None:
        """Return one stage-18 material pack by business id."""

        _require_sqlalchemy()
        stmt = select(AnalysisMaterialPack).where(AnalysisMaterialPack.material_pack_id == material_pack_id).limit(1)
        return db_session.execute(stmt).scalar_one_or_none()

    def list_model_analysis_runs_for_material_pack(
        self,
        db_session: Any,
        *,
        material_pack_id: str,
    ) -> tuple[Any, ...]:
        """Return stage-19 attempt rows for status counting only."""

        _require_sqlalchemy()
        stmt = (
            select(ModelAnalysisRun)
            .where(ModelAnalysisRun.material_pack_id == material_pack_id)
            .order_by(ModelAnalysisRun.id.desc())
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def list_success_model_review_candidates(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        limit: int = 20,
    ) -> tuple[Any, ...]:
        """Return latest successful stage-19 final results for reuse checks.

        The service still validates material fingerprint, version metadata,
        boundary flags, and base-bar staleness before accepting any row. This
        query is intentionally read-only and never invokes stage 19.
        """

        _require_sqlalchemy()
        stmt = (
            select(ModelAnalysisRun, ModelAnalysisResultRow, AnalysisMaterialPack)
            .join(
                ModelAnalysisResultRow,
                ModelAnalysisResultRow.model_analysis_run_id == ModelAnalysisRun.model_analysis_run_id,
            )
            .join(
                AnalysisMaterialPack,
                AnalysisMaterialPack.material_pack_id == ModelAnalysisResultRow.material_pack_id,
            )
            .where(ModelAnalysisRun.symbol == symbol)
            .where(ModelAnalysisRun.base_interval == base_interval)
            .where(ModelAnalysisRun.higher_interval == higher_interval)
            .where(ModelAnalysisRun.status == "success")
            .order_by(ModelAnalysisResultRow.created_at_utc.desc(), ModelAnalysisResultRow.id.desc())
            .limit(limit)
        )
        return tuple(
            SimpleNamespace(
                model_analysis_run=run_row,
                model_analysis_result=result_row,
                material_pack=material_row,
            )
            for run_row, result_row, material_row in db_session.execute(stmt).all()
        )

    def create_model_review_aggregation_run(
        self,
        db_session: Any,
        *,
        payload: ModelReviewAggregationPersistencePayload,
    ) -> ModelReviewAggregationRun:
        """Insert one `model_review_aggregation_run` row without committing."""

        created_at_utc = now_utc()
        row = ModelReviewAggregationRun(
            review_aggregation_run_id=payload.review_aggregation_run_id,
            material_pack_id=payload.material_pack_id,
            aggregation_run_id=payload.aggregation_run_id,
            strategy_signal_run_id=payload.strategy_signal_run_id,
            snapshot_id=payload.snapshot_id,
            symbol=payload.symbol,
            base_interval=payload.base_interval,
            higher_interval=payload.higher_interval,
            status=payload.status.value,
            trigger_source=payload.trigger_source,
            created_by=payload.created_by,
            trace_id=payload.trace_id,
            input_model_run_count=payload.input_model_run_count,
            input_model_result_count=payload.input_model_result_count,
            accepted_model_result_count=payload.accepted_model_result_count,
            failed_model_result_count=payload.failed_model_result_count,
            blocked_model_result_count=payload.blocked_model_result_count,
            skipped_model_result_count=payload.skipped_model_result_count,
            aggregation_mode=payload.aggregation_mode,
            model_review_invoked=payload.model_review_invoked,
            model_review_invocation_mode=payload.model_review_invocation_mode,
            model_review_reused=payload.model_review_reused,
            reused_model_analysis_run_id=payload.reused_model_analysis_run_id,
            reused_model_review_created_at_utc=payload.reused_model_review_created_at_utc,
            model_review_skip_reason=payload.model_review_skip_reason,
            model_review_block_reason=payload.model_review_block_reason,
            invoked_model_keys_json=json_text(payload.invoked_model_keys_json),
            invoked_model_roles_json=json_text(payload.invoked_model_roles_json),
            model_review_chain_status=payload.model_review_chain_status,
            model_review_partial_failure_reason=payload.model_review_partial_failure_reason,
            latest_model_review_at_utc=payload.latest_model_review_at_utc,
            model_review_basis=payload.model_review_basis,
            model_review_reuse_status=payload.model_review_reuse_status,
            model_review_reuse_base_bars=payload.model_review_reuse_base_bars,
            model_review_reuse_max_base_bars=payload.model_review_reuse_max_base_bars,
            model_review_expired=payload.model_review_expired,
            review_input_fingerprint=payload.review_input_fingerprint,
            review_input_fingerprint_version=payload.review_input_fingerprint_version,
            review_decision_summary=payload.review_decision_summary,
            evidence_quality_summary=payload.evidence_quality_summary,
            risk_acceptability_summary=payload.risk_acceptability_summary,
            strategy_conflict_summary=payload.strategy_conflict_summary,
            model_consensus_level=payload.model_consensus_level,
            allowed_advice_mode=payload.allowed_advice_mode,
            directional_trade_allowed=payload.directional_trade_allowed,
            model_results_summary_json=json_text(payload.model_results_summary_json),
            model_disagreement_json=json_text(payload.model_disagreement_json),
            risk_warnings_json=json_text(payload.risk_warnings_json),
            missing_evidence_json=json_text(payload.missing_evidence_json),
            human_review_questions_json=json_text(payload.human_review_questions_json),
            summary_text=payload.summary_text,
            is_final_trading_advice=payload.is_final_trading_advice,
            is_trading_signal=payload.is_trading_signal,
            is_executable=payload.is_executable,
            auto_trading_allowed=payload.auto_trading_allowed,
            error_code=payload.error_code,
            error_message=payload.error_message,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row


def create_default_model_review_aggregation_repository() -> ModelReviewAggregationRepository:
    """Create the default stage-20A repository."""

    return ModelReviewAggregationRepository()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for model review aggregation repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = ["ModelReviewAggregationRepository", "create_default_model_review_aggregation_repository"]
