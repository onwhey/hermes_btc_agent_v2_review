"""Repository helpers for stage-25A manual strategy pipeline orchestration.

This file belongs to `app/strategy_pipeline`. It reads the formal 4h Kline
table only to resolve a base Kline slot, reads the existing 23F aggregation
table by strategy run id, and writes compact `strategy_pipeline_event_log`
audit rows.

Called by `app/strategy_pipeline/service.py`. External services: none. MySQL:
reads Kline/evidence rows and writes pipeline event logs through caller-owned
sessions. Redis: none. Hermes: none. Large models: none. Trading execution:
none.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core.time_utils import ensure_utc_aware, now_utc
from app.market_data.kline_constants import KLINE_4H_INTERVAL_VALUE
from app.storage.mysql.models.market_kline_4h import MarketKline4h
from app.storage.mysql.models.strategy_aggregation import StrategyEvidenceAggregationResult
from app.storage.mysql.models.strategy_pipeline import StrategyPipelineEventLog
from app.storage.mysql.models.strategy_signal import StrategySignalRun
from app.storage.mysql.models.strategy_signal_scheduler_event import StrategySignalSchedulerEventLog
from app.strategy_pipeline.types import StrategyPipelineEventPayload

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class StrategyPipelineRepository:
    """Data access helper for the manual 25A pipeline service.

    Parameters: none. Return value: repository instance.
    Failure scenarios: SQLAlchemy/database errors propagate to the service,
    which converts them to structured pipeline results.
    External service access: none. Data impact: no commit; caller owns
    transaction boundaries.
    """

    def resolve_latest_base_kline_slot_utc(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
    ) -> datetime | None:
        """Return the latest closed base Kline open time for a supported scope.

        Stage 25A supports the existing formal 4h table. Unsupported intervals
        return None rather than guessing another source.
        """

        if base_interval != KLINE_4H_INTERVAL_VALUE:
            return None
        _require_sqlalchemy()
        stmt = (
            select(MarketKline4h.open_time_utc)
            .where(MarketKline4h.symbol == symbol, MarketKline4h.interval_value == base_interval)
            .order_by(MarketKline4h.open_time_utc.desc())
            .limit(1)
        )
        value = db_session.execute(stmt).scalar_one_or_none()
        return ensure_utc_aware(value)

    def get_latest_strategy_evidence_aggregation(
        self,
        db_session: Any,
        *,
        strategy_signal_run_id: str,
    ) -> Any | None:
        """Return the existing 23F aggregation row for one strategy run id."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyEvidenceAggregationResult)
            .where(StrategyEvidenceAggregationResult.strategy_signal_run_id == strategy_signal_run_id)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_strategy_signal_run_by_run_id(self, db_session: Any, *, run_id: str) -> Any | None:
        """Return one stage-16 strategy signal run by business id."""

        _require_sqlalchemy()
        stmt = select(StrategySignalRun).where(StrategySignalRun.run_id == run_id).limit(1)
        return db_session.execute(stmt).scalar_one_or_none()

    def get_latest_reusable_stage17_scheduler_event(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        target_base_open_time_utc: datetime,
    ) -> Any | None:
        """Return the latest successful stage-17 event that can supply a SSR id.

        This is used only when a fresh stage-17 call reports a duplicate skip.
        It never creates a strategy run and never changes the old scheduler
        event; it only finds a prior success/partial_success row for the exact
        same target base Kline open time.
        """

        _require_sqlalchemy()
        slot = ensure_utc_aware(target_base_open_time_utc)
        stmt = (
            select(StrategySignalSchedulerEventLog)
            .where(StrategySignalSchedulerEventLog.symbol == symbol)
            .where(StrategySignalSchedulerEventLog.base_interval == base_interval)
            .where(StrategySignalSchedulerEventLog.higher_interval == higher_interval)
            .where(StrategySignalSchedulerEventLog.target_base_open_time_utc == slot)
            .where(StrategySignalSchedulerEventLog.status.in_(("success", "partial_success")))
            .where(StrategySignalSchedulerEventLog.run_id.is_not(None))
            .where(StrategySignalSchedulerEventLog.run_id != "")
            .order_by(
                StrategySignalSchedulerEventLog.created_at_utc.desc(),
                StrategySignalSchedulerEventLog.id.desc(),
            )
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_latest_stage17_scheduler_event_for_slot(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        target_base_open_time_utc: datetime,
    ) -> Any | None:
        """Return the latest stage-17 event for one exact pipeline target slot."""

        _require_sqlalchemy()
        slot = ensure_utc_aware(target_base_open_time_utc)
        stmt = (
            select(StrategySignalSchedulerEventLog)
            .where(StrategySignalSchedulerEventLog.symbol == symbol)
            .where(StrategySignalSchedulerEventLog.base_interval == base_interval)
            .where(StrategySignalSchedulerEventLog.higher_interval == higher_interval)
            .where(StrategySignalSchedulerEventLog.target_base_open_time_utc == slot)
            .order_by(
                StrategySignalSchedulerEventLog.created_at_utc.desc(),
                StrategySignalSchedulerEventLog.id.desc(),
            )
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_latest_retryable_failed_stage17_scheduler_event_for_slot(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        target_base_open_time_utc: datetime,
    ) -> Any | None:
        """Return the latest failed/blocked stage-17 event for retry review."""

        _require_sqlalchemy()
        slot = ensure_utc_aware(target_base_open_time_utc)
        stmt = (
            select(StrategySignalSchedulerEventLog)
            .where(StrategySignalSchedulerEventLog.symbol == symbol)
            .where(StrategySignalSchedulerEventLog.base_interval == base_interval)
            .where(StrategySignalSchedulerEventLog.higher_interval == higher_interval)
            .where(StrategySignalSchedulerEventLog.target_base_open_time_utc == slot)
            .where(StrategySignalSchedulerEventLog.status.in_(("failed", "blocked")))
            .order_by(
                StrategySignalSchedulerEventLog.created_at_utc.desc(),
                StrategySignalSchedulerEventLog.id.desc(),
            )
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_latest_in_progress_stage17_scheduler_event_for_slot(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        target_base_open_time_utc: datetime,
    ) -> Any | None:
        """Return a running/waiting stage-17 event that blocks manual retry."""

        _require_sqlalchemy()
        slot = ensure_utc_aware(target_base_open_time_utc)
        stmt = (
            select(StrategySignalSchedulerEventLog)
            .where(StrategySignalSchedulerEventLog.symbol == symbol)
            .where(StrategySignalSchedulerEventLog.base_interval == base_interval)
            .where(StrategySignalSchedulerEventLog.higher_interval == higher_interval)
            .where(StrategySignalSchedulerEventLog.target_base_open_time_utc == slot)
            .where(StrategySignalSchedulerEventLog.status.in_(("running", "waiting_upstream")))
            .order_by(
                StrategySignalSchedulerEventLog.created_at_utc.desc(),
                StrategySignalSchedulerEventLog.id.desc(),
            )
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def create_pipeline_event_log(
        self,
        db_session: Any,
        *,
        payload: StrategyPipelineEventPayload,
    ) -> Any:
        """Insert one compact pipeline audit row without committing."""

        now = now_utc()
        row = StrategyPipelineEventLog(
            pipeline_run_id=payload.pipeline_run_id,
            symbol=payload.symbol,
            base_interval=payload.base_interval,
            higher_interval=payload.higher_interval,
            kline_slot_utc=ensure_utc_aware(payload.kline_slot_utc),
            kline_slot_source=payload.kline_slot_source,
            trigger_source=payload.trigger_source,
            status=payload.status,
            current_step=payload.current_step,
            strategy_signal_run_id=payload.strategy_signal_run_id,
            strategy_evidence_aggregation_id=payload.strategy_evidence_aggregation_id,
            material_pack_id=payload.material_pack_id,
            model_analysis_run_id=payload.model_analysis_run_id,
            review_aggregation_run_id=payload.review_aggregation_run_id,
            advice_id=payload.advice_id,
            review_id=payload.review_id,
            notification_status=payload.notification_status,
            model_review_invoked=payload.model_review_invoked,
            model_review_reused=payload.model_review_reused,
            real_model_called=payload.real_model_called,
            hermes_real_sent=payload.hermes_real_sent,
            error_code=payload.error_code,
            error_message=payload.error_message,
            trace_id=payload.trace_id,
            details_json=_json_text(payload.details),
            started_at_utc=now,
            finished_at_utc=None,
            created_at_utc=now,
            updated_at_utc=now,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def update_pipeline_event_log(
        self,
        db_session: Any,
        *,
        row: Any,
        payload: StrategyPipelineEventPayload,
        finished: bool,
    ) -> Any:
        """Update one pipeline audit row without committing."""

        row.status = payload.status
        row.current_step = payload.current_step
        row.kline_slot_utc = ensure_utc_aware(payload.kline_slot_utc)
        row.kline_slot_source = payload.kline_slot_source
        row.strategy_signal_run_id = payload.strategy_signal_run_id
        row.strategy_evidence_aggregation_id = payload.strategy_evidence_aggregation_id
        row.material_pack_id = payload.material_pack_id
        row.model_analysis_run_id = payload.model_analysis_run_id
        row.review_aggregation_run_id = payload.review_aggregation_run_id
        row.advice_id = payload.advice_id
        row.review_id = payload.review_id
        row.notification_status = payload.notification_status
        row.model_review_invoked = payload.model_review_invoked
        row.model_review_reused = payload.model_review_reused
        row.real_model_called = payload.real_model_called
        row.hermes_real_sent = payload.hermes_real_sent
        row.error_code = payload.error_code
        row.error_message = payload.error_message
        row.details_json = _json_text(payload.details)
        row.finished_at_utc = now_utc() if finished else None
        row.updated_at_utc = now_utc()
        _flush_if_possible(db_session)
        return row


def create_default_strategy_pipeline_repository() -> StrategyPipelineRepository:
    """Create the default 25A pipeline repository."""

    return StrategyPipelineRepository()


def _json_text(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for strategy pipeline repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = [
    "StrategyPipelineRepository",
    "create_default_strategy_pipeline_repository",
]
