"""Repository for stage-21B strategy advice notification delivery.

This file belongs to `app/strategy_advice`. It reads 21A lifecycle reviews,
checks notification idempotency, writes existing `alert_message` records, and
writes strategy-advice notification events.

Called by `app/strategy_advice/notification_sender.py`. External services:
none in this file. MySQL: reads/writes through the caller-owned session and
never commits. Redis: none. Hermes: none. Large-model calls: none. Trading
execution: none.
"""

from __future__ import annotations

from typing import Any

from app.alerting.sanitizer import sanitize_mapping, sanitize_text
from app.alerting.types import AlertEvent, AlertSendResult
from app.core.time_utils import now_utc
from app.storage.mysql.models.alert_message import AlertMessage
from app.strategy_advice.id_utils import build_strategy_advice_event_id
from app.strategy_advice.models import StrategyAdviceEvent, StrategyAdviceLifecycleReview
from app.strategy_advice.schema import AdviceEventType, json_text

try:
    from sqlalchemy import func, select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    func = select = None  # type: ignore[assignment]

SUCCESSFUL_ALERT_STATUSES = frozenset({"submitted_to_hermes", "accepted", "success"})
NOTIFICATION_DELIVERY_EVENT_TYPES = frozenset(
    {
        AdviceEventType.NOTIFICATION_PREPARED.value,
        AdviceEventType.NOTIFICATION_SENT.value,
        AdviceEventType.NOTIFICATION_FAILED.value,
        AdviceEventType.NOTIFICATION_SKIPPED.value,
    }
)


class StrategyAdviceNotificationRepository:
    """Data access helper for 21B notification sending.

    Failure scenarios: database query/insert/update errors propagate to the
    service, which converts them into structured results and rolls back.
    External service access: none.
    Data impact: writes only `alert_message` and `strategy_advice_event`.
    """

    def get_lifecycle_review_by_id(self, db_session: Any, *, review_id: str) -> Any | None:
        """Return one 21A lifecycle review by business id."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceLifecycleReview)
            .where(StrategyAdviceLifecycleReview.review_id == review_id)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def has_successful_notification_event(self, db_session: Any, *, review_id: str) -> bool:
        """Return whether a notification_sent event already exists."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceEvent.event_id)
            .where(StrategyAdviceEvent.related_review_id == review_id)
            .where(StrategyAdviceEvent.event_type == AdviceEventType.NOTIFICATION_SENT.value)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none() is not None

    def has_successful_alert_message(
        self,
        db_session: Any,
        *,
        related_type: str,
        related_id: str,
    ) -> bool:
        """Return whether a successful alert already exists for the relation."""

        _require_sqlalchemy()
        stmt = (
            select(AlertMessage.id)
            .where(AlertMessage.alert_type == "strategy_advice")
            .where(AlertMessage.related_type == related_type)
            .where(AlertMessage.related_id == related_id)
            .where(AlertMessage.status.in_(tuple(SUCCESSFUL_ALERT_STATUSES)))
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none() is not None

    def count_notification_delivery_events(self, db_session: Any, *, review_id: str) -> int:
        """Return existing 21B notification event count for stable event ids."""

        _require_sqlalchemy()
        stmt = (
            select(func.count(StrategyAdviceEvent.event_id))
            .where(StrategyAdviceEvent.related_review_id == review_id)
            .where(StrategyAdviceEvent.event_type.in_(tuple(NOTIFICATION_DELIVERY_EVENT_TYPES)))
        )
        return int(db_session.execute(stmt).scalar_one())

    def create_alert_message(
        self,
        db_session: Any,
        *,
        event: AlertEvent,
        message: str,
        related_type: str,
        related_id: str,
        initial_status: str,
        channel_response: dict[str, Any] | None = None,
    ) -> AlertMessage:
        """Insert one `alert_message` row without committing."""

        now = now_utc()
        row = AlertMessage(
            alert_type=event.alert_type.value,
            severity=event.severity.value,
            title=sanitize_text(event.title),
            message=sanitize_text(message),
            channel="hermes",
            status=initial_status,
            source=sanitize_text(event.source),
            trace_id=sanitize_text(event.trace_id),
            related_type=sanitize_text(related_type),
            related_id=sanitize_text(related_id),
            channel_response=sanitize_mapping(channel_response or {}),
            error_message=None,
            retry_count=0,
            http_status_code=None,
            occurred_at_utc=event.occurred_at_utc,
            sent_at_utc=None,
            created_at_utc=now,
            updated_at_utc=now,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def update_alert_message_result(self, db_session: Any, *, alert_message: Any, result: AlertSendResult) -> Any:
        """Update one alert row with a Hermes client result."""

        alert_message.status = result.status.value
        alert_message.channel_response = sanitize_mapping(result.channel_response)
        alert_message.error_message = sanitize_text(result.error_message) if result.error_message else None
        alert_message.retry_count = result.retry_count
        alert_message.http_status_code = result.http_status_code
        alert_message.sent_at_utc = result.submitted_at_utc
        alert_message.updated_at_utc = now_utc()
        _flush_if_possible(db_session)
        return alert_message

    def create_notification_event(
        self,
        db_session: Any,
        *,
        review_id: str,
        advice_id: str | None,
        event_type: AdviceEventType,
        event_reason: str,
        event_payload: dict[str, Any],
    ) -> StrategyAdviceEvent:
        """Insert one 21B strategy advice notification event."""

        sequence_no = self.count_notification_delivery_events(db_session, review_id=review_id) + 1
        row = StrategyAdviceEvent(
            event_id=build_strategy_advice_event_id(
                review_id=review_id,
                event_type=event_type.value,
                sequence_no=sequence_no,
            ),
            advice_id=advice_id,
            related_review_id=review_id,
            event_type=event_type.value,
            event_reason=event_reason,
            event_payload_json=json_text(event_payload),
            created_at_utc=now_utc(),
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row


def create_default_strategy_advice_notification_repository() -> StrategyAdviceNotificationRepository:
    """Create the default stage-21B notification repository."""

    return StrategyAdviceNotificationRepository()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for strategy advice notification repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = [
    "NOTIFICATION_DELIVERY_EVENT_TYPES",
    "SUCCESSFUL_ALERT_STATUSES",
    "StrategyAdviceNotificationRepository",
    "create_default_strategy_advice_notification_repository",
]
