"""Repository for stage-21A strategy advice lifecycle persistence.

This file belongs to `app/strategy_advice`. It reads stage-20A
`model_review_aggregation_run` rows and reads/writes only stage-21A advice,
lifecycle review, event, and setup rows.

Called by `app/strategy_advice/service.py`. External services: none. MySQL:
reads/writes through the caller-owned session and never commits. Redis: none.
Hermes: none. Large-model calls: none. Formal Kline impact: none. Trading
execution: none.
"""

from __future__ import annotations

from typing import Any

from app.core.time_utils import now_utc
from app.strategy_advice.models import (
    ModelReviewAggregationRun,
    StrategyAdvice,
    StrategyAdviceEvent,
    StrategyAdviceLifecycleReview,
    StrategyAdviceTradeSetup,
)
from app.strategy_advice.schema import (
    StrategyAdviceEventPersistencePayload,
    StrategyAdviceLifecycleReviewPersistencePayload,
    StrategyAdvicePersistencePayload,
    StrategyAdviceTradeSetupPersistencePayload,
    json_text,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class StrategyAdviceRepository:
    """Data access helper for stage-21A advice lifecycle state.

    Parameters: none.
    Return value: repository instance.
    Failure scenarios: database query/insert/update errors propagate to the
    service, which converts them into structured failures.
    External service access: none.
    Data impact: writes only stage-21A rows and never commits.
    """

    def get_review_aggregation_run_by_id(self, db_session: Any, *, review_aggregation_run_id: str) -> Any | None:
        """Return one stage-20A aggregation row by business id."""

        _require_sqlalchemy()
        stmt = (
            select(ModelReviewAggregationRun)
            .where(ModelReviewAggregationRun.review_aggregation_run_id == review_aggregation_run_id)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_active_strategy_advice(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
    ) -> Any | None:
        """Return the latest active advice for one symbol/base/higher tuple."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdvice)
            .where(StrategyAdvice.symbol == symbol)
            .where(StrategyAdvice.base_interval == base_interval)
            .where(StrategyAdvice.higher_interval == higher_interval)
            .where(StrategyAdvice.advice_status == "active")
            .order_by(StrategyAdvice.created_at_utc.desc(), StrategyAdvice.id.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def create_strategy_advice(
        self,
        db_session: Any,
        *,
        payload: StrategyAdvicePersistencePayload,
    ) -> StrategyAdvice:
        """Insert one `strategy_advice` row without committing."""

        created_at_utc = now_utc()
        row = StrategyAdvice(
            advice_id=payload.advice_id,
            advice_code=payload.advice_code,
            symbol=payload.symbol,
            base_interval=payload.base_interval,
            higher_interval=payload.higher_interval,
            parent_advice_id=payload.parent_advice_id,
            root_advice_id=payload.root_advice_id,
            previous_advice_id=payload.previous_advice_id,
            advice_path=payload.advice_path,
            version_no=payload.version_no,
            advice_status=payload.advice_status.value,
            advice_action=payload.advice_action.value,
            directional_bias=payload.directional_bias.value,
            trade_permission=payload.trade_permission.value,
            source_review_aggregation_run_id=payload.source_review_aggregation_run_id,
            source_material_pack_id=payload.source_material_pack_id,
            source_strategy_signal_run_id=payload.source_strategy_signal_run_id,
            source_snapshot_id=payload.source_snapshot_id,
            source_model_chain_id=payload.source_model_chain_id,
            model_review_invoked=payload.model_review_invoked,
            model_review_invocation_mode=payload.model_review_invocation_mode,
            model_review_reused=payload.model_review_reused,
            reused_model_analysis_run_id=payload.reused_model_analysis_run_id,
            model_review_basis=payload.model_review_basis,
            model_review_expired=payload.model_review_expired,
            model_review_chain_status=payload.model_review_chain_status,
            latest_model_review_at_utc=payload.latest_model_review_at_utc,
            model_review_status_summary_json=json_text(payload.model_review_status_summary_json),
            summary_text=payload.summary_text,
            risk_summary_json=json_text(payload.risk_summary_json),
            strategy_summary_json=json_text(payload.strategy_summary_json),
            model_summary_json=json_text(payload.model_summary_json),
            is_trading_signal=False,
            is_executable=False,
            auto_trading_allowed=False,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
            closed_at_utc=payload.closed_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def update_strategy_advice_status(
        self,
        db_session: Any,
        advice_row: Any,
        *,
        advice_status: str,
        closed_at_utc: Any | None,
    ) -> Any:
        """Update one advice status row without committing."""

        advice_row.advice_status = advice_status
        advice_row.closed_at_utc = closed_at_utc
        advice_row.updated_at_utc = now_utc()
        advice_row.is_trading_signal = False
        advice_row.is_executable = False
        advice_row.auto_trading_allowed = False
        _flush_if_possible(db_session)
        return advice_row

    def create_lifecycle_review(
        self,
        db_session: Any,
        *,
        payload: StrategyAdviceLifecycleReviewPersistencePayload,
    ) -> StrategyAdviceLifecycleReview:
        """Insert one `strategy_advice_lifecycle_review` row without committing."""

        created_at_utc = now_utc()
        row = StrategyAdviceLifecycleReview(
            review_id=payload.review_id,
            symbol=payload.symbol,
            base_interval=payload.base_interval,
            higher_interval=payload.higher_interval,
            reviewed_advice_id=payload.reviewed_advice_id,
            result_advice_id=payload.result_advice_id,
            previous_advice_id=payload.previous_advice_id,
            lifecycle_action=payload.lifecycle_action.value,
            lifecycle_reason=payload.lifecycle_reason,
            source_review_aggregation_run_id=payload.source_review_aggregation_run_id,
            source_material_pack_id=payload.source_material_pack_id,
            source_strategy_signal_run_id=payload.source_strategy_signal_run_id,
            source_snapshot_id=payload.source_snapshot_id,
            model_review_invoked=payload.model_review_invoked,
            model_review_invocation_mode=payload.model_review_invocation_mode,
            model_review_reused=payload.model_review_reused,
            reused_model_analysis_run_id=payload.reused_model_analysis_run_id,
            model_review_basis=payload.model_review_basis,
            model_review_expired=payload.model_review_expired,
            model_review_chain_status=payload.model_review_chain_status,
            notification_required=payload.notification_required,
            notification_level=payload.notification_level,
            notification_reason=payload.notification_reason,
            notification_payload_json=json_text(payload.notification_payload_json),
            created_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def create_strategy_advice_event(
        self,
        db_session: Any,
        *,
        payload: StrategyAdviceEventPersistencePayload,
    ) -> StrategyAdviceEvent:
        """Insert one `strategy_advice_event` row without committing."""

        row = StrategyAdviceEvent(
            event_id=payload.event_id,
            advice_id=payload.advice_id,
            related_review_id=payload.related_review_id,
            event_type=payload.event_type.value,
            event_reason=payload.event_reason,
            event_payload_json=json_text(payload.event_payload_json),
            created_at_utc=now_utc(),
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def create_strategy_advice_trade_setup(
        self,
        db_session: Any,
        *,
        payload: StrategyAdviceTradeSetupPersistencePayload,
    ) -> StrategyAdviceTradeSetup:
        """Insert one `strategy_advice_trade_setup` row without committing."""

        created_at_utc = now_utc()
        row = StrategyAdviceTradeSetup(
            setup_id=payload.setup_id,
            advice_id=payload.advice_id,
            setup_rank=payload.setup_rank,
            setup_type=payload.setup_type,
            side=payload.side,
            entry_zone_json=json_text(payload.entry_zone_json),
            trigger_condition_json=json_text(payload.trigger_condition_json),
            invalid_condition_json=json_text(payload.invalid_condition_json),
            stop_loss_json=json_text(payload.stop_loss_json),
            target_zones_json=json_text(payload.target_zones_json),
            expiry_base_bars=payload.expiry_base_bars,
            permission=payload.permission.value,
            source_strategy_names_json=json_text(payload.source_strategy_names_json),
            source_model_keys_json=json_text(payload.source_model_keys_json),
            status=payload.status,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row


def create_default_strategy_advice_repository() -> StrategyAdviceRepository:
    """Create the default stage-21A repository."""

    return StrategyAdviceRepository()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for strategy advice repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = ["StrategyAdviceRepository", "create_default_strategy_advice_repository"]
