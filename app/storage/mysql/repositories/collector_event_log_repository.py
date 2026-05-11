"""Repository for collector_event_log records.

This file belongs to `app/storage/mysql/repositories`.
It creates and updates task event records through caller-provided sessions.
It does not request Binance, write formal Klines, read or write Redis, send
Hermes, call DeepSeek, repair Klines, execute migrations, or trade.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from app.core.time_utils import now_utc, utc_aware_to_prc_aware
from app.storage.mysql.models.collector_event_log import CollectorEventLog


class CollectorEventLogRepository:
    """Data access helper for `collector_event_log`.

    Methods never commit. The caller owns transaction boundaries so formal Kline
    writes can stay all-or-nothing while event logs remain explicit and auditable.
    """

    def create_running_event(
        self,
        db_session: Any,
        *,
        event_type: str,
        symbol: str,
        interval_value: str,
        trigger_source: str,
        data_source: str,
        requested_start_open_time_ms: int,
        requested_end_open_time_ms: int,
        requested_count: int,
        trace_id: str,
        details: Mapping[str, Any] | None = None,
    ) -> CollectorEventLog:
        """Create a running event before Binance requests and formal writes."""

        now = now_utc()
        now_prc = utc_aware_to_prc_aware(now)
        record = CollectorEventLog(
            event_type=event_type,
            symbol=symbol,
            interval_value=interval_value,
            trigger_source=trigger_source,
            data_source=data_source,
            status="running",
            severity="info",
            requested_start_open_time_ms=requested_start_open_time_ms,
            requested_end_open_time_ms=requested_end_open_time_ms,
            requested_count=requested_count,
            trace_id=trace_id,
            details_json=_json_dumps(details or {}),
            started_at_utc=now,
            started_at_prc=now_prc,
            created_at_utc=now,
            created_at_prc=now_prc,
            updated_at_utc=now,
            updated_at_prc=now_prc,
        )
        db_session.add(record)
        _flush(db_session)
        return record

    def create_skipped_event(
        self,
        db_session: Any,
        *,
        event_type: str,
        symbol: str,
        interval_value: str,
        trigger_source: str,
        data_source: str,
        requested_start_open_time_ms: int,
        requested_end_open_time_ms: int,
        requested_count: int,
        trace_id: str,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> CollectorEventLog:
        """Create a skipped event when concurrency rules prevent execution."""

        event = self.create_running_event(
            db_session,
            event_type=event_type,
            symbol=symbol,
            interval_value=interval_value,
            trigger_source=trigger_source,
            data_source=data_source,
            requested_start_open_time_ms=requested_start_open_time_ms,
            requested_end_open_time_ms=requested_end_open_time_ms,
            requested_count=requested_count,
            trace_id=trace_id,
            details=details,
        )
        return self.mark_event_status(
            db_session,
            event,
            status="skipped",
            severity="warning",
            error_code="task_lock_not_acquired",
            error_message=reason,
        )

    def mark_success(
        self,
        db_session: Any,
        event: CollectorEventLog,
        **values: Any,
    ) -> CollectorEventLog:
        """Mark an event as successful with final counters."""

        return self.mark_event_status(db_session, event, status="success", severity="info", **values)

    def mark_blocked(
        self,
        db_session: Any,
        event: CollectorEventLog,
        **values: Any,
    ) -> CollectorEventLog:
        """Mark an event as blocked by data-quality rules."""

        return self.mark_event_status(db_session, event, status="blocked", severity="error", **values)

    def mark_failed(
        self,
        db_session: Any,
        event: CollectorEventLog,
        **values: Any,
    ) -> CollectorEventLog:
        """Mark an event as failed due to task, dependency, or persistence errors."""

        return self.mark_event_status(db_session, event, status="failed", severity="critical", **values)

    def mark_event_status(
        self,
        db_session: Any,
        event: CollectorEventLog,
        *,
        status: str,
        severity: str,
        fetched_count: int | None = None,
        parsed_count: int | None = None,
        closed_count: int | None = None,
        inserted_count: int | None = None,
        skipped_count: int | None = None,
        conflict_count: int | None = None,
        filtered_unclosed_count: int | None = None,
        issue_count: int | None = None,
        actual_start_open_time_ms: int | None = None,
        actual_end_open_time_ms: int | None = None,
        quality_check_id: int | None = None,
        alert_message_id: int | None = None,
        first_issue_type: str | None = None,
        first_issue_message: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        report_json: str | Mapping[str, Any] | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> CollectorEventLog:
        """Update final status and counters without committing."""

        event.status = status
        event.severity = severity
        for attr_name, value in {
            "fetched_count": fetched_count,
            "parsed_count": parsed_count,
            "closed_count": closed_count,
            "inserted_count": inserted_count,
            "skipped_count": skipped_count,
            "conflict_count": conflict_count,
            "filtered_unclosed_count": filtered_unclosed_count,
            "issue_count": issue_count,
            "actual_start_open_time_ms": actual_start_open_time_ms,
            "actual_end_open_time_ms": actual_end_open_time_ms,
            "quality_check_id": quality_check_id,
            "alert_message_id": alert_message_id,
        }.items():
            if value is not None:
                setattr(event, attr_name, value)
        event.first_issue_type = first_issue_type
        event.first_issue_message = first_issue_message
        event.error_code = error_code
        event.error_message = error_message
        if report_json is not None:
            event.report_json = report_json if isinstance(report_json, str) else _json_dumps(report_json)
        if details is not None:
            event.details_json = _json_dumps(details)
        now = now_utc()
        event.finished_at_utc = now
        event.finished_at_prc = utc_aware_to_prc_aware(now)
        event.updated_at_utc = now
        event.updated_at_prc = utc_aware_to_prc_aware(now)
        _flush(db_session)
        return event


def create_default_collector_event_log_repository() -> CollectorEventLogRepository:
    """Create the default collector event-log repository."""

    return CollectorEventLogRepository()


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, default=str)


def _flush(db_session: Any) -> None:
    if hasattr(db_session, "flush"):
        db_session.flush()

