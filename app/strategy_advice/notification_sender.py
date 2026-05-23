"""Stage-21B strategy advice notification sender service.

Call chain:
scripts/send_strategy_advice_notification.py::main
    -> app/strategy_advice/notification_sender.py::send_strategy_advice_notification
    -> app/strategy_advice/notification_repository.py::get_lifecycle_review_by_id
    -> app/strategy_advice/notification_renderer.py::render_strategy_advice_notification
    -> app/strategy_advice/notification_repository.py::create_alert_message
    -> app/alerting/hermes_client.py::HermesClient.send_alert_message
       (only with --send-real-alert)
    -> app/strategy_advice/notification_repository.py::create_notification_event

This file belongs to `app/strategy_advice`. It reads 21A notification payloads,
renders Chinese Hermes content, writes existing alert_message records when
confirmed, and calls the existing Hermes client only when explicitly allowed.

It does not call stage 19, does not call model providers, does not regenerate
strategy advice, does not connect scheduler jobs, does not read private trading
state, does not modify formal Kline tables, and does not perform trading.
"""

from __future__ import annotations

from typing import Any

from app.alerting.hermes_client import HermesClient
from app.alerting.types import AlertEvent, AlertSendStatus, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.strategy_advice.notification_renderer import render_strategy_advice_notification
from app.strategy_advice.notification_repository import (
    StrategyAdviceNotificationRepository,
    create_default_strategy_advice_notification_repository,
)
from app.strategy_advice.notification_schema import (
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    STRATEGY_ADVICE_NOTIFICATION_SOURCE,
    RenderedStrategyAdviceNotification,
    StrategyAdviceNotificationRequest,
    StrategyAdviceNotificationResult,
    StrategyAdviceNotificationStatus,
)
from app.strategy_advice.schema import AdviceEventType

ALLOWED_STRATEGY_ADVICE_NOTIFICATION_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI})


