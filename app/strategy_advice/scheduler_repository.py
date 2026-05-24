"""Repository for stage-21C strategy advice scheduler orchestration.

This file belongs to `app/strategy_advice`. It reads stage-20
`model_review_aggregation_run` rows, reads stage-21 lifecycle reviews/events,
and writes only stale-skip audit rows plus the lightweight 21C scheduler log.

Called by `app/strategy_advice/scheduler_service.py`. External services: none.
MySQL: reads/writes through the caller-owned session and never commits. Redis:
none. Hermes: none. Model providers: none. Trading execution: none.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.time_utils import now_utc
from app.strategy_advice.models import (
    ModelReviewAggregationRun,
    StrategyAdviceEvent,
    StrategyAdviceLifecycleReview,
    StrategyAdviceSchedulerEventLog,
)
from app.strategy_advice.schema import (
    StrategyAdviceEventPersistencePayload,
    StrategyAdviceLifecycleReviewPersistencePayload,
    json_text,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class StrategyAdviceSchedulerRepository:
    """Data access helper for stage-21C idempotency and audit state."""

    def get_review_aggregation_by_id(self, db_session: Any, *, review_aggregation_run_id: str) -> Any | None:
        """Return one MRAG row by business id."""

        _require_sqlalchemy()
        stmt = (
            select(ModelReviewAggregationRun)
            .where(ModelReviewAggregationRun.review_aggregation_run_id == review_aggregation_run_id)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_latest_review_aggregation_for_scope(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
    ) -> Any | None:
        """Return the newest MRAG row for one symbol/base/higher tuple."""

        _require_sqlalchemy()
        stmt = (
            select(ModelReviewAggregationRun)
            .where(ModelReviewAggregationRun.symbol == symbol)
            .where(ModelReviewAggregationRun.base_interval == base_interval)
            .where(ModelReviewAggregationRun.higher_interval == higher_interval)
            .order_by(ModelReviewAggregationRun.created_at_utc.desc(), ModelReviewAggregationRun.id.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def list_unprocessed_review_aggregations(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        limit: int,
    ) -> tuple[Any, ...]:
        """Return newest unprocessed MRAG rows, never material packs."""

        _require_sqlalchemy()
        stmt = (
            select(ModelReviewAggregationRun)
            .outerjoin(
                StrategyAdviceLifecycleReview,
                StrategyAdviceLifecycleReview.source_review_aggregation_run_id
                == ModelReviewAggregationRun.review_aggregation_run_id,
            )
            .where(ModelReviewAggregationRun.symbol == symbol)
            .where(ModelReviewAggregationRun.base_interval == base_interval)
            .where(ModelReviewAggregationRun.higher_interval == higher_interval)
            .where(StrategyAdviceLifecycleReview.id.is_(None))
            .order_by(ModelReviewAggregationRun.created_at_utc.desc(), ModelReviewAggregationRun.id.desc())
            .limit(limit)
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def get_lifecycle_review_by_source_review_aggregation(
        self,
        db_session: Any,
        *,
        review_aggregation_run_id: str,
    ) -> Any | None:
        """Return the lifecycle review created for one MRAG, if any."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceLifecycleReview)
            .where(StrategyAdviceLifecycleReview.source_review_aggregation_run_id == review_aggregation_run_id)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_lifecycle_review_by_id(self, db_session: Any, *, review_id: str) -> Any | None:
        """Return one lifecycle review by business id."""

        _require_sqlalchemy()
        stmt = select(StrategyAdviceLifecycleReview).where(StrategyAdviceLifecycleReview.review_id == review_id).limit(1)
        return db_session.execute(stmt).scalar_one_or_none()

    def list_notification_recovery_reviews(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        limit: int,
    ) -> tuple[Any, ...]:
        """Return recent notification-required reviews for 21B recovery checks."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceLifecycleReview)
            .where(StrategyAdviceLifecycleReview.symbol == symbol)
            .where(StrategyAdviceLifecycleReview.base_interval == base_interval)
            .where(StrategyAdviceLifecycleReview.higher_interval == higher_interval)
            .where(StrategyAdviceLifecycleReview.notification_required.is_(True))
            .order_by(StrategyAdviceLifecycleReview.created_at_utc.desc(), StrategyAdviceLifecycleReview.id.desc())
            .limit(limit)
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def count_notification_failed_events(self, db_session: Any, *, review_id: str) -> int:
        """Return how many notification_failed events exist for one review."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceEvent)
            .where(StrategyAdviceEvent.related_review_id == review_id)
            .where(StrategyAdviceEvent.event_type == "notification_failed")
        )
        return len(tuple(db_session.execute(stmt).scalars().all()))

    def latest_notification_failed_at(self, db_session: Any, *, review_id: str) -> datetime | None:
        """Return the newest notification_failed timestamp for retry spacing."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceEvent.created_at_utc)
            .where(StrategyAdviceEvent.related_review_id == review_id)
            .where(StrategyAdviceEvent.event_type == "notification_failed")
            .order_by(StrategyAdviceEvent.created_at_utc.desc(), StrategyAdviceEvent.id.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def create_lifecycle_review(
        self,
        db_session: Any,
        *,
        payload: StrategyAdviceLifecycleReviewPersistencePayload,
    ) -> StrategyAdviceLifecycleReview:
        """Insert one lifecycle review row without committing."""

        row = StrategyAdviceLifecycleReview(
            review_id=payload.review_id,
            symbol=payload.symbol,
            base_interval=payload.base_interval,
            higher_interval=payload.higher_interval,
            reviewed_advice_id=payload.reviewed_advice_id,
            result_advice_id=payload.result_advice_id,
            previous_advice_id=payload.previous_advice_id,
            lifecycle_action=payload.lifecycle_action.value,
            lifecycle_reason=payload.lifecycle_reason,
            source_review_aggregation_run_id=payload.source_review_aggregation_run_id,
            source_material_pack_id=payload.source_material_pack_id,
            source_strategy_signal_run_id=payload.source_strategy_signal_run_id,
            source_snapshot_id=payload.source_snapshot_id,
            model_review_invoked=payload.model_review_invoked,
            model_review_invocation_mode=payload.model_review_invocation_mode,
            model_review_reused=payload.model_review_reused,
            reused_model_analysis_run_id=payload.reused_model_analysis_run_id,
            model_review_basis=payload.model_review_basis,
            model_review_expired=payload.model_review_expired,
            model_review_chain_status=payload.model_review_chain_status,
            notification_required=payload.notification_required,
            notification_level=payload.notification_level,
            notification_reason=payload.notification_reason,
            notification_payload_json=json_text(payload.notification_payload_json),
            created_at_utc=now_utc(),
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def create_strategy_advice_event(
        self,
        db_session: Any,
        *,
        payload: StrategyAdviceEventPersistencePayload,
    ) -> StrategyAdviceEvent:
        """Insert one stage-21C audit event row without committing."""

        row = StrategyAdviceEvent(
            event_id=payload.event_id,
            advice_id=payload.advice_id,
            related_review_id=payload.related_review_id,
            event_type=payload.event_type.value,
            event_reason=payload.event_reason,
            event_payload_json=json_text(payload.event_payload_json),
            created_at_utc=now_utc(),
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def create_scheduler_event_log(
        self,
        db_session: Any,
        *,
        event_id: str,
        job_name: str,
        symbol: str | None,
        base_interval: str | None,
        higher_interval: str | None,
        review_aggregation_run_id: str | None,
        trigger_source: str,
        status: str,
        reason: str,
        trace_id: str,
        started_at_utc: datetime,
        finished_at_utc: datetime,
        details: dict[str, Any],
    ) -> StrategyAdviceSchedulerEventLog:
        """Insert one lightweight scheduler audit log without committing."""

        row = StrategyAdviceSchedulerEventLog(
            event_id=event_id,
            job_name=job_name,
            symbol=symbol,
            base_interval=base_interval,
            higher_interval=higher_interval,
            review_aggregation_run_id=review_aggregation_run_id,
            trigger_source=trigger_source,
            status=status,
            reason=reason,
            trace_id=trace_id,
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
            details_json=json_text(details),
            created_at_utc=now_utc(),
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row


def create_default_strategy_advice_scheduler_repository() -> StrategyAdviceSchedulerRepository:
    """Create the default stage-21C scheduler repository."""

    return StrategyAdviceSchedulerRepository()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for strategy advice scheduler repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = [
    "StrategyAdviceSchedulerRepository",
    "create_default_strategy_advice_scheduler_repository",
]
