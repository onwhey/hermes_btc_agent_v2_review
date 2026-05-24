"""Result and small value helpers for stage-21C scheduler orchestration.

This file belongs to `app/strategy_advice`. It centralizes compact result
construction and scope-copy helpers shared by the 21C scheduler service,
notification recovery helper, and stale-skip helper.

External services: none. MySQL: none. Redis: none. Hermes: none. Model
providers: none. Trading execution: none.
"""

from __future__ import annotations

from typing import Any

from app.strategy_advice.scheduler_schema import StrategyAdviceSchedulerRequest, StrategyAdviceSchedulerResult


def build_scheduler_result(
    *,
    settings: Any,
    request: StrategyAdviceSchedulerRequest,
    status: Any,
    exit_code: int,
    summary_text: str,
    review_aggregation_run_id: str | None = None,
    lifecycle_review_id: str | None = None,
    advice_result_status: str | None = None,
    notification_attempted: bool = False,
    notification_status: str | None = None,
    send_real_alert: bool = False,
    processed_mrag_count: int = 0,
    stale_skipped_count: int = 0,
    lock_key: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    details: dict[str, Any] | None = None,
) -> StrategyAdviceSchedulerResult:
    """Build a compact stage-21C result with config visibility."""

    return StrategyAdviceSchedulerResult(
        status=status,
        exit_code=exit_code,
        trace_id=request.trace_id,
        trigger_source=request.trigger_source,
        review_aggregation_run_id=review_aggregation_run_id or request.review_aggregation_run_id,
        symbol=request.symbol,
        base_interval=request.base_interval,
        higher_interval=request.higher_interval,
        scheduler_enabled=bool(getattr(settings, "strategy_advice_scheduler_enabled", False)),
        notification_send_enabled=bool(getattr(settings, "strategy_advice_notification_send_enabled", False)),
        processed_mrag_count=processed_mrag_count,
        stale_skipped_count=stale_skipped_count,
        lifecycle_review_id=lifecycle_review_id,
        advice_result_status=advice_result_status,
        notification_attempted=notification_attempted,
        notification_status=notification_status,
        send_real_alert=send_real_alert,
        lock_key=lock_key,
        dry_run=request.dry_run,
        error_code=error_code,
        error_message=error_message,
        summary_text=summary_text,
        details=dict(details or {}),
    )


def request_with_scope_from_mrag(request: StrategyAdviceSchedulerRequest, mrag: Any) -> StrategyAdviceSchedulerRequest:
    """Copy request mode and trace while taking scope fields from one MRAG."""

    return StrategyAdviceSchedulerRequest(
        review_aggregation_run_id=str(getattr(mrag, "review_aggregation_run_id", "") or request.review_aggregation_run_id or ""),
        symbol=str(getattr(mrag, "symbol", "") or request.symbol),
        base_interval=str(getattr(mrag, "base_interval", "") or request.base_interval),
        higher_interval=str(getattr(mrag, "higher_interval", "") or request.higher_interval),
        trigger_source=request.trigger_source,
        dry_run=request.dry_run,
        confirm_write=request.confirm_write,
        created_by=request.created_by,
        trace_id=request.trace_id,
        limit=request.limit,
    )


def request_with_scope_from_review(
    request: StrategyAdviceSchedulerRequest,
    review_row: Any,
) -> StrategyAdviceSchedulerRequest:
    """Copy request mode and trace while taking scope fields from one review."""

    return StrategyAdviceSchedulerRequest(
        review_aggregation_run_id=str(getattr(review_row, "source_review_aggregation_run_id", "") or ""),
        symbol=str(getattr(review_row, "symbol", "") or request.symbol),
        base_interval=str(getattr(review_row, "base_interval", "") or request.base_interval),
        higher_interval=str(getattr(review_row, "higher_interval", "") or request.higher_interval),
        trigger_source=request.trigger_source,
        dry_run=request.dry_run,
        confirm_write=request.confirm_write,
        created_by=request.created_by,
        trace_id=request.trace_id,
        limit=request.limit,
    )


def compact_object_details(value: Any | None) -> dict[str, Any]:
    """Return a small debug map without dumping payloads or notification bodies."""

    if value is None:
        return {}
    return {
        "status": status_value(getattr(value, "status", "")),
        "review_id": getattr(value, "review_id", None),
        "review_aggregation_run_id": getattr(value, "review_aggregation_run_id", None),
        "advice_id": getattr(value, "advice_id", None),
        "lifecycle_action": status_value(getattr(value, "lifecycle_action", "")),
        "notification_required": getattr(value, "notification_required", None),
        "notification_level": getattr(value, "notification_level", None),
        "event_type": getattr(value, "event_type", None),
        "alert_status": getattr(value, "alert_status", None),
        "error_code": getattr(value, "error_code", None),
        "error_message": getattr(value, "error_message", None),
    }


def status_value(value: Any) -> str:
    """Return enum value or plain text for stable details."""

    return str(getattr(value, "value", value) or "")


def optional_str(value: Any) -> str | None:
    """Return stripped text or None for nullable foreign references."""

    text = str(value or "").strip()
    return text or None


def merge_scope_scheduler_results(
    *,
    settings: Any,
    request: StrategyAdviceSchedulerRequest,
    results: list[StrategyAdviceSchedulerResult],
    success_status: Any,
    failed_status: Any,
    exit_success: int,
    exit_failed: int,
) -> StrategyAdviceSchedulerResult:
    """Merge per-MRAG results into one compact scope-scan result."""

    first_failed = next((item for item in results if item.status == failed_status), None)
    latest_current = next((item for item in results if item.stale_skipped_count == 0), results[0])
    merged_status = success_status if first_failed is None else failed_status
    if first_failed is None and latest_current.status != success_status:
        merged_status = latest_current.status
    return build_scheduler_result(
        settings=settings,
        request=request,
        status=merged_status,
        exit_code=exit_success if first_failed is None else exit_failed,
        review_aggregation_run_id=latest_current.review_aggregation_run_id,
        lifecycle_review_id=latest_current.lifecycle_review_id,
        advice_result_status=latest_current.advice_result_status,
        notification_attempted=latest_current.notification_attempted,
        notification_status=latest_current.notification_status,
        send_real_alert=latest_current.send_real_alert,
        processed_mrag_count=sum(item.processed_mrag_count for item in results),
        stale_skipped_count=sum(item.stale_skipped_count for item in results),
        summary_text="21C scope scan completed for latest MRAG and stale MRAG audit rows.",
        error_code=latest_current.error_code if merged_status != success_status else None,
        error_message=latest_current.error_message if merged_status != success_status else None,
        details={"results": [dict(item.details) for item in results]},
    )


def commit_if_possible(db_session: Any) -> None:
    """Commit caller-owned session only when it exposes commit()."""

    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def rollback_if_possible(db_session: Any) -> None:
    """Rollback caller-owned session only when it exposes rollback()."""

    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = [
    "build_scheduler_result",
    "commit_if_possible",
    "compact_object_details",
    "merge_scope_scheduler_results",
    "optional_str",
    "request_with_scope_from_mrag",
    "request_with_scope_from_review",
    "rollback_if_possible",
    "status_value",
]