class StrategyAdviceNotificationSender:
    """Coordinate one 21B strategy-advice notification attempt.

    Parameters: repository, settings, and Hermes client are injectable for tests.
    Return value: service instance.
    Failure scenarios: invalid request, missing lifecycle review, malformed
    notification payload, database errors, and Hermes submission failures are
    converted into structured results.
    External effects: dry-run reads only; confirm-write writes alert/event rows;
    send-real-alert may call Hermes through the existing client.
    """

    def __init__(
        self,
        *,
        repository: StrategyAdviceNotificationRepository | Any | None = None,
        settings: AppSettings | None = None,
        hermes_client: HermesClient | Any | None = None,
    ) -> None:
        self._repository = repository or create_default_strategy_advice_notification_repository()
        self._settings = settings or get_settings()
        self._hermes_client = hermes_client or HermesClient(self._settings)

    def send_strategy_advice_notification(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceNotificationRequest,
    ) -> StrategyAdviceNotificationResult:
        """Render and optionally send one 21B strategy-advice notification."""

        invalid = _validate_notification_request(request)
        if invalid is not None:
            return invalid
        try:
            review_row = self._repository.get_lifecycle_review_by_id(db_session, review_id=request.review_id)
        except Exception as exc:  # noqa: BLE001 - service reports repository failure.
            _rollback_if_possible(db_session)
            return _failed_result(request=request, error_code="lifecycle_review_lookup_failed", error_message=str(exc))
        if review_row is None:
            return _blocked_result(
                request=request,
                error_code="lifecycle_review_not_found",
                error_message="strategy_advice_lifecycle_review does not exist.",
            )

        try:
            rendered = render_strategy_advice_notification(review_row)
        except Exception as exc:  # noqa: BLE001 - malformed payload becomes blocked.
            return _blocked_result(
                request=request,
                error_code="notification_payload_render_failed",
                error_message=str(exc),
            )

        if not bool(getattr(review_row, "notification_required", False)):
            return _skipped_result(
                request=request,
                rendered=rendered,
                reason="notification_required=false",
                event_type=None,
            )
        if not rendered.payload:
            return _blocked_result(
                request=request,
                rendered=rendered,
                error_code="notification_payload_empty",
                error_message="notification_payload_json is empty or malformed.",
            )
        try:
            if self._repository.has_successful_notification_event(db_session, review_id=request.review_id):
                return _skipped_result(
                    request=request,
                    rendered=rendered,
                    reason="notification_sent event already exists",
                    event_type=AdviceEventType.NOTIFICATION_SKIPPED,
                )
            if self._repository.has_successful_alert_message(
                db_session,
                related_type=rendered.related_type,
                related_id=rendered.related_id,
            ):
                return _skipped_result(
                    request=request,
                    rendered=rendered,
                    reason="successful alert_message already exists",
                    event_type=AdviceEventType.NOTIFICATION_SKIPPED,
                )
        except Exception as exc:  # noqa: BLE001 - idempotency lookup failure is explicit.
            _rollback_if_possible(db_session)
            return _failed_result(
                request=request,
                rendered=rendered,
                error_code="notification_idempotency_lookup_failed",
                error_message=str(exc),
            )

        if request.dry_run:
            return _success_result(
                request=request,
                rendered=rendered,
                alert_status="preview",
                event_type=None,
                hermes_status="not_attempted",
            )
        try:
            return self._persist_and_maybe_send(
                db_session=db_session,
                request=request,
                review_row=review_row,
                rendered=rendered,
            )
        except Exception as exc:  # noqa: BLE001 - persistence/send orchestration failure is explicit.
            _rollback_if_possible(db_session)
            return _failed_result(
                request=request,
                rendered=rendered,
                error_code="strategy_advice_notification_persistence_failed",
                error_message=str(exc),
            )

    def _persist_and_maybe_send(
        self,
        *,
        db_session: Any,
        request: StrategyAdviceNotificationRequest,
        review_row: Any,
        rendered: RenderedStrategyAdviceNotification,
    ) -> StrategyAdviceNotificationResult:
        event = _build_alert_event(request=request, rendered=rendered)
        if not request.send_real_alert:
            alert_row = self._repository.create_alert_message(
                db_session,
                event=event,
                message=rendered.message,
                related_type=rendered.related_type,
                related_id=rendered.related_id,
                initial_status=AlertSendStatus.SKIPPED.value,
                channel_response={"reason": "confirm_write_without_send_real_alert"},
            )
            event_row = self._repository.create_notification_event(
                db_session,
                review_id=request.review_id,
                advice_id=_event_advice_id(rendered),
                event_type=AdviceEventType.NOTIFICATION_PREPARED,
                event_reason="notification prepared; real Hermes send was not requested",
                event_payload=_event_payload(
                    alert_message=alert_row,
                    rendered=rendered,
                    request=request,
                    hermes_status="not_attempted",
                    error_message="",
                ),
            )
            _commit_if_possible(db_session)
            return _success_result(
                request=request,
                rendered=rendered,
                alert_message_id=_alert_message_id(alert_row),
                alert_status=getattr(alert_row, "status", None),
                event_type=getattr(event_row, "event_type", AdviceEventType.NOTIFICATION_PREPARED.value),
                hermes_status="not_attempted",
            )

        alert_row = self._repository.create_alert_message(
            db_session,
            event=event,
            message=rendered.message,
            related_type=rendered.related_type,
            related_id=rendered.related_id,
            initial_status=AlertSendStatus.PENDING.value,
        )
        send_result = self._hermes_client.send_alert_message(
            event,
            rendered.message,
            send_real_alert=True,
        )
        self._repository.update_alert_message_result(db_session, alert_message=alert_row, result=send_result)
        event_type = _event_type_for_send_result(send_result.status)
        event_row = self._repository.create_notification_event(
            db_session,
            review_id=request.review_id,
            advice_id=_event_advice_id(rendered),
            event_type=event_type,
            event_reason=_event_reason_for_send_result(send_result.status),
            event_payload=_event_payload(
                alert_message=alert_row,
                rendered=rendered,
                request=request,
                hermes_status=send_result.status.value,
                error_message=send_result.error_message,
            ),
        )
        _commit_if_possible(db_session)
        result_status = (
            StrategyAdviceNotificationStatus.SUCCESS
            if send_result.status == AlertSendStatus.SUBMITTED_TO_HERMES
            else StrategyAdviceNotificationStatus.FAILED
        )
        exit_code = EXIT_SUCCESS if result_status == StrategyAdviceNotificationStatus.SUCCESS else EXIT_FAILED
        return _base_result(
            status=result_status,
            exit_code=exit_code,
            request=request,
            rendered=rendered,
            alert_message_id=_alert_message_id(alert_row),
            alert_status=getattr(alert_row, "status", None),
            event_type=getattr(event_row, "event_type", event_type.value),
            hermes_status=send_result.status.value,
            error_code=None if result_status == StrategyAdviceNotificationStatus.SUCCESS else "hermes_submit_failed",
            error_message=send_result.error_message or None,
        )


