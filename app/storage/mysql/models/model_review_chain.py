"""SQLAlchemy models for stage-20B model review chain orchestration.

This file belongs to `app/storage/mysql/models`. It defines only
`model_review_chain_run` and `model_review_chain_step` metadata for the
stage-20B chain/step state machine.

Called by Alembic metadata, the stage-20B repository, and tests.
External services: none. MySQL: metadata only at import time. Redis: none.
Hermes: none. DeepSeek/large-model calls: none. Trading execution: none.
It never writes formal Kline tables and never stores final trading advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.storage.mysql.base import Base

try:
    from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = Boolean = DateTime = ForeignKey = Index = String = Text = UniqueConstraint = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class ModelReviewChainRun(Base):
        """ORM mapping for one stage-20B model review chain run.

        Parameters: values are supplied by `app/model_review_chain/repository.py`.
        Return value: SQLAlchemy ORM row.
        Failure scenarios: SQLAlchemy raises mapping/database errors when used.
        External services: none at class definition time.
        Data impact: stores compact chain state only; boundary flags are fixed
        false and this table is not a trading signal or executable advice.
        """

        __tablename__ = "model_review_chain_run"
        __table_args__ = (
            UniqueConstraint("chain_id", name="uq_model_review_chain_run_chain_id"),
            Index("idx_model_review_chain_run_material_pack", "material_pack_id"),
            Index("idx_model_review_chain_run_aggregation", "aggregation_run_id"),
            Index("idx_model_review_chain_run_strategy_signal", "strategy_signal_run_id"),
            Index("idx_model_review_chain_run_status_created", "status", "created_at_utc"),
            Index("idx_model_review_chain_run_trace_id", "trace_id"),
            Index("idx_model_review_chain_run_chain_key", "chain_key"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        chain_id: Mapped[str] = mapped_column(String(160), nullable=False)
        material_pack_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("analysis_material_pack.material_pack_id"),
            nullable=False,
        )
        aggregation_run_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("strategy_aggregation_run.aggregation_run_id"),
            nullable=True,
        )
        strategy_signal_run_id: Mapped[str | None] = mapped_column(
            String(128),
            ForeignKey("strategy_signal_run.run_id"),
            nullable=True,
        )
        snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
        base_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
        higher_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
        chain_key: Mapped[str] = mapped_column(String(128), nullable=False)
        chain_profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        current_step: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        total_steps: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        success_step_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        failed_step_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        timeout_step_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        skipped_step_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        blocked_step_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        max_retry_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
        summary_text: Mapped[str] = mapped_column(Text, nullable=False)
        error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        is_final_trading_advice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        is_trading_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        is_executable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        auto_trading_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    class ModelReviewChainStep(Base):
        """ORM mapping for one stage-20B chain step.

        Each step may point at a mock `model_analysis_run` row. A successful
        step is never executed again during resume; the service only updates
        non-success resumable states.
        """

        __tablename__ = "model_review_chain_step"
        __table_args__ = (
            UniqueConstraint("chain_step_id", name="uq_model_review_chain_step_id"),
            UniqueConstraint("chain_id", "step_no", name="uk_model_review_chain_step_chain_no"),
            Index("idx_model_review_chain_step_chain", "chain_id"),
            Index("idx_model_review_chain_step_status", "status"),
            Index("idx_model_review_chain_step_model_run", "model_analysis_run_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        chain_step_id: Mapped[str] = mapped_column(String(160), nullable=False)
        chain_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("model_review_chain_run.chain_id"),
            nullable=False,
        )
        step_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
        model_key: Mapped[str] = mapped_column(String(96), nullable=False)
        model_role: Mapped[str] = mapped_column(String(96), nullable=False)
        parent_step_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        parent_model_analysis_run_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("model_analysis_run.model_analysis_run_id"),
            nullable=True,
        )
        model_analysis_run_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("model_analysis_run.model_analysis_run_id"),
            nullable=True,
        )
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        attempt_no: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        max_retry_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
        started_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        finished_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        retry_after_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        step_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
        step_output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class ModelReviewChainRun:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        chain_id: str = ""
        material_pack_id: str = ""
        aggregation_run_id: str | None = None
        strategy_signal_run_id: str | None = None
        snapshot_id: str | None = None
        symbol: str | None = None
        base_interval: str | None = None
        higher_interval: str | None = None
        chain_key: str = ""
        chain_profile_version: str = ""
        status: str = ""
        trigger_source: str = ""
        trace_id: str = ""
        current_step: int = 0
        total_steps: int = 0
        success_step_count: int = 0
        failed_step_count: int = 0
        timeout_step_count: int = 0
        skipped_step_count: int = 0
        blocked_step_count: int = 0
        max_retry_count: int = 1
        summary_text: str = ""
        error_code: str | None = None
        error_message: str | None = None
        is_final_trading_advice: bool = False
        is_trading_signal: bool = False
        is_executable: bool = False
        auto_trading_allowed: bool = False
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


    @dataclass
    class ModelReviewChainStep:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        chain_step_id: str = ""
        chain_id: str = ""
        step_no: int = 0
        model_key: str = ""
        model_role: str = ""
        parent_step_id: str | None = None
        parent_model_analysis_run_id: str | None = None
        model_analysis_run_id: str | None = None
        status: str = ""
        attempt_no: int = 0
        max_retry_count: int = 1
        started_at_utc: datetime | None = None
        finished_at_utc: datetime | None = None
        error_code: str | None = None
        error_message: str | None = None
        retry_after_utc: datetime | None = None
        step_input_hash: str | None = None
        step_output_hash: str | None = None
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


__all__ = ["ModelReviewChainRun", "ModelReviewChainStep"]
