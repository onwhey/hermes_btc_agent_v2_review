"""Internal lifecycle plan DTO for stage-21A strategy advice.

This file belongs to `app/strategy_advice`. It holds the in-memory plan that
the service can either return as dry-run output or persist as stage-21A rows.
It does not access external services, MySQL, Redis, Hermes, model providers, or
trading execution capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.strategy_advice.lifecycle import AdviceCandidate
from app.strategy_advice.schema import (
    AdviceStatus,
    LifecycleAction,
    StrategyAdviceEventPersistencePayload,
    StrategyAdviceLifecycleReviewPersistencePayload,
    StrategyAdvicePersistencePayload,
    StrategyAdviceTradeSetupPersistencePayload,
)


@dataclass(frozen=True)
class LifecyclePlan:
    """Bounded in-memory write plan for one stage-21A lifecycle pass."""

    aggregation_row: Any
    candidate: AdviceCandidate
    lifecycle_action: LifecycleAction
    lifecycle_reason: str
    reviewed_advice_id: str | None
    result_advice_id: str | None
    previous_advice_id: str | None
    advice_code: str | None
    advice_path: str | None
    advice_status: AdviceStatus | None
    advice_payload: StrategyAdvicePersistencePayload | None
    lifecycle_payload: StrategyAdviceLifecycleReviewPersistencePayload
    event_payloads: tuple[StrategyAdviceEventPersistencePayload, ...]
    trade_setup_payloads: tuple[StrategyAdviceTradeSetupPersistencePayload, ...]
    status_update_row: Any | None = None
    status_update_to: AdviceStatus | None = None


def replace_plan_status_update(plan: LifecyclePlan, *, row: Any, status: AdviceStatus) -> LifecyclePlan:
    """Return a copy of a plan with one advice status update attached."""

    return LifecyclePlan(
        aggregation_row=plan.aggregation_row,
        candidate=plan.candidate,
        lifecycle_action=plan.lifecycle_action,
        lifecycle_reason=plan.lifecycle_reason,
        reviewed_advice_id=plan.reviewed_advice_id,
        result_advice_id=plan.result_advice_id,
        previous_advice_id=plan.previous_advice_id,
        advice_code=plan.advice_code,
        advice_path=plan.advice_path,
        advice_status=plan.advice_status,
        advice_payload=plan.advice_payload,
        lifecycle_payload=plan.lifecycle_payload,
        event_payloads=plan.event_payloads,
        trade_setup_payloads=plan.trade_setup_payloads,
        status_update_row=row,
        status_update_to=status,
    )


__all__ = ["LifecyclePlan", "replace_plan_status_update"]
