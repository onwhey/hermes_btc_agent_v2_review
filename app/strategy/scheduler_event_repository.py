"""Repository for stage-17 strategy signal scheduler event persistence.

This file belongs to `app/strategy` because it records orchestration around
stage-16 strategy signal runs. It writes only
`strategy_signal_scheduler_event_log` and never writes strategy result rows,
formal Kline tables, Redis, Hermes, exchange data, large-model data, private
trading state, or final trading advice. It is called by
`app/scheduler/strategy_signal_scheduler_service.py`.
"""

from __future__ import annotations

from typing import Any

from app.core.time_utils import now_utc
from app.storage.mysql.models.strategy_signal_scheduler_event import StrategySignalSchedulerEventLog

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class StrategySignalSchedulerEventRepository:
    """Persist and update stage-17 scheduler orchestration events.

    Parameters: none.
    Return value: repository instance.
    Failure scenarios: database insert, uniqueness, or update errors propagate
    to the scheduler orchestration service.
    External service access: none.
    Data impact: writes only `strategy_signal_scheduler_event_log` and never
    commits; the service owns transaction timing so `running` can be stored
    before calling stage 16.
    """

    def get_event_by_target(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        target_base_open_time_ms: int,
    ) -> Any | None:
        """Return one scheduler event for the unique 4h target window."""

        _require_sqlalchemy()
        stmt = (
            select(StrategySignalSchedulerEventLog)
            .where(StrategySignalSchedulerEventLog.symbol == symbol)
            .where(StrategySignalSchedulerEventLog.base_interval == base_interval)
            .where(StrategySignalSchedulerEventLog.higher_interval == higher_interval)
            .where(StrategySignalSchedulerEventLog.target_base_open_time_ms == target_base_open_time_ms)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def create_scheduler_event(
        self,
        db_session: Any,
        *,
        payload: Any,
    ) -> StrategySignalSchedulerEventLog:
        """Insert a scheduler event row without committing the session."""

        created_at_utc = now_utc()
        row = StrategySignalSchedulerEventLog(
            event_id=payload.event_id,
            symbol=payload.symbol,
            base_interval=payload.base_interval,
            higher_interval=payload.higher_interval,
            target_base_open_time_ms=payload.target_base_open_time_ms,
            target_base_open_time_utc=payload.target_base_open_time_utc,
            target_base_close_time_ms=payload.target_base_close_time_ms,
            target_base_close_time_utc=payload.target_base_close_time_utc,
            target_higher_open_time_ms=payload.target_higher_open_time_ms,
            target_higher_open_time_utc=payload.target_higher_open_time_utc,
            status=payload.status,
            trigger_source=payload.trigger_source,
            trigger_reason=payload.trigger_reason,
            run_id=None,
            snapshot_id=None,
            upstream_4h_collector_event_id=payload.upstream_4h_collector_event_id,
            upstream_1d_collector_event_id=payload.upstream_1d_collector_event_id,
            strategy_count=0,
            success_count=0,
            failed_count=0,
            invalid_count=0,
            not_implemented_count=0,
            message=payload.message,
            error_code=payload.error_code,
            error_message=payload.error_message,
            trace_id=payload.trace_id,
            hermes_enabled=payload.hermes_enabled,
            hermes_status=payload.hermes_status,
            hermes_message=None,
            hermes_error=None,
            hermes_sent_at_utc=None,
            skip_count=0,
            last_skipped_at_utc=None,
            last_skip_reason=None,
            started_at_utc=payload.started_at_utc,
            finished_at_utc=payload.finished_at_utc,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def mark_event_running(
        self,
        db_session: Any,
        event: Any,
        *,
        message: str,
        upstream_1d_collector_event_id: int | None = None,
    ) -> Any:
        """Update a waiting event to running before stage 16 is called."""

        updated_at_utc = now_utc()
        event.status = "running"
        event.message = message
        event.error_code = None
        event.error_message = None
        event.started_at_utc = updated_at_utc
        event.finished_at_utc = None
        event.updated_at_utc = updated_at_utc
        if upstream_1d_collector_event_id is not None:
            event.upstream_1d_collector_event_id = upstream_1d_collector_event_id
        _flush_if_possible(db_session)
        return event

    def mark_event_completed_from_strategy_result(
        self,
        db_session: Any,
        event: Any,
        *,
        status: str,
        run_id: str | None,
        snapshot_id: str | None,
        strategy_count: int,
        success_count: int,
        failed_count: int,
        invalid_count: int,
        not_implemented_count: int,
        message: str,
        error_code: str | None,
        error_message: str | None,
    ) -> Any:
        """Store the final stage-16 result summary on the scheduler event."""

        updated_at_utc = now_utc()
        event.status = status
        event.run_id = run_id
        event.snapshot_id = snapshot_id
        event.strategy_count = strategy_count
        event.success_count = success_count
        event.failed_count = failed_count
        event.invalid_count = invalid_count
        event.not_implemented_count = not_implemented_count
        event.message = message
        event.error_code = error_code
        event.error_message = error_message
        event.finished_at_utc = updated_at_utc
        event.updated_at_utc = updated_at_utc
        _flush_if_possible(db_session)
        return event

    def mark_duplicate_skipped(
        self,
        db_session: Any,
        event: Any,
        *,
        reason: str,
    ) -> Any:
        """Record a duplicate trigger on the existing unique event row."""

        updated_at_utc = now_utc()
        event.skip_count = int(getattr(event, "skip_count", 0) or 0) + 1
        event.last_skipped_at_utc = updated_at_utc
        event.last_skip_reason = reason
        event.updated_at_utc = updated_at_utc
        _flush_if_possible(db_session)
        return event

    def record_hermes_result(
        self,
        db_session: Any,
        event: Any,
        *,
        hermes_status: str,
        hermes_message: str | None,
        hermes_error: str | None,
        hermes_sent_at_utc: Any | None,
    ) -> Any:
        """Store stage-17 Hermes dispatch outcome without changing run status."""

        event.hermes_status = hermes_status
        event.hermes_message = hermes_message
        event.hermes_error = hermes_error
        event.hermes_sent_at_utc = hermes_sent_at_utc
        event.updated_at_utc = now_utc()
        _flush_if_possible(db_session)
        return event


def create_default_strategy_signal_scheduler_event_repository() -> StrategySignalSchedulerEventRepository:
    """Create the default scheduler event repository."""

    return StrategySignalSchedulerEventRepository()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for strategy signal scheduler event queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = [
    "StrategySignalSchedulerEventRepository",
    "create_default_strategy_signal_scheduler_event_repository",
]