def send_strategy_advice_notification(
    *,
    db_session: Any,
    request: StrategyAdviceNotificationRequest,
    service: StrategyAdviceNotificationSender | None = None,
) -> StrategyAdviceNotificationResult:
    """Convenience app-service function used by CLI and tests."""

    active_service = service or create_default_strategy_advice_notification_sender()
    return active_service.send_strategy_advice_notification(db_session, request=request)


def create_default_strategy_advice_notification_sender() -> StrategyAdviceNotificationSender:
    """Create the default stage-21B notification sender service."""

    return StrategyAdviceNotificationSender()


def _validate_notification_request(
    request: StrategyAdviceNotificationRequest,
) -> StrategyAdviceNotificationResult | None:
    problems: list[str] = []
    if not request.review_id.strip():
        problems.append("review_id is required")
    if request.trigger_source not in ALLOWED_STRATEGY_ADVICE_NOTIFICATION_TRIGGER_SOURCES:
        problems.append("trigger_source supports only cli in stage 21B")
    if request.dry_run and request.confirm_write:
        problems.append("dry_run and confirm_write cannot both be true")
    if not request.dry_run and not request.confirm_write:
        problems.append("non-dry-run notification requires confirm_write")
    if request.send_real_alert and not request.confirm_write:
        problems.append("send_real_alert requires confirm_write")
    if not problems:
        return None
    return StrategyAdviceNotificationResult(
        status=StrategyAdviceNotificationStatus.FAILED,
        exit_code=EXIT_PARAMETER_ERROR,
        review_id=request.review_id,
        trace_id=request.trace_id,
        send_real_alert=request.send_real_alert,
        dry_run=request.dry_run,
        error_code="invalid_request",
        error_message="; ".join(problems),
    )


def _build_alert_event(
    *,
    request: StrategyAdviceNotificationRequest,
    rendered: RenderedStrategyAdviceNotification,
) -> AlertEvent:
    return AlertEvent(
        alert_type=AlertType.STRATEGY_ADVICE,
        severity=AlertSeverity(rendered.severity),
        title=rendered.title,
        summary=rendered.model_status_summary,
        details={
            "review_id": request.review_id,
            "related_type": rendered.related_type,
            "related_id": rendered.related_id,
            "notification_level": rendered.notification_level,
            "is_trading_signal": False,
            "is_executable": False,
            "auto_trading_allowed": False,
        },
        source=STRATEGY_ADVICE_NOTIFICATION_SOURCE,
        trace_id=request.trace_id,
    )


def _event_type_for_send_result(status: AlertSendStatus) -> AdviceEventType:
    if status == AlertSendStatus.SUBMITTED_TO_HERMES:
        return AdviceEventType.NOTIFICATION_SENT
    if status == AlertSendStatus.SKIPPED:
        return AdviceEventType.NOTIFICATION_SKIPPED
    return AdviceEventType.NOTIFICATION_FAILED


def _event_reason_for_send_result(status: AlertSendStatus) -> str:
    if status == AlertSendStatus.SUBMITTED_TO_HERMES:
        return "notification submitted to Hermes gateway"
    if status == AlertSendStatus.SKIPPED:
        return "notification send skipped by Hermes client or configuration"
    return "notification submit to Hermes failed"


def _event_payload(
    *,
    alert_message: Any,
    rendered: RenderedStrategyAdviceNotification,
    request: StrategyAdviceNotificationRequest,
    hermes_status: str,
    error_message: str,
) -> dict[str, Any]:
    return {
        "alert_message_id": _alert_message_id(alert_message),
        "related_type": rendered.related_type,
        "related_id": rendered.related_id,
        "notification_level": rendered.notification_level,
        "send_real_alert": request.send_real_alert,
        "hermes_status": hermes_status,
        "error_message": error_message,
        "is_trading_signal": False,
        "is_executable": False,
        "auto_trading_allowed": False,
    }


def _success_result(
    *,
    request: StrategyAdviceNotificationRequest,
    rendered: RenderedStrategyAdviceNotification,
    alert_message_id: int | None = None,
    alert_status: str | None,
    event_type: str | None,
    hermes_status: str,
) -> StrategyAdviceNotificationResult:
    return _base_result(
        status=StrategyAdviceNotificationStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        request=request,
        rendered=rendered,
        alert_message_id=alert_message_id,
        alert_status=alert_status,
        event_type=event_type,
        hermes_status=hermes_status,
    )


