"""Stage-21A strategy advice lifecycle service.

Call chain:
scripts/run_strategy_advice.py::main
    -> app/strategy_advice/service.py::run_strategy_advice
    -> app/strategy_advice/repository.py::get_review_aggregation_run_by_id
    -> app/strategy_advice/repository.py::get_active_strategy_advice
    -> app/strategy_advice/lifecycle.py::build_advice_candidate_from_aggregation
    -> app/strategy_advice/notification_payload.py::build_notification_payload
    -> app/strategy_advice/repository.py::create_strategy_advice
    -> app/strategy_advice/repository.py::create_lifecycle_review
    -> app/strategy_advice/repository.py::create_strategy_advice_event
    -> app/strategy_advice/repository.py::create_strategy_advice_trade_setup
       (confirm-write only)

This file belongs to `app/strategy_advice`. It consumes stage-20A aggregation
rows, creates or maintains bounded human strategy advice lifecycle state, and
prepares notification payload fields for future 21B delivery.

It does not call stage 19, does not call model providers, does not connect
scheduler jobs, does not send Hermes, does not generate executable trading
signals, does not read private trading state, does not modify formal Kline
tables, and does not perform trading.
"""

from __future__ import annotations

from typing import Any

from app.core.time_utils import now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER
from app.strategy_advice.id_utils import build_strategy_advice_id, build_strategy_advice_review_id
from app.strategy_advice.lifecycle import (
    AdviceCandidate,
    active_advice_semantic_signature,
    build_advice_candidate_from_aggregation,
    lifecycle_action_without_active,
    should_create_new_advice_without_active,
)
from app.strategy_advice.notification_payload import build_notification_payload
from app.strategy_advice.notification_payload import notification_level_for_lifecycle, notification_reason_for_lifecycle
from app.strategy_advice.payload_builder import (
    build_event_payloads,
    build_lifecycle_review_payload,
    build_strategy_advice_payload,
    event_type_for_terminal_status,
    text_attr,
)
from app.strategy_advice.plan import LifecyclePlan, replace_plan_status_update
from app.strategy_advice.repository import StrategyAdviceRepository, create_default_strategy_advice_repository
from app.strategy_advice.result_builder import failed_strategy_advice_result
from app.strategy_advice.result_builder import result_from_lifecycle_plan, validate_strategy_advice_request
from app.strategy_advice.schema import (
    EXIT_BLOCKED,
    AdviceEventType,
    AdviceStatus,
    LifecycleAction,
    StrategyAdviceRequest,
    StrategyAdviceResult,
    StrategyAdviceServiceStatus,
    StrategyAdviceTradeSetupPersistencePayload,
)
from app.strategy_advice.trade_setup import build_trade_setup_payloads

ALLOWED_STRATEGY_ADVICE_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER})


