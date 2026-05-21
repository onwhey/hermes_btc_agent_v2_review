"""Repository for stage-20B model review chain persistence.

This file belongs to `app/model_review_chain`. It reads stage-18
`analysis_material_pack`, reads/writes stage-20B `model_review_chain_run` and
`model_review_chain_step`, and delegates mock step attempt rows to the existing
stage-19 `model_analysis_run` repository path.

Called by `app/model_review_chain/service.py`.
External services: none. MySQL: reads/writes through the caller-owned session
and never commits. Redis: none. Hermes: none. DeepSeek/GPT/Claude calls: none.
Formal Kline impact: none. Trading execution: none.
"""

from __future__ import annotations

from typing import Any

from app.core.time_utils import now_utc
from app.model_analysis.repository import ModelAnalysisRepository, create_default_model_analysis_repository
from app.model_analysis.types import (
    MODEL_REVIEW_PROVIDER_MOCK,
    MODEL_REVIEW_TRIGGER_SOURCE_WORKER,
    ModelAnalysisRunPersistencePayload,
)
from app.model_review_chain.models import AnalysisMaterialPack, ModelReviewChainRun, ModelReviewChainStep
from app.model_review_chain.schema import (
    ModelReviewChainRunPersistencePayload,
    ModelReviewChainStepPersistencePayload,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class ModelReviewChainRepository:
    """Data access helper for stage-20B chain orchestration.

    Parameters: optional stage-19 repository for writing mock attempt rows.
    Return value: repository instance.
    Failure scenarios: database query/insert/update errors propagate to the
    service, which converts them into structured failures.
    External service access: none.
    Data impact: writes only chain/step rows and compact mock
    `model_analysis_run` rows; never commits.
    """

    def __init__(self, *, model_analysis_repository: ModelAnalysisRepository | Any | None = None) -> None:
        self._model_analysis_repository = model_analysis_repository or create_default_model_analysis_repository()

    def get_material_pack_by_id(self, db_session: Any, *, material_pack_id: str) -> Any | None:
        """Return one stage-18 material pack by business id."""

        _require_sqlalchemy()
        stmt = select(AnalysisMaterialPack).where(AnalysisMaterialPack.material_pack_id == material_pack_id).limit(1)
        return db_session.execute(stmt).scalar_one_or_none()

    def get_chain_run_by_chain_id(self, db_session: Any, *, chain_id: str) -> Any | None:
        """Return one chain run by business chain id."""

        _require_sqlalchemy()
        stmt = select(ModelReviewChainRun).where(ModelReviewChainRun.chain_id == chain_id).limit(1)
        return db_session.execute(stmt).scalar_one_or_none()

    def get_latest_chain_run_for_material_pack(self, db_session: Any, *, material_pack_id: str) -> Any | None:
        """Return the latest chain run for one material pack.

        This read helper lets the 20C worker resume an existing automatic chain
        instead of creating duplicate model-review costs for the same material.
        """

        _require_sqlalchemy()
        stmt = (
            select(ModelReviewChainRun)
            .where(ModelReviewChainRun.material_pack_id == material_pack_id)
            .order_by(ModelReviewChainRun.created_at_utc.desc(), ModelReviewChainRun.id.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def list_unfinished_chain_runs(self, db_session: Any, *, limit: int = 20) -> tuple[Any, ...]:
        """Return compact unfinished chains for worker/watchdog resume scans."""

        _require_sqlalchemy()
        statuses = ("pending", "running", "partial_success", "failed")
        stmt = (
            select(ModelReviewChainRun)
            .where(ModelReviewChainRun.status.in_(statuses))
            .order_by(ModelReviewChainRun.updated_at_utc.asc(), ModelReviewChainRun.id.asc())
            .limit(max(1, int(limit)))
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def list_chain_steps(self, db_session: Any, *, chain_id: str) -> tuple[Any, ...]:
        """Return chain steps in execution order."""

        _require_sqlalchemy()
        stmt = (
            select(ModelReviewChainStep)
            .where(ModelReviewChainStep.chain_id == chain_id)
            .order_by(ModelReviewChainStep.step_no.asc())
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def create_model_review_chain_run(
        self,
        db_session: Any,
        *,
        payload: ModelReviewChainRunPersistencePayload,
    ) -> ModelReviewChainRun:
        """Insert one `model_review_chain_run` row without committing."""

        created_at_utc = now_utc()
        row = ModelReviewChainRun(
            chain_id=payload.chain_id,
            material_pack_id=payload.material_pack_id,
            aggregation_run_id=payload.aggregation_run_id,
            strategy_signal_run_id=payload.strategy_signal_run_id,
            snapshot_id=payload.snapshot_id,
            symbol=payload.symbol,
            base_interval=payload.base_interval,
            higher_interval=payload.higher_interval,
            chain_key=payload.chain_key,
            chain_profile_version=payload.chain_profile_version,
            status=payload.status.value,
            trigger_source=payload.trigger_source,
            trace_id=payload.trace_id,
            current_step=payload.current_step,
            total_steps=payload.total_steps,
            success_step_count=payload.success_step_count,
            failed_step_count=payload.failed_step_count,
            timeout_step_count=payload.timeout_step_count,
            skipped_step_count=payload.skipped_step_count,
            blocked_step_count=payload.blocked_step_count,
            max_retry_count=payload.max_retry_count,
            summary_text=payload.summary_text,
            error_code=payload.error_code,
            error_message=payload.error_message,
            is_final_trading_advice=payload.is_final_trading_advice,
            is_trading_signal=payload.is_trading_signal,
            is_executable=payload.is_executable,
            auto_trading_allowed=payload.auto_trading_allowed,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def update_model_review_chain_run(
        self,
        db_session: Any,
        chain_row: Any,
        *,
        payload: ModelReviewChainRunPersistencePayload,
    ) -> Any:
        """Update one existing chain run row without committing."""

        for field_name in _CHAIN_RUN_UPDATE_FIELDS:
            value = getattr(payload, field_name)
            if field_name == "status":
                value = payload.status.value
            setattr(chain_row, field_name, value)
        chain_row.updated_at_utc = now_utc()
        _flush_if_possible(db_session)
        return chain_row

    def create_model_review_chain_step(
        self,
        db_session: Any,
        *,
        payload: ModelReviewChainStepPersistencePayload,
    ) -> ModelReviewChainStep:
        """Insert one `model_review_chain_step` row without committing."""

        created_at_utc = now_utc()
        row = ModelReviewChainStep(
            chain_step_id=payload.chain_step_id,
            chain_id=payload.chain_id,
            step_no=payload.step_no,
            model_key=payload.model_key,
            model_role=payload.model_role,
            parent_step_id=payload.parent_step_id,
            parent_model_analysis_run_id=payload.parent_model_analysis_run_id,
            model_analysis_run_id=payload.model_analysis_run_id,
            status=payload.status.value,
            attempt_no=payload.attempt_no,
            max_retry_count=payload.max_retry_count,
            started_at_utc=payload.started_at_utc,
            finished_at_utc=payload.finished_at_utc,
            error_code=payload.error_code,
            error_message=payload.error_message,
            retry_after_utc=payload.retry_after_utc,
            step_input_hash=payload.step_input_hash,
            step_output_hash=payload.step_output_hash,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def update_model_review_chain_step(
        self,
        db_session: Any,
        step_row: Any,
        *,
        payload: ModelReviewChainStepPersistencePayload,
    ) -> Any:
        """Update one existing chain step row without committing."""

        for field_name in _CHAIN_STEP_UPDATE_FIELDS:
            value = getattr(payload, field_name)
            if field_name == "status":
                value = payload.status.value
            setattr(step_row, field_name, value)
        step_row.updated_at_utc = now_utc()
        _flush_if_possible(db_session)
        return step_row

    def create_mock_model_analysis_run(
        self,
        db_session: Any,
        *,
        payload: ModelAnalysisRunPersistencePayload,
    ) -> Any:
        """Insert one compact mock `model_analysis_run` attempt row.

        The insert goes through the stage-19 repository so 20B remains aligned
        with the existing model-call record table. This method does not call a
        provider and does not create a final `model_analysis_result`.
        """

        return self._model_analysis_repository.create_model_analysis_run(db_session, payload=payload)

    def list_worker_real_model_runs_between(
        self,
        db_session: Any,
        *,
        start_at_utc: Any,
        end_at_utc: Any,
    ) -> tuple[Any, ...]:
        """Return worker-created real-model attempt rows in a UTC window.

        The 20C policy uses these compact stage-19 attempt rows for budget and
        per-4h frequency checks before it allows another provider call.
        """

        _require_sqlalchemy()
        stmt = (
            select(ModelAnalysisRun)
            .where(ModelAnalysisRun.trigger_source == MODEL_REVIEW_TRIGGER_SOURCE_WORKER)
            .where(ModelAnalysisRun.model_provider != MODEL_REVIEW_PROVIDER_MOCK)
            .where(ModelAnalysisRun.created_at_utc >= start_at_utc)
            .where(ModelAnalysisRun.created_at_utc < end_at_utc)
            .order_by(ModelAnalysisRun.created_at_utc.desc(), ModelAnalysisRun.id.desc())
        )
        return tuple(db_session.execute(stmt).scalars().all())


def create_default_model_review_chain_repository() -> ModelReviewChainRepository:
    """Create the default stage-20B repository."""

    return ModelReviewChainRepository()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for model review chain repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


_CHAIN_RUN_UPDATE_FIELDS = (
    "status",
    "current_step",
    "success_step_count",
    "failed_step_count",
    "timeout_step_count",
    "skipped_step_count",
    "blocked_step_count",
    "summary_text",
    "error_code",
    "error_message",
    "is_final_trading_advice",
    "is_trading_signal",
    "is_executable",
    "auto_trading_allowed",
)

_CHAIN_STEP_UPDATE_FIELDS = (
    "parent_model_analysis_run_id",
    "model_analysis_run_id",
    "status",
    "attempt_no",
    "started_at_utc",
    "finished_at_utc",
    "error_code",
    "error_message",
    "retry_after_utc",
    "step_input_hash",
    "step_output_hash",
)

__all__ = ["ModelReviewChainRepository", "create_default_model_review_chain_repository"]