def _skipped_result(
    *,
    request: StrategyAdviceNotificationRequest,
    rendered: RenderedStrategyAdviceNotification,
    reason: str,
    event_type: AdviceEventType | None,
) -> StrategyAdviceNotificationResult:
    return _base_result(
        status=StrategyAdviceNotificationStatus.SKIPPED,
        exit_code=EXIT_SUCCESS,
        request=request,
        rendered=rendered,
        alert_message_id=None,
        alert_status="skipped",
        event_type=event_type.value if event_type else None,
        hermes_status="not_attempted",
        error_code="notification_skipped",
        error_message=reason,
    )


def _blocked_result(
    *,
    request: StrategyAdviceNotificationRequest,
    error_code: str,
    error_message: str,
    rendered: RenderedStrategyAdviceNotification | None = None,
) -> StrategyAdviceNotificationResult:
    if rendered is None:
        return StrategyAdviceNotificationResult(
            status=StrategyAdviceNotificationStatus.BLOCKED,
            exit_code=EXIT_BLOCKED,
            review_id=request.review_id,
            trace_id=request.trace_id,
            send_real_alert=request.send_real_alert,
            dry_run=request.dry_run,
            error_code=error_code,
            error_message=error_message,
        )
    return _base_result(
        status=StrategyAdviceNotificationStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        request=request,
        rendered=rendered,
        alert_message_id=None,
        alert_status=None,
        event_type=None,
        hermes_status="not_attempted",
        error_code=error_code,
        error_message=error_message,
    )


def _failed_result(
    *,
    request: StrategyAdviceNotificationRequest,
    error_code: str,
    error_message: str,
    rendered: RenderedStrategyAdviceNotification | None = None,
) -> StrategyAdviceNotificationResult:
    if rendered is None:
        return StrategyAdviceNotificationResult(
            status=StrategyAdviceNotificationStatus.FAILED,
            exit_code=EXIT_FAILED,
            review_id=request.review_id,
            trace_id=request.trace_id,
            send_real_alert=request.send_real_alert,
            dry_run=request.dry_run,
            error_code=error_code,
            error_message=error_message,
        )
    return _base_result(
        status=StrategyAdviceNotificationStatus.FAILED,
        exit_code=EXIT_FAILED,
        request=request,
        rendered=rendered,
        alert_message_id=None,
        alert_status=None,
        event_type=None,
        hermes_status="not_attempted",
        error_code=error_code,
        error_message=error_message,
    )


def _base_result(
    *,
    status: StrategyAdviceNotificationStatus,
    exit_code: int,
    request: StrategyAdviceNotificationRequest,
    rendered: RenderedStrategyAdviceNotification,
    alert_message_id: int | None,
    alert_status: str | None,
    event_type: str | None,
    hermes_status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> StrategyAdviceNotificationResult:
    return StrategyAdviceNotificationResult(
        status=status,
        exit_code=exit_code,
        review_id=request.review_id,
        trace_id=request.trace_id,
        related_type=rendered.related_type,
        related_id=rendered.related_id,
        notification_level=rendered.notification_level,
        title=rendered.title,
        message_preview=_message_preview(rendered.message),
        alert_message_id=alert_message_id,
        alert_status=alert_status,
        event_type=event_type,
        send_real_alert=request.send_real_alert,
        hermes_status=hermes_status,
        dry_run=request.dry_run,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        error_code=error_code,
        error_message=error_message,
        details={"stage21b_calls_model": False, "stage21b_connects_scheduler": False},
    )


def _message_preview(message: str) -> str:
    text = " ".join(str(message).split())
    if len(text) <= 240:
        return text
    return f"{text[:225]}...[truncated]"


def _event_advice_id(rendered: RenderedStrategyAdviceNotification) -> str | None:
    if rendered.related_type == "strategy_advice":
        return rendered.related_id
    return None


def _alert_message_id(alert_message: Any) -> int | None:
    value = getattr(alert_message, "id", None)
    return int(value) if value is not None else None


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = [
    "ALLOWED_STRATEGY_ADVICE_NOTIFICATION_TRIGGER_SOURCES",
    "StrategyAdviceNotificationSender",
    "create_default_strategy_advice_notification_sender",
    "send_strategy_advice_notification",
]
