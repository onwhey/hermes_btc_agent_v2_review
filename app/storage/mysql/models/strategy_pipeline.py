"""SQLAlchemy model for stage-25A strategy pipeline event logs.

This file belongs to `app/storage/mysql/models`. It defines only the manual
strategy pipeline audit table used by stage 25A.

Called by Alembic metadata import and `app/strategy_pipeline/repository.py`.
External services: none. MySQL: metadata only at import time. Redis: none.
Hermes: none. Large models: none. Trading execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.storage.mysql.base import Base

try:
    from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = Boolean = DateTime = Index = String = Text = UniqueConstraint = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class StrategyPipelineEventLog(Base):
        """ORM mapping for one 25A manual pipeline run audit row."""

        __tablename__ = "strategy_pipeline_event_log"
        __table_args__ = (
            UniqueConstraint("pipeline_run_id", name="uq_strategy_pipeline_run_id"),
            Index(
                "idx_strategy_pipeline_scope_slot",
                "symbol",
                "base_interval",
                "higher_interval",
                "kline_slot_utc",
            ),
            Index("idx_strategy_pipeline_status_created", "status", "created_at_utc"),
            Index("idx_strategy_pipeline_trace_id", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        pipeline_run_id: Mapped[str] = mapped_column(String(160), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        kline_slot_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        kline_slot_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        current_step: Mapped[str | None] = mapped_column(String(96), nullable=True)
        strategy_signal_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        strategy_evidence_aggregation_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        material_pack_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        model_analysis_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        review_aggregation_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        advice_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        review_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        notification_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
        model_review_invoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        model_review_reused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        real_model_called: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        hermes_real_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
        started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        finished_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class StrategyPipelineEventLog:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        pipeline_run_id: str = ""
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        kline_slot_utc: datetime | None = None
        kline_slot_source: str | None = None
        trigger_source: str = ""
        status: str = ""
        current_step: str | None = None
        strategy_signal_run_id: str | None = None
        strategy_evidence_aggregation_id: str | None = None
        material_pack_id: str | None = None
        model_analysis_run_id: str | None = None
        review_aggregation_run_id: str | None = None
        advice_id: str | None = None
        review_id: str | None = None
        notification_status: str | None = None
        model_review_invoked: bool = False
        model_review_reused: bool = False
        real_model_called: bool = False
        hermes_real_sent: bool = False
        error_code: str | None = None
        error_message: str | None = None
        trace_id: str = ""
        details_json: str = "{}"
        started_at_utc: datetime | None = None
        finished_at_utc: datetime | None = None
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None

