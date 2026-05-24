"""SQLAlchemy models for stage-21A strategy advice lifecycle.

This file belongs to `app/storage/mysql/models`. It defines only metadata for
strategy advice rows, lifecycle review rows, lifecycle events, and conditional
trade setup rows.

Called by Alembic metadata, the stage-21A repository, and tests. External
services: none. MySQL: metadata only at import time. Redis: none. Hermes: none.
Large-model calls: none. Trading execution: none. These tables store human
advice lifecycle state and bounded JSON summaries; they do not store orders,
private trading state, full prompts, full model responses, or Kline arrays.
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

    class StrategyAdvice(Base):
        """ORM mapping for one versioned human strategy advice row."""

        __tablename__ = "strategy_advice"
        __table_args__ = (
            UniqueConstraint("advice_id", name="uq_strategy_advice_advice_id"),
            Index("idx_strategy_advice_symbol_status", "symbol", "base_interval", "higher_interval", "advice_status"),
            Index("idx_strategy_advice_root", "root_advice_id"),
            Index("idx_strategy_advice_parent", "parent_advice_id"),
            Index("idx_strategy_advice_source_review", "source_review_aggregation_run_id"),
            Index("idx_strategy_advice_material_pack", "source_material_pack_id"),
            Index("idx_strategy_advice_created", "created_at_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        advice_id: Mapped[str] = mapped_column(String(160), nullable=False)
        advice_code: Mapped[str] = mapped_column(String(160), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        parent_advice_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=True,
        )
        root_advice_id: Mapped[str] = mapped_column(String(160), nullable=False)
        previous_advice_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=True,
        )
        advice_path: Mapped[str] = mapped_column(Text, nullable=False)
        version_no: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
        advice_status: Mapped[str] = mapped_column(String(32), nullable=False)
        advice_action: Mapped[str] = mapped_column(String(64), nullable=False)
        directional_bias: Mapped[str] = mapped_column(String(32), nullable=False)
        trade_permission: Mapped[str] = mapped_column(String(64), nullable=False)
        source_review_aggregation_run_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("model_review_aggregation_run.review_aggregation_run_id"),
            nullable=False,
        )
        source_material_pack_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("analysis_material_pack.material_pack_id"),
            nullable=False,
        )
        source_strategy_signal_run_id: Mapped[str | None] = mapped_column(
            String(128),
            ForeignKey("strategy_signal_run.run_id"),
            nullable=True,
        )
        source_snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        source_model_chain_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("model_review_chain_run.chain_id"),
            nullable=True,
        )
        model_review_invoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        model_review_invocation_mode: Mapped[str] = mapped_column(String(64), nullable=False)
        model_review_reused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        reused_model_analysis_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        model_review_basis: Mapped[str] = mapped_column(String(96), nullable=False)
        model_review_expired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        model_review_chain_status: Mapped[str] = mapped_column(String(32), nullable=False)
        latest_model_review_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        model_review_status_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
        summary_text: Mapped[str] = mapped_column(Text, nullable=False)
        risk_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
        strategy_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
        model_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
        is_trading_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        is_executable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        auto_trading_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        closed_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


    class StrategyAdviceLifecycleReview(Base):
        """ORM mapping for one 4h lifecycle review over active advice state."""

        __tablename__ = "strategy_advice_lifecycle_review"
        __table_args__ = (
            UniqueConstraint("review_id", name="uq_strategy_advice_lifecycle_review_id"),
            UniqueConstraint(
                "source_review_aggregation_run_id",
                name="uq_strategy_advice_lifecycle_source_review",
            ),
            Index("idx_strategy_advice_lifecycle_symbol", "symbol", "base_interval", "higher_interval"),
            Index("idx_strategy_advice_lifecycle_reviewed", "reviewed_advice_id"),
            Index("idx_strategy_advice_lifecycle_result", "result_advice_id"),
            Index("idx_strategy_advice_lifecycle_source_review", "source_review_aggregation_run_id"),
            Index("idx_strategy_advice_lifecycle_created", "created_at_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        review_id: Mapped[str] = mapped_column(String(160), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        reviewed_advice_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=True,
        )
        result_advice_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=True,
        )
        previous_advice_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=True,
        )
        lifecycle_action: Mapped[str] = mapped_column(String(64), nullable=False)
        lifecycle_reason: Mapped[str] = mapped_column(Text, nullable=False)
        source_review_aggregation_run_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("model_review_aggregation_run.review_aggregation_run_id"),
            nullable=False,
        )
        source_material_pack_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("analysis_material_pack.material_pack_id"),
            nullable=False,
        )
        source_strategy_signal_run_id: Mapped[str | None] = mapped_column(
            String(128),
            ForeignKey("strategy_signal_run.run_id"),
            nullable=True,
        )
        source_snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        model_review_invoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        model_review_invocation_mode: Mapped[str] = mapped_column(String(64), nullable=False)
        model_review_reused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        reused_model_analysis_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        model_review_basis: Mapped[str] = mapped_column(String(96), nullable=False)
        model_review_expired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        model_review_chain_status: Mapped[str] = mapped_column(String(32), nullable=False)
        notification_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
        notification_level: Mapped[str] = mapped_column(String(32), nullable=False)
        notification_reason: Mapped[str] = mapped_column(Text, nullable=False)
        notification_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    class StrategyAdviceEvent(Base):
        """ORM mapping for one strategy advice lifecycle event."""

        __tablename__ = "strategy_advice_event"
        __table_args__ = (
            UniqueConstraint("event_id", name="uq_strategy_advice_event_id"),
            Index("idx_strategy_advice_event_advice", "advice_id"),
            Index("idx_strategy_advice_event_review", "related_review_id"),
            Index("idx_strategy_advice_event_type_created", "event_type", "created_at_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        event_id: Mapped[str] = mapped_column(String(160), nullable=False)
        advice_id: Mapped[str | None] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=True,
        )
        related_review_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("strategy_advice_lifecycle_review.review_id"),
            nullable=False,
        )
        event_type: Mapped[str] = mapped_column(String(64), nullable=False)
        event_reason: Mapped[str] = mapped_column(Text, nullable=False)
        event_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    class StrategyAdviceTradeSetup(Base):
        """ORM mapping for one conditional setup under a strategy advice row."""

        __tablename__ = "strategy_advice_trade_setup"
        __table_args__ = (
            UniqueConstraint("setup_id", name="uq_strategy_advice_trade_setup_id"),
            UniqueConstraint("advice_id", "setup_rank", name="uk_strategy_advice_trade_setup_rank"),
            Index("idx_strategy_advice_trade_setup_advice", "advice_id"),
            Index("idx_strategy_advice_trade_setup_status", "status"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        setup_id: Mapped[str] = mapped_column(String(160), nullable=False)
        advice_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("strategy_advice.advice_id"),
            nullable=False,
        )
        setup_rank: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
        setup_type: Mapped[str] = mapped_column(String(96), nullable=False)
        side: Mapped[str] = mapped_column(String(32), nullable=False)
        entry_zone_json: Mapped[str] = mapped_column(Text, nullable=False)
        trigger_condition_json: Mapped[str] = mapped_column(Text, nullable=False)
        invalid_condition_json: Mapped[str] = mapped_column(Text, nullable=False)
        stop_loss_json: Mapped[str] = mapped_column(Text, nullable=False)
        target_zones_json: Mapped[str] = mapped_column(Text, nullable=False)
        expiry_base_bars: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        permission: Mapped[str] = mapped_column(String(64), nullable=False)
        source_strategy_names_json: Mapped[str] = mapped_column(Text, nullable=False)
        source_model_keys_json: Mapped[str] = mapped_column(Text, nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    class StrategyAdviceSchedulerEventLog(Base):
        """ORM mapping for one stage-21C scheduler task audit event."""

        __tablename__ = "strategy_advice_scheduler_event_log"
        __table_args__ = (
            UniqueConstraint("event_id", name="uq_strategy_advice_scheduler_event_id"),
            Index("idx_strategy_advice_scheduler_job_created", "job_name", "created_at_utc"),
            Index("idx_strategy_advice_scheduler_mrag", "review_aggregation_run_id"),
            Index("idx_strategy_advice_scheduler_status", "status", "created_at_utc"),
            Index("idx_strategy_advice_scheduler_trace", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        event_id: Mapped[str] = mapped_column(String(160), nullable=False)
        job_name: Mapped[str] = mapped_column(String(96), nullable=False)
        symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
        base_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
        higher_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
        review_aggregation_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        reason: Mapped[str] = mapped_column(Text, nullable=False)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        finished_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        details_json: Mapped[str] = mapped_column(Text, nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class StrategyAdvice:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        advice_id: str = ""
        advice_code: str = ""
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        parent_advice_id: str | None = None
        root_advice_id: str = ""
        previous_advice_id: str | None = None
        advice_path: str = ""
        version_no: int = 1
        advice_status: str = ""
        advice_action: str = ""
        directional_bias: str = ""
        trade_permission: str = ""
        source_review_aggregation_run_id: str = ""
        source_material_pack_id: str = ""
        source_strategy_signal_run_id: str | None = None
        source_snapshot_id: str | None = None
        source_model_chain_id: str | None = None
        model_review_invoked: bool = False
        model_review_invocation_mode: str = "none"
        model_review_reused: bool = False
        reused_model_analysis_run_id: str | None = None
        model_review_basis: str = ""
        model_review_expired: bool = False
        model_review_chain_status: str = "not_started"
        latest_model_review_at_utc: datetime | None = None
        model_review_status_summary_json: str = "{}"
        summary_text: str = ""
        risk_summary_json: str = "{}"
        strategy_summary_json: str = "{}"
        model_summary_json: str = "{}"
        is_trading_signal: bool = False
        is_executable: bool = False
        auto_trading_allowed: bool = False
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None
        closed_at_utc: datetime | None = None


    @dataclass
    class StrategyAdviceLifecycleReview:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        review_id: str = ""
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        reviewed_advice_id: str | None = None
        result_advice_id: str | None = None
        previous_advice_id: str | None = None
        lifecycle_action: str = ""
        lifecycle_reason: str = ""
        source_review_aggregation_run_id: str = ""
        source_material_pack_id: str = ""
        source_strategy_signal_run_id: str | None = None
        source_snapshot_id: str | None = None
        model_review_invoked: bool = False
        model_review_invocation_mode: str = "none"
        model_review_reused: bool = False
        reused_model_analysis_run_id: str | None = None
        model_review_basis: str = ""
        model_review_expired: bool = False
        model_review_chain_status: str = "not_started"
        notification_required: bool = True
        notification_level: str = "brief"
        notification_reason: str = ""
        notification_payload_json: str = "{}"
        created_at_utc: datetime | None = None


    @dataclass
    class StrategyAdviceEvent:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        event_id: str = ""
        advice_id: str | None = None
        related_review_id: str = ""
        event_type: str = ""
        event_reason: str = ""
        event_payload_json: str = "{}"
        created_at_utc: datetime | None = None


    @dataclass
    class StrategyAdviceTradeSetup:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        setup_id: str = ""
        advice_id: str = ""
        setup_rank: int = 1
        setup_type: str = ""
        side: str = ""
        entry_zone_json: str = "{}"
        trigger_condition_json: str = "{}"
        invalid_condition_json: str = "{}"
        stop_loss_json: str = "{}"
        target_zones_json: str = "[]"
        expiry_base_bars: int | None = None
        permission: str = ""
        source_strategy_names_json: str = "[]"
        source_model_keys_json: str = "[]"
        status: str = ""
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


    @dataclass
    class StrategyAdviceSchedulerEventLog:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        event_id: str = ""
        job_name: str = ""
        symbol: str | None = None
        base_interval: str | None = None
        higher_interval: str | None = None
        review_aggregation_run_id: str | None = None
        trigger_source: str = ""
        status: str = ""
        reason: str = ""
        trace_id: str = ""
        started_at_utc: datetime | None = None
        finished_at_utc: datetime | None = None
        details_json: str = "{}"
        created_at_utc: datetime | None = None


__all__ = [
    "StrategyAdvice",
    "StrategyAdviceEvent",
    "StrategyAdviceLifecycleReview",
    "StrategyAdviceSchedulerEventLog",
    "StrategyAdviceTradeSetup",
]
