"""Stage-22A manual execution feedback ORM models.

This file belongs to `app/storage/mysql/models`. It defines only the two
stage-22A manual execution metadata tables: manual position summary rows and
manual execution record rows.

Called by Alembic metadata, the manual execution repository, and tests.
External services: none at import time. MySQL: metadata only at import time.
Redis: none. Hermes: none. DeepSeek: none. Trading execution: none. These
tables record only user-provided manual execution feedback; they are not
exchange positions and they never enable automatic trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.storage.mysql.base import Base

try:
    from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = Boolean = CheckConstraint = DateTime = ForeignKey = Index = Numeric = String = UniqueConstraint = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class StrategyAdviceManualPosition(Base):
        """ORM mapping for one user-reported manual position lifecycle."""

        __tablename__ = "strategy_advice_manual_position"
        __table_args__ = (
            UniqueConstraint("manual_position_id", name="uq_manual_position_id"),
            CheckConstraint("side in ('long', 'short')", name="ck_manual_position_side"),
            CheckConstraint("status in ('open', 'closed')", name="ck_manual_position_status"),
            Index("idx_manual_position_symbol_side_status", "symbol", "side", "status"),
            Index("idx_manual_position_opened_advice", "opened_by_advice_id"),
            Index("idx_manual_position_latest_advice", "latest_related_advice_id"),
            Index("idx_manual_position_closed_advice", "closed_by_advice_id"),
            Index("idx_manual_position_trace", "trace_id"),
            Index("idx_manual_position_created", "created_at_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        manual_position_id: Mapped[str] = mapped_column(String(160), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        side: Mapped[str] = mapped_column(String(16), nullable=False)
        status: Mapped[str] = mapped_column(String(16), nullable=False)
        opened_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        closed_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        opened_by_advice_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=False,
        )
        latest_related_advice_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=False,
        )
        closed_by_advice_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=True,
        )
        initial_entry_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        avg_entry_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        close_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
        current_quantity_base_asset: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        current_cost_basis_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        margin_basis_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        effective_leverage: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        total_open_notional_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        total_close_notional_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        total_fee_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        gross_realized_pnl_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        net_realized_pnl_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        net_pnl_ratio_on_margin: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        open_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
        open_decision_context: Mapped[str | None] = mapped_column(String(2000), nullable=True)
        review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_reviewed")
        review_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
        review_correctness: Mapped[str | None] = mapped_column(String(64), nullable=True)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        created_by: Mapped[str] = mapped_column(String(64), nullable=False)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
        auto_trading_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


    class StrategyAdviceExecutionRecord(Base):
        """ORM mapping for one user-reported manual execution action."""

        __tablename__ = "strategy_advice_execution_record"
        __table_args__ = (
            UniqueConstraint("execution_id", name="uq_manual_execution_id"),
            CheckConstraint("side in ('long', 'short')", name="ck_manual_execution_side"),
            CheckConstraint(
                "execution_action in "
                "('open_position', 'add_position', 'reduce_position', 'close_position', 'take_profit', 'stop_loss')",
                name="ck_manual_execution_action",
            ),
            Index("idx_manual_execution_position", "manual_position_id"),
            Index("idx_manual_execution_advice", "advice_id"),
            Index("idx_manual_execution_review", "review_id"),
            Index("idx_manual_execution_setup", "setup_id"),
            Index("idx_manual_execution_action_created", "execution_action", "created_at_utc"),
            Index("idx_manual_execution_trace", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        execution_id: Mapped[str] = mapped_column(String(160), nullable=False)
        manual_position_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("strategy_advice_manual_position.manual_position_id"),
            nullable=False,
        )
        execution_action: Mapped[str] = mapped_column(String(32), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        side: Mapped[str] = mapped_column(String(16), nullable=False)
        price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        notional_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        quantity_base_asset: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        margin_usdt: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
        fee_rate: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        fee_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        gross_pnl_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        net_pnl_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        advice_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=False,
        )
        review_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("strategy_advice_lifecycle_review.review_id"),
            nullable=True,
        )
        setup_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("strategy_advice_trade_setup.setup_id"),
            nullable=True,
        )
        advice_resolution_method: Mapped[str] = mapped_column(String(64), nullable=False)
        setup_resolution_method: Mapped[str] = mapped_column(String(64), nullable=False)
        manual_position_resolution_method: Mapped[str] = mapped_column(String(64), nullable=False)
        reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
        note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
        executed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        created_by: Mapped[str] = mapped_column(String(64), nullable=False)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
        auto_trading_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

else:

    @dataclass
    class StrategyAdviceManualPosition:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        manual_position_id: str = ""
        symbol: str = ""
        side: str = ""
        status: str = ""
        opened_at_utc: datetime | None = None
        closed_at_utc: datetime | None = None
        opened_by_advice_id: str = ""
        latest_related_advice_id: str = ""
        closed_by_advice_id: str | None = None
        initial_entry_price: Decimal = Decimal("0")
        avg_entry_price: Decimal = Decimal("0")
        close_price: Decimal | None = None
        current_quantity_base_asset: Decimal = Decimal("0")
        current_cost_basis_usdt: Decimal = Decimal("0")
        margin_basis_usdt: Decimal = Decimal("0")
        effective_leverage: Decimal = Decimal("0")
        total_open_notional_usdt: Decimal = Decimal("0")
        total_close_notional_usdt: Decimal = Decimal("0")
        total_fee_usdt: Decimal = Decimal("0")
        gross_realized_pnl_usdt: Decimal = Decimal("0")
        net_realized_pnl_usdt: Decimal = Decimal("0")
        net_pnl_ratio_on_margin: Decimal = Decimal("0")
        open_reason: str | None = None
        open_decision_context: str | None = None
        review_status: str = "not_reviewed"
        review_summary: str | None = None
        review_correctness: str | None = None
        trigger_source: str = ""
        created_by: str = ""
        trace_id: str = ""
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None
        is_manual: bool = True
        auto_trading_allowed: bool = False


    @dataclass
    class StrategyAdviceExecutionRecord:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        execution_id: str = ""
        manual_position_id: str = ""
        execution_action: str = ""
        symbol: str = ""
        side: str = ""
        price: Decimal = Decimal("0")
        notional_usdt: Decimal = Decimal("0")
        quantity_base_asset: Decimal = Decimal("0")
        margin_usdt: Decimal | None = None
        fee_rate: Decimal = Decimal("0")
        fee_usdt: Decimal = Decimal("0")
        gross_pnl_usdt: Decimal = Decimal("0")
        net_pnl_usdt: Decimal = Decimal("0")
        advice_id: str = ""
        review_id: str | None = None
        setup_id: str | None = None
        advice_resolution_method: str = ""
        setup_resolution_method: str = ""
        manual_position_resolution_method: str = ""
        reason: str | None = None
        note: str | None = None
        executed_at_utc: datetime | None = None
        trigger_source: str = ""
        created_by: str = ""
        trace_id: str = ""
        created_at_utc: datetime | None = None
        is_manual: bool = True
        auto_trading_allowed: bool = False


__all__ = ["StrategyAdviceExecutionRecord", "StrategyAdviceManualPosition"]
