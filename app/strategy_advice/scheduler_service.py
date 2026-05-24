"""Stage-21C strategy advice scheduler orchestration service.

This file links completed stage-20 MRAG rows into the existing 21A lifecycle
service and existing 21B notification sender. It does not implement advice
decisions, render notification Chinese, call stage 19, call model providers,
read material packs directly, modify Klines, read private trading state, or
execute trading.
"""
from __future__ import annotations

from typing import Any

from app.core.config import AppSettings, get_settings
from app.core.exceptions import RedisError
from app.core.time_utils import now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER
from app.strategy_advice.id_utils import build_strategy_advice_scheduler_event_id
from app.strategy_advice.notification_repository import create_default_strategy_advice_notification_repository
from app.strategy_advice.notification_sender import create_default_strategy_advice_notification_sender
from app.strategy_advice.scheduler_locks import (
    StrategyAdviceSchedulerLock,
    StrategyAdviceSchedulerLockManager,
    build_strategy_advice_21c_lock_key,
)
from app.strategy_advice.scheduler_notification import StrategyAdviceSchedulerNotificationCoordinator
from app.strategy_advice.scheduler_repository import create_default_strategy_advice_scheduler_repository
from app.strategy_advice.scheduler_result_utils import (
    build_scheduler_result, commit_if_possible, compact_object_details, merge_scope_scheduler_results,
    request_with_scope_from_mrag, rollback_if_possible, status_value,
)
from app.strategy_advice.scheduler_schema import (
    EXIT_FAILED, EXIT_PARAMETER_ERROR, EXIT_SUCCESS, STRATEGY_ADVICE_21C_LOCK_TTL_SECONDS,
    STRATEGY_ADVICE_SCHEDULER_JOB_NAME, StrategyAdviceSchedulerRequest, StrategyAdviceSchedulerResult, StrategyAdviceSchedulerStatus,
)
from app.strategy_advice.scheduler_stale_skip import write_or_preview_stale_review_aggregation_skip
from app.strategy_advice.schema import StrategyAdviceRequest, StrategyAdviceServiceStatus
from app.strategy_advice.service import create_default_strategy_advice_service

ALLOWED_STRATEGY_ADVICE_SCHEDULER_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER})
PROCESSABLE_REVIEW_AGGREGATION_STATUSES = frozenset({"success", "blocked"})

