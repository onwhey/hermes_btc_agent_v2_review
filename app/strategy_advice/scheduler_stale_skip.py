"""Stale MRAG audit helper for stage-21C strategy advice scheduler.

This file belongs to `app/strategy_advice`. It creates the required
`skip_stale_review_aggregation` lifecycle review and audit event for old MRAG
rows. It never creates advice, trade setups, notifications, Hermes sends, model
calls, or trading actions.

Called by `app/strategy_advice/scheduler_service.py`. MySQL: writes only
lifecycle review and event through the injected repository. Redis: none.
Hermes: none. Model providers: none. Trading execution: none.
"""

from __future__ import annotations

from typing import Any

from app.strategy_advice.id_utils import build_strategy_advice_event_id, build_strategy_advice_review_id
from app.strategy_advice.scheduler_result_utils import (
    build_scheduler_result,
    commit_if_possible,
    optional_str,
    request_with_scope_from_mrag,
)
from app.strategy_advice.scheduler_schema import (
    EXIT_SUCCESS,
    StrategyAdviceSchedulerRequest,
    StrategyAdviceSchedulerResult,
    StrategyAdviceSchedulerStatus,
)
from app.strategy_advice.schema import (
    AdviceEventType,
    LifecycleAction,
    StrategyAdviceEventPersistencePayload,
    StrategyAdviceLifecycleReviewPersistencePayload,
)


def write_or_preview_stale_review_aggregation_skip(
    *,
    db_session: Any,
    settings: Any,
    repository: Any,
    request: StrategyAdviceSchedulerRequest,
    stale_mrag: Any,
    latest_mrag: Any,
    lock_key: str,
) -> StrategyAdviceSchedulerResult:
    """Mark an old MRAG as processed without advice or notification."""

    review_aggregation_run_id = str(getattr(stale_mrag, "review_aggregation_run_id", "") or "")
    latest_review_aggregation_run_id = str(getattr(latest_mrag, "review_aggregation_run_id", "") or "")
    review_id = build_strategy_advice_review_id(
        review_aggregation_run_id=review_aggregation_run_id,
        trace_id=request.trace_id,
    )
    scoped_request = request_with_scope_from_mrag(request, stale_mrag)
    if not request.confirm_write:
        return build_scheduler_result(
            settings=settings,
            request=scoped_request,
            status=StrategyAdviceSchedulerStatus.SUCCESS,
            exit_code=EXIT_SUCCESS,
            review_aggregation_run_id=review_aggregation_run_id,
            lifecycle_review_id=review_id,
            stale_skipped_count=1,
            lock_key=lock_key,
            summary_text="Dry-run would mark stale MRAG as processed without advice or notification.",
            details={"superseded_by_review_aggregation_run_id": latest_review_aggregation_run_id},
        )

    repository.create_lifecycle_review(
        db_session,
        payload=StrategyAdviceLifecycleReviewPersistencePayload(
            review_id=review_id,
            symbol=str(getattr(stale_mrag, "symbol", "") or ""),
            base_interval=str(getattr(stale_mrag, "base_interval", "") or ""),
            higher_interval=str(getattr(stale_mrag, "higher_interval", "") or ""),
            reviewed_advice_id=None,
            result_advice_id=None,
            previous_advice_id=None,
            lifecycle_action=LifecycleAction.SKIP_STALE_REVIEW_AGGREGATION,
            lifecycle_reason="旧 MRAG 已被更新 MRAG 覆盖，跳过生成建议",
            source_review_aggregation_run_id=review_aggregation_run_id,
            source_material_pack_id=str(getattr(stale_mrag, "material_pack_id", "") or ""),
            source_strategy_signal_run_id=optional_str(getattr(stale_mrag, "strategy_signal_run_id", None)),
            source_snapshot_id=optional_str(getattr(stale_mrag, "snapshot_id", None)),
            model_review_invoked=bool(getattr(stale_mrag, "model_review_invoked", False)),
            model_review_invocation_mode=str(getattr(stale_mrag, "model_review_invocation_mode", "") or "none"),
            model_review_reused=bool(getattr(stale_mrag, "model_review_reused", False)),
            reused_model_analysis_run_id=optional_str(getattr(stale_mrag, "reused_model_analysis_run_id", None)),
            model_review_basis=str(getattr(stale_mrag, "model_review_basis", "") or "unknown"),
            model_review_expired=bool(getattr(stale_mrag, "model_review_expired", False)),
            model_review_chain_status=str(getattr(stale_mrag, "model_review_chain_status", "") or "not_started"),
            notification_required=False,
            notification_level="brief",
            notification_reason="stale MRAG skipped, no user notification",
            notification_payload_json={
                "schema_version": "strategy_advice_scheduler_stale_skip_v1",
                "stale_review_aggregation_run_id": review_aggregation_run_id,
                "superseded_by_review_aggregation_run_id": latest_review_aggregation_run_id,
                "notification_required": False,
                "boundaries": {
                    "is_trading_signal": False,
                    "is_executable": False,
                    "auto_trading_allowed": False,
                    "stage21c_calls_model": False,
                    "stage21c_sends_hermes": False,
                },
            },
        ),
    )
    repository.create_strategy_advice_event(
        db_session,
        payload=StrategyAdviceEventPersistencePayload(
            event_id=build_strategy_advice_event_id(
                review_id=review_id,
                event_type=AdviceEventType.STALE_REVIEW_AGGREGATION_SKIPPED.value,
                sequence_no=1,
            ),
            advice_id=None,
            related_review_id=review_id,
            event_type=AdviceEventType.STALE_REVIEW_AGGREGATION_SKIPPED,
            event_reason="stale MRAG skipped by 21C scheduler",
            event_payload_json={
                "stale_review_aggregation_run_id": review_aggregation_run_id,
                "superseded_by_review_aggregation_run_id": latest_review_aggregation_run_id,
                "notification_required": False,
                "is_trading_signal": False,
                "is_executable": False,
                "auto_trading_allowed": False,
            },
        ),
    )
    commit_if_possible(db_session)
    return build_scheduler_result(
        settings=settings,
        request=scoped_request,
        status=StrategyAdviceSchedulerStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        review_aggregation_run_id=review_aggregation_run_id,
        lifecycle_review_id=review_id,
        processed_mrag_count=1,
        stale_skipped_count=1,
        lock_key=lock_key,
        summary_text="Stale MRAG was marked as processed without advice or Hermes notification.",
        details={"superseded_by_review_aggregation_run_id": latest_review_aggregation_run_id},
    )


__all__ = ["write_or_preview_stale_review_aggregation_skip"]
