"""Repository for stage-22A manual execution feedback.

This file belongs to `app/manual_execution`. It reads existing strategy advice
metadata only to validate `advice_id` and infer optional review/setup IDs, then
reads/writes the two stage-22A manual feedback tables through a caller-owned
session.

Called by `app/manual_execution/service.py`. External services: none. MySQL:
reads/writes only through the provided session and never commits. Redis: none.
Hermes: none. DeepSeek: none. Trading execution: none. The repository does not
make business decisions and does not modify strategy advice lifecycle state.
"""

from __future__ import annotations

from typing import Any

from app.manual_execution.schema import ExecutionRecordPersistencePayload, ManualPositionPersistencePayload
from app.storage.mysql.models.manual_execution import StrategyAdviceExecutionRecord, StrategyAdviceManualPosition
from app.storage.mysql.models.strategy_advice import (
    StrategyAdvice,
    StrategyAdviceLifecycleReview,
    StrategyAdviceTradeSetup,
)

try:
    from sqlalchemy import or_, select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    or_ = select = None  # type: ignore[assignment]


class ManualExecutionRepository:
    """Data access helper for stage-22A manual execution feedback.

    Parameters: none.
    Return value: repository instance.
    Failure scenarios: database query/insert/update errors propagate to the
    service, which blocks or fails without pretending writes succeeded.
    External service access: none.
    Data impact: writes only stage-22A rows and never commits.
    """

    def get_advice_by_id(self, db_session: Any, *, advice_id: str) -> Any | None:
        """Return one strategy advice row by business id without updating it."""

        _require_sqlalchemy()
        stmt = select(StrategyAdvice).where(StrategyAdvice.advice_id == advice_id).limit(1)
        return db_session.execute(stmt).scalar_one_or_none()

    def find_review_ids_for_advice(self, db_session: Any, *, advice_id: str) -> tuple[str, ...]:
        """Return review IDs that can be associated with one advice id."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceLifecycleReview.review_id)
            .where(
                or_(
                    StrategyAdviceLifecycleReview.reviewed_advice_id == advice_id,
                    StrategyAdviceLifecycleReview.result_advice_id == advice_id,
                    StrategyAdviceLifecycleReview.previous_advice_id == advice_id,
                )
            )
            .order_by(StrategyAdviceLifecycleReview.created_at_utc.asc())
        )
        return tuple(row[0] for row in db_session.execute(stmt).all())

    def find_setup_ids_for_advice(self, db_session: Any, *, advice_id: str) -> tuple[str, ...]:
        """Return setup IDs under one advice id."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceTradeSetup.setup_id)
            .where(StrategyAdviceTradeSetup.advice_id == advice_id)
            .order_by(StrategyAdviceTradeSetup.setup_rank.asc(), StrategyAdviceTradeSetup.id.asc())
        )
        return tuple(row[0] for row in db_session.execute(stmt).all())

    def get_manual_position_by_id(self, db_session: Any, *, manual_position_id: str) -> Any | None:
        """Return one manual position summary row by business id."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceManualPosition)
            .where(StrategyAdviceManualPosition.manual_position_id == manual_position_id)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def find_open_manual_positions(self, db_session: Any, *, symbol: str, side: str) -> tuple[Any, ...]:
        """Return open manual positions for one symbol and side."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceManualPosition)
            .where(StrategyAdviceManualPosition.symbol == symbol)
            .where(StrategyAdviceManualPosition.side == side)
            .where(StrategyAdviceManualPosition.status == "open")
            .order_by(StrategyAdviceManualPosition.opened_at_utc.asc(), StrategyAdviceManualPosition.id.asc())
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def list_manual_positions(
        self,
        db_session: Any,
        *,
        status: str,
        symbol: str | None = None,
    ) -> tuple[Any, ...]:
        """List manual positions for CLI inspection."""

        _require_sqlalchemy()
        stmt = select(StrategyAdviceManualPosition).where(StrategyAdviceManualPosition.status == status)
        if symbol:
            stmt = stmt.where(StrategyAdviceManualPosition.symbol == symbol)
        stmt = stmt.order_by(StrategyAdviceManualPosition.opened_at_utc.asc(), StrategyAdviceManualPosition.id.asc())
        return tuple(db_session.execute(stmt).scalars().all())

    def list_execution_records_for_position(self, db_session: Any, *, manual_position_id: str) -> tuple[Any, ...]:
        """List execution records for receipt rendering."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceExecutionRecord)
            .where(StrategyAdviceExecutionRecord.manual_position_id == manual_position_id)
            .order_by(StrategyAdviceExecutionRecord.executed_at_utc.asc(), StrategyAdviceExecutionRecord.id.asc())
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def create_manual_position(
        self,
        db_session: Any,
        *,
        payload: ManualPositionPersistencePayload,
    ) -> StrategyAdviceManualPosition:
        """Insert one manual position summary row without committing."""

        row = StrategyAdviceManualPosition(
            manual_position_id=payload.manual_position_id,
            symbol=payload.symbol,
            side=payload.side,
            status=payload.status,
            opened_at_utc=payload.opened_at_utc,
            closed_at_utc=payload.closed_at_utc,
            opened_by_advice_id=payload.opened_by_advice_id,
            latest_related_advice_id=payload.latest_related_advice_id,
            closed_by_advice_id=payload.closed_by_advice_id,
            initial_entry_price=payload.initial_entry_price,
            avg_entry_price=payload.avg_entry_price,
            close_price=payload.close_price,
            current_quantity_base_asset=payload.current_quantity_base_asset,
            current_cost_basis_usdt=payload.current_cost_basis_usdt,
            margin_basis_usdt=payload.margin_basis_usdt,
            effective_leverage=payload.effective_leverage,
            total_open_notional_usdt=payload.total_open_notional_usdt,
            total_close_notional_usdt=payload.total_close_notional_usdt,
            total_fee_usdt=payload.total_fee_usdt,
            gross_realized_pnl_usdt=payload.gross_realized_pnl_usdt,
            net_realized_pnl_usdt=payload.net_realized_pnl_usdt,
            net_pnl_ratio_on_margin=payload.net_pnl_ratio_on_margin,
            open_reason=payload.open_reason,
            open_decision_context=payload.open_decision_context,
            review_status=payload.review_status,
            trigger_source=payload.trigger_source,
            created_by=payload.created_by,
            trace_id=payload.trace_id,
            created_at_utc=payload.created_at_utc,
            updated_at_utc=payload.updated_at_utc,
            is_manual=True,
            auto_trading_allowed=False,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def create_execution_record(
        self,
        db_session: Any,
        *,
        payload: ExecutionRecordPersistencePayload,
    ) -> StrategyAdviceExecutionRecord:
        """Insert one manual execution record row without committing."""

        row = StrategyAdviceExecutionRecord(
            execution_id=payload.execution_id,
            manual_position_id=payload.manual_position_id,
            execution_action=payload.execution_action,
            symbol=payload.symbol,
            side=payload.side,
            price=payload.price,
            notional_usdt=payload.notional_usdt,
            quantity_base_asset=payload.quantity_base_asset,
            margin_usdt=payload.margin_usdt,
            fee_rate=payload.fee_rate,
            fee_usdt=payload.fee_usdt,
            gross_pnl_usdt=payload.gross_pnl_usdt,
            net_pnl_usdt=payload.net_pnl_usdt,
            advice_id=payload.advice_id,
            review_id=payload.review_id,
            setup_id=payload.setup_id,
            advice_resolution_method=payload.advice_resolution_method,
            setup_resolution_method=payload.setup_resolution_method,
            manual_position_resolution_method=payload.manual_position_resolution_method,
            reason=payload.reason,
            note=payload.note,
            executed_at_utc=payload.executed_at_utc,
            trigger_source=payload.trigger_source,
            created_by=payload.created_by,
            trace_id=payload.trace_id,
            created_at_utc=payload.created_at_utc,
            is_manual=True,
            auto_trading_allowed=False,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def update_manual_position_from_payload(
        self,
        db_session: Any,
        manual_position_row: Any,
        *,
        payload: ManualPositionPersistencePayload,
    ) -> Any:
        """Update one manual position summary row without committing."""

        for field_name, value in payload.__dict__.items():
            if field_name in {"manual_position_id", "opened_at_utc", "created_at_utc", "created_by", "trace_id"}:
                continue
            setattr(manual_position_row, field_name, value)
        manual_position_row.is_manual = True
        manual_position_row.auto_trading_allowed = False
        _flush_if_possible(db_session)
        return manual_position_row


def create_default_manual_execution_repository() -> ManualExecutionRepository:
    """Create the default stage-22A manual execution repository."""

    return ManualExecutionRepository()


def _require_sqlalchemy() -> None:
    if select is None or or_ is None:
        raise RuntimeError("SQLAlchemy is required for manual execution repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()

