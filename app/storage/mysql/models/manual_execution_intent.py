"""Stage-22B manual execution intent ORM model.

This file belongs to `app/storage/mysql/models`. It defines only the
stage-22B Hermes/WeChat manual execution confirmation-intent table.

Called by Alembic metadata and the 22B intent repository. External services:
none at import time. MySQL: metadata only at import time. Redis: none.
Hermes: none. DeepSeek: none. Trading execution: none. The table stores
user-provided intent drafts and never records exchange positions directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.storage.mysql.base import Base

try:
    from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Index, Numeric, String, Text, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = Boolean = CheckConstraint = DateTime = Index = Numeric = String = Text = UniqueConstraint = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class StrategyAdviceManualExecutionIntent(Base):
        """ORM mapping for one Hermes/WeChat manual execution confirmation intent."""

        __tablename__ = "strategy_advice_manual_execution_intent"
        __table_args__ = (
            UniqueConstraint("intent_id", name="uq_manual_execution_intent_id"),
            CheckConstraint(
                "status in "
                "('pending_confirmation', 'confirmed', 'executed', 'cancelled', 'expired', "
                "'parse_failed', 'validation_failed', 'execution_failed', 'failed')",
                name="ck_manual_execution_intent_status",
            ),
            Index("idx_manual_execution_intent_status_expires", "status", "expires_at_utc"),
            Index("idx_manual_execution_intent_source_message", "source_channel", "source_message_id"),
            Index("idx_manual_execution_intent_trace", "trace_id"),
            Index("idx_manual_execution_intent_created", "created_at_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        intent_id: Mapped[str] = mapped_column(String(160), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        source_channel: Mapped[str] = mapped_column(String(32), nullable=False)
        source_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        source_user_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        raw_text: Mapped[str] = mapped_column(Text, nullable=False)
        normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
        parsed_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
        parsed_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
        parsed_side: Mapped[str | None] = mapped_column(String(16), nullable=True)
        parsed_manual_position_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        parsed_advice_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        parsed_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
        parsed_notional_usdt: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
        parsed_margin_usdt: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
        parsed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
        parsed_note: Mapped[str | None] = mapped_column(Text, nullable=True)
        parsed_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
        validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
        validation_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        validation_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        missing_fields_json: Mapped[str] = mapped_column(Text, nullable=False)
        dry_run_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
        executed_manual_position_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        executed_execution_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        expires_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        confirmed_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        cancelled_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        executed_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        failed_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
        auto_trading_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

else:

    @dataclass
    class StrategyAdviceManualExecutionIntent:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        intent_id: str = ""
        status: str = ""
        source_channel: str = ""
        source_message_id: str | None = None
        source_user_id: str | None = None
        raw_text: str = ""
        normalized_text: str = ""
        parsed_action: str | None = None
        parsed_symbol: str | None = None
        parsed_side: str | None = None
        parsed_manual_position_id: str | None = None
        parsed_advice_id: str | None = None
        parsed_price: Decimal | None = None
        parsed_notional_usdt: Decimal | None = None
        parsed_margin_usdt: Decimal | None = None
        parsed_reason: str | None = None
        parsed_note: str | None = None
        parsed_payload_json: str = "{}"
        validation_status: str = ""
        validation_error_code: str | None = None
        validation_error_message: str | None = None
        missing_fields_json: str = "[]"
        dry_run_snapshot_json: str = "{}"
        executed_manual_position_id: str | None = None
        executed_execution_id: str | None = None
        expires_at_utc: datetime | None = None
        confirmed_at_utc: datetime | None = None
        cancelled_at_utc: datetime | None = None
        executed_at_utc: datetime | None = None
        failed_at_utc: datetime | None = None
        trace_id: str = ""
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None
        is_manual: bool = True
        auto_trading_allowed: bool = False


__all__ = ["StrategyAdviceManualExecutionIntent"]