class StrategyAdviceService:
    """Coordinate one deterministic stage-21A advice lifecycle attempt.

    Parameters: repository is injectable for tests.
    Return value: service instance.
    Failure scenarios: invalid parameters, missing stage-20A aggregation rows,
    active advice lookup failures, lifecycle persistence failures, and malformed
    upstream summary fields are converted into structured results.
    External effects: dry-run reads only; confirm-write writes only stage-21A
    rows and commits if the caller session exposes `commit`.
    """

    def __init__(self, *, repository: StrategyAdviceRepository | Any | None = None) -> None:
        self._repository = repository or create_default_strategy_advice_repository()

    def run_strategy_advice(self, db_session: Any, *, request: StrategyAdviceRequest) -> StrategyAdviceResult:
        """Run one stage-21A lifecycle pass over an existing stage-20 result."""

        review_id = build_strategy_advice_review_id(
            review_aggregation_run_id=request.review_aggregation_run_id,
            trace_id=request.trace_id,
        )
        invalid = validate_strategy_advice_request(
            request=request,
            review_id=review_id,
            allowed_trigger_sources=ALLOWED_STRATEGY_ADVICE_TRIGGER_SOURCES,
        )
        if invalid is not None:
            return invalid
        try:
            aggregation_row = self._repository.get_review_aggregation_run_by_id(
                db_session,
                review_aggregation_run_id=request.review_aggregation_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - service converts database failures.
            _rollback_if_possible(db_session)
            return failed_strategy_advice_result(
                request=request,
                review_id=review_id,
                error_code="review_aggregation_lookup_failed",
                error_message=str(exc),
            )
        if aggregation_row is None:
            return StrategyAdviceResult(
                status=StrategyAdviceServiceStatus.BLOCKED,
                exit_code=EXIT_BLOCKED,
                review_id=review_id,
                review_aggregation_run_id=request.review_aggregation_run_id,
                trace_id=request.trace_id,
                dry_run=request.dry_run,
                notification_required=False,
                notification_level="none",
                notification_reason="stage-20 aggregation row not found",
                summary_text="Stage 21A did not write advice because the stage-20 aggregation row was not found.",
                error_code="review_aggregation_run_not_found",
                error_message="model_review_aggregation_run does not exist.",
            )

        try:
            active_advice = self._repository.get_active_strategy_advice(
                db_session,
                symbol=text_attr(aggregation_row, "symbol"),
                base_interval=text_attr(aggregation_row, "base_interval"),
                higher_interval=text_attr(aggregation_row, "higher_interval"),
            )
        except Exception as exc:  # noqa: BLE001 - service converts database failures.
            _rollback_if_possible(db_session)
            return failed_strategy_advice_result(
                request=request,
                review_id=review_id,
                aggregation_row=aggregation_row,
                error_code="active_advice_lookup_failed",
                error_message=str(exc),
            )

        candidate = build_advice_candidate_from_aggregation(aggregation_row)
        plan = self._build_lifecycle_plan(
            request=request,
            review_id=review_id,
            aggregation_row=aggregation_row,
            active_advice=active_advice,
            candidate=candidate,
        )
        if request.dry_run:
            return result_from_lifecycle_plan(request=request, plan=plan)
        try:
            if plan.status_update_row is not None and plan.status_update_to is not None:
                self._repository.update_strategy_advice_status(
                    db_session,
                    plan.status_update_row,
                    advice_status=plan.status_update_to.value,
                    closed_at_utc=now_utc(),
                )
            if plan.advice_payload is not None:
                self._repository.create_strategy_advice(db_session, payload=plan.advice_payload)
            self._repository.create_lifecycle_review(db_session, payload=plan.lifecycle_payload)
            for event_payload in plan.event_payloads:
                self._repository.create_strategy_advice_event(db_session, payload=event_payload)
            for setup_payload in plan.trade_setup_payloads:
                self._repository.create_strategy_advice_trade_setup(db_session, payload=setup_payload)
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001 - persistence failure is reported to caller.
            _rollback_if_possible(db_session)
            return failed_strategy_advice_result(
                request=request,
                review_id=review_id,
                aggregation_row=aggregation_row,
                error_code="strategy_advice_persistence_failed",
                error_message=str(exc),
            )
        return result_from_lifecycle_plan(request=request, plan=plan)

    def _build_lifecycle_plan(
        self,
        *,
        request: StrategyAdviceRequest,
        review_id: str,
        aggregation_row: Any,
        active_advice: Any | None,
        candidate: AdviceCandidate,
    ) -> LifecyclePlan:
        if active_advice is None:
            if should_create_new_advice_without_active(candidate, aggregation_row):
                return self._plan_create_new_advice(
                    request=request,
                    review_id=review_id,
                    aggregation_row=aggregation_row,
                    candidate=candidate,
                )
            return self._plan_no_active_wait_or_stop(
                request=request,
                review_id=review_id,
                aggregation_row=aggregation_row,
                candidate=candidate,
            )
        if candidate.terminal_lifecycle_action is not None and candidate.terminal_advice_status is not None:
            return self._plan_terminal_active_advice(
                request=request,
                review_id=review_id,
                aggregation_row=aggregation_row,
                active_advice=active_advice,
                candidate=candidate,
            )
        if active_advice_semantic_signature(active_advice) == candidate.semantic_signature:
            return self._plan_continue_active_advice(
                request=request,
                review_id=review_id,
                aggregation_row=aggregation_row,
                active_advice=active_advice,
                candidate=candidate,
            )
        return self._plan_update_active_advice(
            request=request,
            review_id=review_id,
            aggregation_row=aggregation_row,
            active_advice=active_advice,
            candidate=candidate,
        )

    def _plan_create_new_advice(
        self,
        *,
        request: StrategyAdviceRequest,
        review_id: str,
        aggregation_row: Any,
        candidate: AdviceCandidate,
    ) -> LifecyclePlan:
        version_no = 1
        advice_id = build_strategy_advice_id(
            review_aggregation_run_id=request.review_aggregation_run_id,
            version_no=version_no,
            trace_id=request.trace_id,
        )
        advice_payload = build_strategy_advice_payload(
            request=request,
            aggregation_row=aggregation_row,
            candidate=candidate,
            advice_id=advice_id,
            parent_advice_id=None,
            root_advice_id=advice_id,
            previous_advice_id=None,
            advice_path=advice_id,
            version_no=version_no,
            advice_status=AdviceStatus.ACTIVE,
        )
        setup_payloads = build_trade_setup_payloads(
            advice_id=advice_id,
            candidate=candidate,
            aggregation_row=aggregation_row,
        )
        return self._finalize_plan(
            request=request,
            review_id=review_id,
            aggregation_row=aggregation_row,
            candidate=candidate,
            lifecycle_action=LifecycleAction.CREATE_NEW_ADVICE,
            lifecycle_reason=candidate.lifecycle_reason,
            reviewed_advice_id=None,
            result_advice_id=advice_id,
            previous_advice_id=None,
            advice_code=advice_payload.advice_code,
            advice_path=advice_payload.advice_path,
            advice_status=AdviceStatus.ACTIVE,
            advice_payload=advice_payload,
            event_types=(AdviceEventType.CREATED, AdviceEventType.ACTIVATED),
            event_advice_ids=(advice_id, advice_id),
            setup_payloads=setup_payloads,
        )

    def _plan_no_active_wait_or_stop(
        self,
        *,
        request: StrategyAdviceRequest,
        review_id: str,
        aggregation_row: Any,
        candidate: AdviceCandidate,
    ) -> LifecyclePlan:
        lifecycle_action = lifecycle_action_without_active(candidate)
        return self._finalize_plan(
            request=request,
            review_id=review_id,
            aggregation_row=aggregation_row,
            candidate=candidate,
            lifecycle_action=lifecycle_action,
            lifecycle_reason=candidate.lifecycle_reason,
            reviewed_advice_id=None,
            result_advice_id=None,
            previous_advice_id=None,
            advice_code=None,
            advice_path=None,
            advice_status=None,
            advice_payload=None,
            event_types=(),
            event_advice_ids=(),
            setup_payloads=(),
        )

    def _plan_continue_active_advice(
        self,
        *,
        request: StrategyAdviceRequest,
        review_id: str,
        aggregation_row: Any,
        active_advice: Any,
        candidate: AdviceCandidate,
    ) -> LifecyclePlan:
        active_id = text_attr(active_advice, "advice_id")
        return self._finalize_plan(
            request=request,
            review_id=review_id,
            aggregation_row=aggregation_row,
            candidate=candidate,
            lifecycle_action=LifecycleAction.CONTINUE_ACTIVE_ADVICE,
            lifecycle_reason="No substantial semantic change; active advice continues.",
            reviewed_advice_id=active_id,
            result_advice_id=active_id,
            previous_advice_id=active_id,
            advice_code=text_attr(active_advice, "advice_code") or None,
            advice_path=text_attr(active_advice, "advice_path") or None,
            advice_status=AdviceStatus.ACTIVE,
            advice_payload=None,
            event_types=(AdviceEventType.CONTINUED,),
            event_advice_ids=(active_id,),
            setup_payloads=(),
        )

    def _plan_update_active_advice(
        self,
        *,
        request: StrategyAdviceRequest,
        review_id: str,
        aggregation_row: Any,
        active_advice: Any,
        candidate: AdviceCandidate,
    ) -> LifecyclePlan:
        old_id = text_attr(active_advice, "advice_id")
        version_no = int(getattr(active_advice, "version_no", 1) or 1) + 1
        advice_id = build_strategy_advice_id(
            review_aggregation_run_id=request.review_aggregation_run_id,
            version_no=version_no,
            trace_id=request.trace_id,
        )
        root_advice_id = text_attr(active_advice, "root_advice_id") or old_id
        parent_path = text_attr(active_advice, "advice_path") or old_id
        advice_path = f"{parent_path}/{advice_id}"
        advice_payload = build_strategy_advice_payload(
            request=request,
            aggregation_row=aggregation_row,
            candidate=candidate,
            advice_id=advice_id,
            parent_advice_id=old_id,
            root_advice_id=root_advice_id,
            previous_advice_id=old_id,
            advice_path=advice_path,
            version_no=version_no,
            advice_status=AdviceStatus.ACTIVE,
        )
        setup_payloads = build_trade_setup_payloads(
            advice_id=advice_id,
            candidate=candidate,
            aggregation_row=aggregation_row,
        )
        plan = self._finalize_plan(
            request=request,
            review_id=review_id,
            aggregation_row=aggregation_row,
            candidate=candidate,
            lifecycle_action=LifecycleAction.UPDATE_ACTIVE_ADVICE,
            lifecycle_reason=candidate.lifecycle_reason,
            reviewed_advice_id=old_id,
            result_advice_id=advice_id,
            previous_advice_id=old_id,
            advice_code=advice_payload.advice_code,
            advice_path=advice_payload.advice_path,
            advice_status=AdviceStatus.ACTIVE,
            advice_payload=advice_payload,
            event_types=(AdviceEventType.SUPERSEDED, AdviceEventType.CREATED, AdviceEventType.ACTIVATED),
            event_advice_ids=(old_id, advice_id, advice_id),
            setup_payloads=setup_payloads,
        )
        return replace_plan_status_update(plan, row=active_advice, status=AdviceStatus.SUPERSEDED)

    def _plan_terminal_active_advice(
        self,
        *,
        request: StrategyAdviceRequest,
        review_id: str,
        aggregation_row: Any,
        active_advice: Any,
        candidate: AdviceCandidate,
    ) -> LifecyclePlan:
        active_id = text_attr(active_advice, "advice_id")
        event_type = event_type_for_terminal_status(candidate.terminal_advice_status or AdviceStatus.CLOSED)
        plan = self._finalize_plan(
            request=request,
            review_id=review_id,
            aggregation_row=aggregation_row,
            candidate=candidate,
            lifecycle_action=candidate.terminal_lifecycle_action or LifecycleAction.CLOSE_ACTIVE_ADVICE,
            lifecycle_reason=candidate.lifecycle_reason,
            reviewed_advice_id=active_id,
            result_advice_id=active_id,
            previous_advice_id=active_id,
            advice_code=text_attr(active_advice, "advice_code") or None,
            advice_path=text_attr(active_advice, "advice_path") or None,
            advice_status=candidate.terminal_advice_status,
            advice_payload=None,
            event_types=(event_type,),
            event_advice_ids=(active_id,),
            setup_payloads=(),
        )
        return replace_plan_status_update(
            plan,
            row=active_advice,
            status=candidate.terminal_advice_status or AdviceStatus.CLOSED,
        )

    def _finalize_plan(
        self,
        *,
        request: StrategyAdviceRequest,
        review_id: str,
        aggregation_row: Any,
        candidate: AdviceCandidate,
        lifecycle_action: LifecycleAction,
        lifecycle_reason: str,
        reviewed_advice_id: str | None,
        result_advice_id: str | None,
        previous_advice_id: str | None,
        advice_code: str | None,
        advice_path: str | None,
        advice_status: AdviceStatus | None,
        advice_payload: Any | None,
        event_types: tuple[AdviceEventType, ...],
        event_advice_ids: tuple[str | None, ...],
        setup_payloads: tuple[StrategyAdviceTradeSetupPersistencePayload, ...],
    ) -> LifecyclePlan:
        notification_level = notification_level_for_lifecycle(lifecycle_action, candidate)
        notification_reason = notification_reason_for_lifecycle(lifecycle_action, candidate)
        notification_payload = build_notification_payload(
            lifecycle_action=lifecycle_action,
            lifecycle_reason=lifecycle_reason,
            aggregation_row=aggregation_row,
            candidate=candidate,
            reviewed_advice_id=reviewed_advice_id,
            result_advice_id=result_advice_id,
            advice_code=advice_code,
            advice_path=advice_path,
            notification_level=notification_level,
            trade_setup_count=len(setup_payloads),
        )
        lifecycle_payload = build_lifecycle_review_payload(
            review_id=review_id,
            aggregation_row=aggregation_row,
            lifecycle_action=lifecycle_action,
            lifecycle_reason=lifecycle_reason,
            reviewed_advice_id=reviewed_advice_id,
            result_advice_id=result_advice_id,
            previous_advice_id=previous_advice_id,
            notification_level=notification_level,
            notification_reason=notification_reason,
            notification_payload=notification_payload,
        )
        event_payloads = build_event_payloads(
            review_id=review_id,
            event_types=event_types + (AdviceEventType.NOTIFICATION_PAYLOAD_CREATED,),
            event_advice_ids=event_advice_ids + (result_advice_id,),
            event_reason=notification_reason,
            event_payload=notification_payload,
        )
        return LifecyclePlan(
            aggregation_row=aggregation_row,
            candidate=candidate,
            lifecycle_action=lifecycle_action,
            lifecycle_reason=lifecycle_reason,
            reviewed_advice_id=reviewed_advice_id,
            result_advice_id=result_advice_id,
            previous_advice_id=previous_advice_id,
            advice_code=advice_code,
            advice_path=advice_path,
            advice_status=advice_status,
            advice_payload=advice_payload,
            lifecycle_payload=lifecycle_payload,
            event_payloads=event_payloads,
            trade_setup_payloads=setup_payloads,
        )


def run_strategy_advice(
    *,
    db_session: Any,
    request: StrategyAdviceRequest,
    service: StrategyAdviceService | None = None,
) -> StrategyAdviceResult:
    """Convenience app-service function used by CLI and tests."""

    active_service = service or create_default_strategy_advice_service()
    return active_service.run_strategy_advice(db_session, request=request)


def create_default_strategy_advice_service() -> StrategyAdviceService:
    """Create the default deterministic stage-21A service."""

    return StrategyAdviceService()


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = [
    "ALLOWED_STRATEGY_ADVICE_TRIGGER_SOURCES",
    "StrategyAdviceService",
    "create_default_strategy_advice_service",
    "run_strategy_advice",
]
