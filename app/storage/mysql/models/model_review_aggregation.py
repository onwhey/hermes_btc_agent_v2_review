"""SQLAlchemy models for stage-20A model review aggregation.

This file belongs to `app/storage/mysql/models`. It defines only the
`model_review_aggregation_run` metadata for the stage-20A aggregation output.
It is called by Alembic metadata, the stage-20A repository, and tests.
External services: none. MySQL: metadata only at import time. Redis: none.
Hermes: none. Large-model calls: none. Trading execution: none.
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

    class ModelReviewAggregationRun(Base):
        """ORM mapping for one stage-20A review aggregation result.

        Parameters: values are supplied by `app/model_review_aggregation/repository.py`.
        Return value: SQLAlchemy ORM row.
        Failure scenarios: SQLAlchemy raises mapping/database errors when used.
        External services: none at class definition time.
        Data impact: stores compact aggregation status and summary only; the
        boundary flags are fixed false and this table is not a trading signal.
        """

        __tablename__ = "model_review_aggregation_run"
        __table_args__ = (
            UniqueConstraint("review_aggregation_run_id", name="uq_model_review_aggregation_run_id"),
            Index("idx_model_review_aggregation_material_pack", "material_pack_id"),
            Index("idx_model_review_aggregation_stage18", "aggregation_run_id"),
            Index("idx_model_review_aggregation_strategy_signal", "strategy_signal_run_id"),
            Index("idx_model_review_aggregation_status_created", "status", "created_at_utc"),
            Index("idx_model_review_aggregation_trace_id", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        review_aggregation_run_id: Mapped[str] = mapped_column(String(160), nullable=False)
        material_pack_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("analysis_material_pack.material_pack_id"),
            nullable=False,
        )
        aggregation_run_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("strategy_aggregation_run.aggregation_run_id"),
            nullable=False,
        )
        strategy_signal_run_id: Mapped[str] = mapped_column(
            String(128),
            ForeignKey("strategy_signal_run.run_id"),
            nullable=False,
        )
        snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        created_by: Mapped[str] = mapped_column(String(64), nullable=False)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        input_model_run_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        input_model_result_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        accepted_model_result_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        failed_model_result_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        blocked_model_result_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        skipped_model_result_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        aggregation_mode: Mapped[str] = mapped_column(String(32), nullable=False)
        model_review_invoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        model_review_invocation_mode: Mapped[str] = mapped_column(String(32), nullable=False)
        model_review_reused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        reused_model_analysis_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        reused_model_review_created_at_utc: Mapped[datetime | None] = mapped_column(
            DateTime(timezone=True),
            nullable=True,
        )
        model_review_skip_reason: Mapped[str] = mapped_column(Text, nullable=False)
        model_review_block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
        invoked_model_keys_json: Mapped[str] = mapped_column(Text, nullable=False)
        invoked_model_roles_json: Mapped[str] = mapped_column(Text, nullable=False)
        model_review_chain_status: Mapped[str] = mapped_column(String(32), nullable=False)
        model_review_partial_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
        latest_model_review_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        model_review_basis: Mapped[str] = mapped_column(String(64), nullable=False)
        model_review_reuse_status: Mapped[str] = mapped_column(String(64), nullable=False)
        model_review_reuse_base_bars: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        model_review_reuse_max_base_bars: Mapped[int] = mapped_column(BigInteger, nullable=False, default=3)
        model_review_expired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        review_input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
        review_input_fingerprint_version: Mapped[str] = mapped_column(String(64), nullable=False)
        review_decision_summary: Mapped[str] = mapped_column(String(160), nullable=False)
        evidence_quality_summary: Mapped[str] = mapped_column(String(160), nullable=False)
        risk_acceptability_summary: Mapped[str] = mapped_column(String(160), nullable=False)
        strategy_conflict_summary: Mapped[str] = mapped_column(String(160), nullable=False)
        model_consensus_level: Mapped[str] = mapped_column(String(32), nullable=False)
        allowed_advice_mode: Mapped[str] = mapped_column(String(32), nullable=False)
        directional_trade_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        model_results_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
        model_disagreement_json: Mapped[str] = mapped_column(Text, nullable=False)
        risk_warnings_json: Mapped[str] = mapped_column(Text, nullable=False)
        missing_evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
        human_review_questions_json: Mapped[str] = mapped_column(Text, nullable=False)
        summary_text: Mapped[str] = mapped_column(Text, nullable=False)
        is_final_trading_advice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        is_trading_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        is_executable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        auto_trading_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class ModelReviewAggregationRun:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        review_aggregation_run_id: str = ""
        material_pack_id: str = ""
        aggregation_run_id: str = ""
        strategy_signal_run_id: str = ""
        snapshot_id: str = ""
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        status: str = ""
        trigger_source: str = ""
        created_by: str = ""
        trace_id: str = ""
        input_model_run_count: int = 0
        input_model_result_count: int = 0
        accepted_model_result_count: int = 0
        failed_model_result_count: int = 0
        blocked_model_result_count: int = 0
        skipped_model_result_count: int = 0
        aggregation_mode: str = "single_or_reuse"
        model_review_invoked: bool = False
        model_review_invocation_mode: str = "none"
        model_review_reused: bool = False
        reused_model_analysis_run_id: str | None = None
        reused_model_review_created_at_utc: datetime | None = None
        model_review_skip_reason: str = ""
        model_review_block_reason: str | None = None
        invoked_model_keys_json: str = "[]"
        invoked_model_roles_json: str = "[]"
        model_review_chain_status: str = "not_started"
        model_review_partial_failure_reason: str | None = None
        latest_model_review_at_utc: datetime | None = None
        model_review_basis: str = "material_only"
        model_review_reuse_status: str = "not_reused"
        model_review_reuse_base_bars: int | None = None
        model_review_reuse_max_base_bars: int = 3
        model_review_expired: bool = False
        review_input_fingerprint: str = ""
        review_input_fingerprint_version: str = ""
        review_decision_summary: str = ""
        evidence_quality_summary: str = ""
        risk_acceptability_summary: str = ""
        strategy_conflict_summary: str = ""
        model_consensus_level: str = ""
        allowed_advice_mode: str = "wait_only"
        directional_trade_allowed: bool = False
        model_results_summary_json: str = "{}"
        model_disagreement_json: str = "{}"
        risk_warnings_json: str = "[]"
        missing_evidence_json: str = "[]"
        human_review_questions_json: str = "[]"
        summary_text: str = ""
        is_final_trading_advice: bool = False
        is_trading_signal: bool = False
        is_executable: bool = False
        auto_trading_allowed: bool = False
        error_code: str | None = None
        error_message: str | None = None
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


__all__ = ["ModelReviewAggregationRun"]