class StrategyAdviceSchedulerService:
    """Coordinate stage 21C without reimplementing 21A or 21B."""

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        repository: Any | None = None,
        advice_service: Any | None = None,
        notification_sender: Any | None = None,
        notification_repository: Any | None = None,
        lock_manager: StrategyAdviceSchedulerLockManager | Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repository = repository or create_default_strategy_advice_scheduler_repository()
        self._advice_service = advice_service or create_default_strategy_advice_service()
        self._notification_sender = notification_sender or create_default_strategy_advice_notification_sender()
        self._notification_repository = notification_repository or create_default_strategy_advice_notification_repository()
        self._lock_manager = lock_manager or StrategyAdviceSchedulerLockManager()
        self._notification_coordinator = StrategyAdviceSchedulerNotificationCoordinator(
            settings=self._settings,
            scheduler_repository=self._repository,
            notification_repository=self._notification_repository,
            notification_sender=self._notification_sender,
        )
        self._event_sequence = 0

    def run_strategy_advice_scheduler(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
    ) -> StrategyAdviceSchedulerResult:
        """Run one 21C orchestration pass for explicit MRAG or one scope scan."""

        invalid = self._validate_scheduler_request(request)
        if invalid is not None:
            return invalid
        if request.trigger_source == TRIGGER_SOURCE_SCHEDULER and not self._settings.strategy_advice_scheduler_enabled:
            result = self._build_result(
                request=request,
                status=StrategyAdviceSchedulerStatus.DISABLED,
                exit_code=EXIT_SUCCESS,
                summary_text="STRATEGY_ADVICE_SCHEDULER_ENABLED=false; 21C scheduler auto-run skipped.",
                error_code="strategy_advice_scheduler_disabled",
            )
            self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
            return result
        try:
            if request.review_aggregation_run_id:
                return self._run_for_explicit_review_aggregation(db_session, request=request)
            return self._scan_scope_and_run(db_session, request=request)
        except Exception as exc:  # noqa: BLE001 - service converts DB/service failures.
            rollback_if_possible(db_session)
            result = self._build_result(
                request=request,
                status=StrategyAdviceSchedulerStatus.FAILED,
                exit_code=EXIT_FAILED,
                summary_text="Stage 21C failed before completing advice scheduler orchestration.",
                error_code="strategy_advice_scheduler_failed",
                error_message=str(exc),
            )
            self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
            return result

    def _validate_scheduler_request(
        self,
        request: StrategyAdviceSchedulerRequest,
    ) -> StrategyAdviceSchedulerResult | None:
        problems: list[str] = []
        if request.trigger_source not in ALLOWED_STRATEGY_ADVICE_SCHEDULER_TRIGGER_SOURCES:
            problems.append("trigger_source supports only cli or scheduler for stage 21C")
        if request.dry_run and request.confirm_write:
            problems.append("dry_run and confirm_write cannot both be true")
        if not request.dry_run and not request.confirm_write:
            problems.append("non-dry-run 21C requires confirm_write")
        if request.limit <= 0:
            problems.append("limit must be greater than 0")
        if not request.review_aggregation_run_id and (
            not request.symbol.strip() or not request.base_interval.strip() or not request.higher_interval.strip()
        ):
            problems.append("symbol, base_interval, and higher_interval are required when MRAG id is not supplied")
        if not problems:
            return None
        return self._build_result(
            request=request,
            status=StrategyAdviceSchedulerStatus.FAILED,
            exit_code=EXIT_PARAMETER_ERROR,
            summary_text="Invalid stage 21C scheduler request.",
            error_code="invalid_request",
            error_message="; ".join(problems),
        )

    def _run_for_explicit_review_aggregation(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
    ) -> StrategyAdviceSchedulerResult:
        mrag = self._repository.get_review_aggregation_by_id(
            db_session,
            review_aggregation_run_id=str(request.review_aggregation_run_id or ""),
        )
        if mrag is None:
            result = self._build_result(
                request=request,
                status=StrategyAdviceSchedulerStatus.SKIPPED,
                exit_code=EXIT_SUCCESS,
                summary_text="MRAG not found; 21C skipped without creating advice.",
                error_code="review_aggregation_not_found",
            )
            self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
            return result
        latest = self._repository.get_latest_review_aggregation_for_scope(
            db_session,
            symbol=str(getattr(mrag, "symbol", "") or ""),
            base_interval=str(getattr(mrag, "base_interval", "") or ""),
            higher_interval=str(getattr(mrag, "higher_interval", "") or ""),
        )
        return self._process_one_review_aggregation(
            db_session,
            request=request_with_scope_from_mrag(request, mrag),
            mrag=mrag,
            latest_mrag=latest,
        )

    def _scan_scope_and_run(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
    ) -> StrategyAdviceSchedulerResult:
        mrags = self._repository.list_unprocessed_review_aggregations(
            db_session,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            limit=request.limit,
        )
        if not mrags:
            recovery = self._recover_one_pending_notification(db_session, request=request)
            if recovery is not None:
                return recovery
            result = self._build_result(
                request=request,
                status=StrategyAdviceSchedulerStatus.SKIPPED,
                exit_code=EXIT_SUCCESS,
                summary_text="No unprocessed MRAG or recoverable 21B notification was found.",
                error_code="no_pending_review_aggregation",
            )
            self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
            return result
        latest = self._repository.get_latest_review_aggregation_for_scope(
            db_session,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
        )
        results = [
            self._process_one_review_aggregation(db_session, request=request, mrag=mrag, latest_mrag=latest)
            for mrag in mrags
        ]
        return merge_scope_scheduler_results(
            settings=self._settings,
            request=request,
            results=results,
            success_status=StrategyAdviceSchedulerStatus.SUCCESS,
            failed_status=StrategyAdviceSchedulerStatus.FAILED,
            exit_success=EXIT_SUCCESS,
            exit_failed=EXIT_FAILED,
        )

    def _process_one_review_aggregation(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
        mrag: Any,
        latest_mrag: Any | None,
    ) -> StrategyAdviceSchedulerResult:
        review_aggregation_run_id = str(getattr(mrag, "review_aggregation_run_id", "") or "")
        lock = self._build_lock(request=request, mrag=mrag)
        if request.confirm_write:
            lock_result = self._acquire_lock_or_result(db_session, request=request, lock=lock, mrag=mrag)
            if lock_result is not None:
                return lock_result
        try:
            return self._process_locked_review_aggregation(
                db_session, request=request, mrag=mrag, latest_mrag=latest_mrag, lock=lock,
                review_aggregation_run_id=review_aggregation_run_id,
            )
        finally:
            if request.confirm_write:
                self._release_lock_safely(lock)

    def _process_locked_review_aggregation(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
        mrag: Any,
        latest_mrag: Any | None,
        lock: StrategyAdviceSchedulerLock,
        review_aggregation_run_id: str,
    ) -> StrategyAdviceSchedulerResult:
        existing_review = self._repository.get_lifecycle_review_by_source_review_aggregation(
            db_session,
            review_aggregation_run_id=review_aggregation_run_id,
        )
        if existing_review is not None:
            return self._recover_notification_for_review(db_session, request=request, review_row=existing_review)
        if latest_mrag is not None and review_aggregation_run_id != str(getattr(latest_mrag, "review_aggregation_run_id", "") or ""):
            result = write_or_preview_stale_review_aggregation_skip(
                db_session=db_session,
                settings=self._settings,
                repository=self._repository,
                request=request,
                stale_mrag=mrag,
                latest_mrag=latest_mrag,
                lock_key=lock.key,
            )
            self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
            return result
        if str(getattr(mrag, "status", "") or "") not in PROCESSABLE_REVIEW_AGGREGATION_STATUSES:
            result = self._build_result(
                request=request_with_scope_from_mrag(request, mrag),
                status=StrategyAdviceSchedulerStatus.SKIPPED,
                exit_code=EXIT_SUCCESS,
                review_aggregation_run_id=review_aggregation_run_id,
                lock_key=lock.key,
                summary_text="Latest MRAG status is not processable by 21C; lifecycle advice was not generated.",
                error_code="review_aggregation_status_not_processable",
                details={"review_aggregation_status": str(getattr(mrag, "status", "") or "")},
            )
            self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
            return result
        return self._run_21a_then_21b_for_current_mrag(
            db_session, request=request_with_scope_from_mrag(request, mrag), mrag=mrag, lock_key=lock.key,
        )

    def _run_21a_then_21b_for_current_mrag(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
        mrag: Any,
        lock_key: str,
    ) -> StrategyAdviceSchedulerResult:
        review_aggregation_run_id = str(getattr(mrag, "review_aggregation_run_id", "") or "")
        advice_result = self._advice_service.run_strategy_advice(
            db_session,
            request=StrategyAdviceRequest(
                review_aggregation_run_id=review_aggregation_run_id,
                trigger_source=request.trigger_source,
                dry_run=request.dry_run,
                confirm_write=request.confirm_write,
                created_by=request.created_by,
                trace_id=request.trace_id,
            ),
        )
        if request.dry_run:
            return self._build_result(
                request=request,
                status=StrategyAdviceSchedulerStatus.SUCCESS,
                exit_code=EXIT_SUCCESS,
                review_aggregation_run_id=review_aggregation_run_id,
                lifecycle_review_id=getattr(advice_result, "review_id", None),
                advice_result_status=status_value(getattr(advice_result, "status", "")),
                lock_key=lock_key,
                summary_text="Dry-run previewed current MRAG through 21A; no lifecycle row or notification was written.",
                details={"stage21a_dry_run": compact_object_details(advice_result)},
            )
        if getattr(advice_result, "status", None) != StrategyAdviceServiceStatus.SUCCESS:
            result = self._build_result(
                request=request,
                status=StrategyAdviceSchedulerStatus.FAILED,
                exit_code=EXIT_FAILED,
                review_aggregation_run_id=review_aggregation_run_id,
                lifecycle_review_id=getattr(advice_result, "review_id", None),
                advice_result_status=status_value(getattr(advice_result, "status", "")),
                lock_key=lock_key,
                summary_text="21A failed; 21B notification was not attempted.",
                error_code=getattr(advice_result, "error_code", None) or "stage21a_failed",
                error_message=getattr(advice_result, "error_message", None),
                details={"stage21a_result": compact_object_details(advice_result)},
            )
            self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
            return result
        review_row = self._repository.get_lifecycle_review_by_id(
            db_session,
            review_id=str(getattr(advice_result, "review_id", "") or ""),
        )
        notification_result = self._notification_coordinator.send_notification_if_needed(
            db_session,
            request=request,
            review_row=review_row,
        )
        status = StrategyAdviceSchedulerStatus.SUCCESS
        exit_code = EXIT_SUCCESS
        if notification_result is not None and status_value(getattr(notification_result, "status", "")) == "failed":
            status = StrategyAdviceSchedulerStatus.FAILED
            exit_code = EXIT_FAILED
        result = self._build_result(
            request=request,
            status=status,
            exit_code=exit_code,
            review_aggregation_run_id=review_aggregation_run_id,
            lifecycle_review_id=str(getattr(advice_result, "review_id", "") or ""),
            advice_result_status=status_value(getattr(advice_result, "status", "")),
            notification_attempted=notification_result is not None,
            notification_status=status_value(getattr(notification_result, "status", "")) if notification_result else None,
            send_real_alert=self._settings.strategy_advice_notification_send_enabled,
            processed_mrag_count=1,
            lock_key=lock_key,
            summary_text="21C processed current MRAG through 21A and attempted 21B when required.",
            error_code=getattr(notification_result, "error_code", None) if status == StrategyAdviceSchedulerStatus.FAILED else None,
            error_message=getattr(notification_result, "error_message", None) if status == StrategyAdviceSchedulerStatus.FAILED else None,
            details={
                "stage21a_result": compact_object_details(advice_result),
                "stage21b_result": compact_object_details(notification_result) if notification_result else {},
            },
        )
        self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
        return result

    def _recover_one_pending_notification(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
    ) -> StrategyAdviceSchedulerResult | None:
        reviews = self._repository.list_notification_recovery_reviews(
            db_session,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            limit=request.limit,
        )
        for review_row in reviews:
            result = self._recover_notification_for_review(db_session, request=request, review_row=review_row)
            if result.notification_attempted or result.error_code in {
                "notification_retry_waiting",
                "notification_retry_limit_reached",
            }:
                return result
        return None

    def _recover_notification_for_review(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
        review_row: Any,
    ) -> StrategyAdviceSchedulerResult:
        result = self._notification_coordinator.recover_notification_for_review(
            db_session,
            request=request,
            review_row=review_row,
        )
        self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
        return result

    def _build_lock(self, *, request: StrategyAdviceSchedulerRequest, mrag: Any) -> StrategyAdviceSchedulerLock:
        lock_key = build_strategy_advice_21c_lock_key(
            symbol=str(getattr(mrag, "symbol", "") or request.symbol),
            base_interval=str(getattr(mrag, "base_interval", "") or request.base_interval),
            higher_interval=str(getattr(mrag, "higher_interval", "") or request.higher_interval),
            review_aggregation_run_id=str(getattr(mrag, "review_aggregation_run_id", "") or ""),
        )
        return StrategyAdviceSchedulerLock(
            key=lock_key,
            owner=f"stage21c:{request.trace_id}",
            ttl_seconds=STRATEGY_ADVICE_21C_LOCK_TTL_SECONDS,
        )

    def _acquire_lock_or_result(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
        lock: StrategyAdviceSchedulerLock,
        mrag: Any,
    ) -> StrategyAdviceSchedulerResult | None:
        try:
            acquired = self._lock_manager.acquire_strategy_advice_lock(lock=lock)
        except RedisError as exc:
            result = self._build_result(
                request=request_with_scope_from_mrag(request, mrag),
                status=StrategyAdviceSchedulerStatus.LOCK_SKIPPED,
                exit_code=EXIT_SUCCESS,
                review_aggregation_run_id=str(getattr(mrag, "review_aggregation_run_id", "") or ""),
                lock_key=lock.key,
                summary_text="21C could not acquire Redis lock; MRAG was skipped for a later retry.",
                error_code="redis_lock_unavailable",
                error_message=str(exc),
            )
            self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
            return result
        if acquired:
            return None
        result = self._build_result(
            request=request_with_scope_from_mrag(request, mrag),
            status=StrategyAdviceSchedulerStatus.LOCK_SKIPPED,
            exit_code=EXIT_SUCCESS,
            review_aggregation_run_id=str(getattr(mrag, "review_aggregation_run_id", "") or ""),
            lock_key=lock.key,
            summary_text="Another 21C worker owns the MRAG Redis lock; skipped without writes.",
            error_code="lock_already_held",
        )
        self._write_scheduler_log_if_confirmed(db_session, request=request, result=result)
        return result

    def _release_lock_safely(self, lock: StrategyAdviceSchedulerLock) -> None:
        try:
            self._lock_manager.release_strategy_advice_lock(lock=lock)
        except RedisError:
            pass

    def _write_scheduler_log_if_confirmed(
        self,
        db_session: Any,
        *,
        request: StrategyAdviceSchedulerRequest,
        result: StrategyAdviceSchedulerResult,
    ) -> None:
        if not request.confirm_write:
            return
        started_at_utc = now_utc()
        self._event_sequence += 1
        try:
            self._repository.create_scheduler_event_log(
                db_session,
                event_id=build_strategy_advice_scheduler_event_id(trace_id=request.trace_id, sequence_no=self._event_sequence),
                job_name=STRATEGY_ADVICE_SCHEDULER_JOB_NAME,
                symbol=result.symbol,
                base_interval=result.base_interval,
                higher_interval=result.higher_interval,
                review_aggregation_run_id=result.review_aggregation_run_id,
                trigger_source=request.trigger_source,
                status=result.status.value,
                reason=result.summary_text or result.error_code or "",
                trace_id=request.trace_id,
                started_at_utc=started_at_utc,
                finished_at_utc=now_utc(),
                details={
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                    "lifecycle_review_id": result.lifecycle_review_id,
                    "notification_status": result.notification_status,
                    "send_real_alert": result.send_real_alert,
                    **dict(result.details),
                },
            )
            commit_if_possible(db_session)
        except Exception:
            rollback_if_possible(db_session)

    def _build_result(self, **kwargs: Any) -> StrategyAdviceSchedulerResult:
        return build_scheduler_result(settings=self._settings, **kwargs)


def run_strategy_advice_scheduler(
    *,
    db_session: Any,
    request: StrategyAdviceSchedulerRequest,
    service: StrategyAdviceSchedulerService | None = None,
) -> StrategyAdviceSchedulerResult:
    """Convenience app-service function used by CLI, scheduler job, and tests."""

    active_service = service or create_default_strategy_advice_scheduler_service()
    return active_service.run_strategy_advice_scheduler(db_session, request=request)


def create_default_strategy_advice_scheduler_service() -> StrategyAdviceSchedulerService:
    """Create the default stage-21C scheduler orchestration service."""

    return StrategyAdviceSchedulerService()


__all__ = [
    "PROCESSABLE_REVIEW_AGGREGATION_STATUSES",
    "StrategyAdviceSchedulerService",
    "create_default_strategy_advice_scheduler_service",
    "run_strategy_advice_scheduler",
]
