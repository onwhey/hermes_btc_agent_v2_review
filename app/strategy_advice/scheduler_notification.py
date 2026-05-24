"""Notification recovery helper for stage-21C strategy advice scheduler.

This file belongs to `app/strategy_advice`. It contains only 21B recovery and
retry coordination for lifecycle reviews that already exist. It never creates
strategy_advice rows, never reruns 21A decisions, and never changes active
advice state.

External services: Hermes only through the existing 21B sender when the caller
and env allow it. MySQL: reads/writes through injected repositories/sender.
Redis: none. Model providers: none. Trading execution: none.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.core.time_utils import now_utc
from app.strategy_advice.notification_schema import StrategyAdviceNotificationRequest
from app.strategy_advice.scheduler_result_utils import (
    build_scheduler_result,
    compact_object_details,
    request_with_scope_from_review,
    status_value,
)
from app.strategy_advice.scheduler_schema import (
    EXIT_FAILED,
    EXIT_SUCCESS,
    STRATEGY_ADVICE_NOTIFICATION_MAX_RETRY_COUNT,
    STRATEGY_ADVICE_NOTIFICATION_RETRY_DELAY_SECONDS,
    StrategyAdviceSchedulerRequest,
    StrategyAdviceSchedulerResult,
    StrategyAdviceSchedulerStatus,
)


class StrategyAdviceSchedulerNotificationCoordinator:
    """Coordinate 21B idempotency and retry from stage 21C."""

    def __init__(
        self,
        *,
        settings: Any,
        scheduler_repository: Any,
        notification_repository: Any,
        notification_sender: Any,
    ) -> None:
        self._settings = settings
        self._scheduler_repository = scheduler_repository
        self._notification_repository = notification_repository
        self._notification_sender = notification_sender

    def recover_notification_for_review(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
        review_row: Any,
    ) -> StrategyAdviceSchedulerResult:
        """Run only the 21B path for an existing lifecycle review if needed."""

        review_id = str(getattr(review_row, "review_id", "") or "")
        review_aggregation_run_id = str(getattr(review_row, "source_review_aggregation_run_id", "") or "")
        scoped_request = request_with_scope_from_review(request, review_row)
        if not bool(getattr(review_row, "notification_required", False)):
            return build_scheduler_result(
                settings=self._settings,
                request=scoped_request,
                status=StrategyAdviceSchedulerStatus.SKIPPED,
                exit_code=EXIT_SUCCESS,
                summary_text="Existing lifecycle review does not require notification.",
                review_aggregation_run_id=review_aggregation_run_id,
                lifecycle_review_id=review_id,
                error_code="notification_not_required",
            )
        if not str(getattr(review_row, "notification_payload_json", "") or "").strip():
            return build_scheduler_result(
                settings=self._settings,
                request=scoped_request,
                status=StrategyAdviceSchedulerStatus.SKIPPED,
                exit_code=EXIT_SUCCESS,
                summary_text="Existing lifecycle review has empty notification payload; 21B recovery skipped.",
                review_aggregation_run_id=review_aggregation_run_id,
                lifecycle_review_id=review_id,
                error_code="notification_payload_empty",
            )
        already_sent = self._already_successfully_notified(db_session, review_id=review_id)
        if already_sent:
            return build_scheduler_result(
                settings=self._settings,
                request=scoped_request,
                status=StrategyAdviceSchedulerStatus.SKIPPED,
                exit_code=EXIT_SUCCESS,
                summary_text=already_sent,
                review_aggregation_run_id=review_aggregation_run_id,
                lifecycle_review_id=review_id,
                error_code="notification_already_sent",
            )
        retry_skip = self._notification_retry_skip_result(db_session, request=scoped_request, review_row=review_row)
        if retry_skip is not None:
            return retry_skip
        notification_result = self.send_notification_if_needed(
            db_session,
            request=scoped_request,
            review_row=review_row,
        )
        status = StrategyAdviceSchedulerStatus.SUCCESS
        exit_code = EXIT_SUCCESS
        if notification_result and status_value(getattr(notification_result, "status", "")) == "failed":
            status = StrategyAdviceSchedulerStatus.FAILED
            exit_code = EXIT_FAILED
        return build_scheduler_result(
            settings=self._settings,
            request=scoped_request,
            status=status,
            exit_code=exit_code,
            review_aggregation_run_id=review_aggregation_run_id,
            lifecycle_review_id=review_id,
            notification_attempted=notification_result is not None,
            notification_status=status_value(getattr(notification_result, "status", "")) if notification_result else None,
            send_real_alert=bool(getattr(self._settings, "strategy_advice_notification_send_enabled", False)),
            summary_text="21A already existed; 21C recovered only the 21B notification path.",
            error_code=getattr(notification_result, "error_code", None)
            if status == StrategyAdviceSchedulerStatus.FAILED
            else None,
            error_message=getattr(notification_result, "error_message", None)
            if status == StrategyAdviceSchedulerStatus.FAILED
            else None,
            details={"stage21b_result": compact_object_details(notification_result) if notification_result else {}},
        )

    def send_notification_if_needed(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
        review_row: Any | None,
    ) -> Any | None:
        """Delegate to 21B without rendering or sending logic duplication."""

        if review_row is None or not bool(getattr(review_row, "notification_required", False)):
            return None
        if request.dry_run:
            return self._notification_sender.send_strategy_advice_notification(
                db_session,
                request=StrategyAdviceNotificationRequest(
                    review_id=str(getattr(review_row, "review_id", "") or ""),
                    trigger_source=request.trigger_source,
                    dry_run=True,
                    confirm_write=False,
                    send_real_alert=False,
                    trace_id=request.trace_id,
                ),
            )
        return self._notification_sender.send_strategy_advice_notification(
            db_session,
            request=StrategyAdviceNotificationRequest(
                review_id=str(getattr(review_row, "review_id", "") or ""),
                trigger_source=request.trigger_source,
                dry_run=False,
                confirm_write=True,
                send_real_alert=bool(getattr(self._settings, "strategy_advice_notification_send_enabled", False)),
                trace_id=request.trace_id,
            ),
        )

    def _already_successfully_notified(self, db_session: Any, *, review_id: str) -> str:
        if self._notification_repository.has_successful_notification_event(db_session, review_id=review_id):
            return "notification_sent event already exists for review_id; 21B not repeated."
        if self._notification_repository.has_successful_alert_message(db_session, review_id=review_id):
            return "successful alert_message already exists for review_id; 21B not repeated."
        return ""

    def _notification_retry_skip_result(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
        review_row: Any,
    ) -> StrategyAdviceSchedulerResult | None:
        review_id = str(getattr(review_row, "review_id", "") or "")
        failed_count = self._scheduler_repository.count_notification_failed_events(db_session, review_id=review_id)
        latest_failed_at = self._scheduler_repository.latest_notification_failed_at(db_session, review_id=review_id)
        if failed_count >= STRATEGY_ADVICE_NOTIFICATION_MAX_RETRY_COUNT:
            return build_scheduler_result(
                settings=self._settings,
                request=request,
                status=StrategyAdviceSchedulerStatus.SKIPPED,
                exit_code=EXIT_SUCCESS,
                review_aggregation_run_id=str(getattr(review_row, "source_review_aggregation_run_id", "") or ""),
                lifecycle_review_id=review_id,
                summary_text="21B notification retry limit reached; 21A state was not changed.",
                error_code="notification_retry_limit_reached",
                details={"notification_failed_count": failed_count},
            )
        if latest_failed_at is None:
            return None
        active_latest_failed_at = latest_failed_at
        if active_latest_failed_at.tzinfo is None:
            active_latest_failed_at = active_latest_failed_at.replace(tzinfo=now_utc().tzinfo)
        retry_after = active_latest_failed_at + timedelta(seconds=STRATEGY_ADVICE_NOTIFICATION_RETRY_DELAY_SECONDS)
        if now_utc() < retry_after:
            return build_scheduler_result(
                settings=self._settings,
                request=request,
                status=StrategyAdviceSchedulerStatus.SKIPPED,
                exit_code=EXIT_SUCCESS,
                review_aggregation_run_id=str(getattr(review_row, "source_review_aggregation_run_id", "") or ""),
                lifecycle_review_id=review_id,
                summary_text="21B notification retry is waiting for the 5 minute interval.",
                error_code="notification_retry_waiting",
                details={"retry_after_utc": retry_after.isoformat(), "notification_failed_count": failed_count},
            )
        return None


__all__ = ["StrategyAdviceSchedulerNotificationCoordinator"]
