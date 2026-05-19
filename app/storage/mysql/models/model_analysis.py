"""SQLAlchemy models for stage-19 model analysis review-gate persistence.

This file belongs to `app/storage/mysql/models`. It defines only
`model_analysis_run` attempt rows and `model_analysis_result` final rows for
the stage-19A review gate.

Called by Alembic metadata, the stage-19 repository, and tests.
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

    class ModelAnalysisRun(Base):
        """ORM mapping for one stage-19 model review attempt.

        Parameters: values are supplied by `app/model_analysis/repository.py`.
        Return value: SQLAlchemy ORM row.
        Failure scenarios: SQLAlchemy raises mapping/database errors when used.
        External services: none at class definition time.
        Data impact: attempt rows may record blocked/failed/success attempts,
        but this table has no `review_version_key` unique constraint so failed
        attempts cannot permanently lock a later rerun.
        """

        __tablename__ = "model_analysis_run"
        __table_args__ = (
            UniqueConstraint("model_analysis_run_id", name="uq_model_analysis_run_id"),
            Index("idx_model_analysis_run_material_pack", "material_pack_id"),
            Index("idx_model_analysis_run_aggregation", "aggregation_run_id"),
            Index("idx_model_analysis_run_strategy_signal", "strategy_signal_run_id"),
            Index("idx_model_analysis_run_review_version_key", "review_version_key"),
            Index("idx_model_analysis_run_status_created", "status", "created_at_utc"),
            Index("idx_model_analysis_run_trace_id", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        model_analysis_run_id: Mapped[str] = mapped_column(String(160), nullable=False)
        review_version_key: Mapped[str] = mapped_column(String(64), nullable=False)
        material_pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
        aggregation_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        strategy_signal_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
        base_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
        higher_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
        review_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
        prompt_template_version: Mapped[str] = mapped_column(String(64), nullable=False)
        model_provider: Mapped[str] = mapped_column(String(32), nullable=False)
        model_name: Mapped[str] = mapped_column(String(96), nullable=False)
        model_version: Mapped[str] = mapped_column(String(96), nullable=False)
        review_mode: Mapped[str] = mapped_column(String(32), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        input_material_hash: Mapped[str] = mapped_column(String(64), nullable=False)
        input_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
        input_char_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        input_byte_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        output_char_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        output_byte_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        is_final_trading_advice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        is_trading_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        is_executable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        auto_trading_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        human_review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        created_by: Mapped[str] = mapped_column(String(64), nullable=False)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        hermes_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        hermes_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
        hermes_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        hermes_error: Mapped[str | None] = mapped_column(Text, nullable=True)
        hermes_sent_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    class ModelAnalysisResult(Base):
        """ORM mapping for one final stage-19 review result.

        Final rows are written only after successful or partial successful
        schema-validated review. The single-column `review_version_key` unique
        constraint is the only version idempotency gate; blocked/failed attempts
        stay only in `model_analysis_run`.
        """

        __tablename__ = "model_analysis_result"
        __table_args__ = (
            UniqueConstraint("model_analysis_result_id", name="uq_model_analysis_result_id"),
            UniqueConstraint("review_version_key", name="uk_model_analysis_result_review_version_key"),
            Index("idx_model_analysis_result_run", "model_analysis_run_id"),
            Index("idx_model_analysis_result_material_pack", "material_pack_id"),
            Index("idx_model_analysis_result_aggregation", "aggregation_run_id"),
            Index("idx_model_analysis_result_strategy_signal", "strategy_signal_run_id"),
            Index("idx_model_analysis_result_created_at", "created_at_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        model_analysis_result_id: Mapped[str] = mapped_column(String(160), nullable=False)
        model_analysis_run_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("model_analysis_run.model_analysis_run_id"),
            nullable=False,
        )
        review_version_key: Mapped[str] = mapped_column(String(64), nullable=False)
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
        review_decision: Mapped[str] = mapped_column(String(64), nullable=False)
        evidence_quality: Mapped[str] = mapped_column(String(32), nullable=False)
        logic_consistency: Mapped[str] = mapped_column(String(32), nullable=False)
        risk_acceptability: Mapped[str] = mapped_column(String(32), nullable=False)
        strategy_conflict_level: Mapped[str] = mapped_column(String(32), nullable=False)
        missing_evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
        rejection_reasons_json: Mapped[str] = mapped_column(Text, nullable=False)
        risk_warnings_json: Mapped[str] = mapped_column(Text, nullable=False)
        conditions_to_reconsider_json: Mapped[str] = mapped_column(Text, nullable=False)
        validation_focus_json: Mapped[str] = mapped_column(Text, nullable=False)
        human_review_questions_json: Mapped[str] = mapped_column(Text, nullable=False)
        summary_text: Mapped[str] = mapped_column(Text, nullable=False)
        not_trading_advice_text: Mapped[str] = mapped_column(Text, nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class ModelAnalysisRun:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        model_analysis_run_id: str = ""
        review_version_key: str = ""
        material_pack_id: str = ""
        aggregation_run_id: str = ""
        strategy_signal_run_id: str = ""
        snapshot_id: str = ""
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        review_schema_version: str = ""
        prompt_template_version: str = ""
        model_provider: str = "mock"
        model_name: str = ""
        model_version: str = ""
        review_mode: str = "review_gate"
        status: str = ""
        input_material_hash: str = ""
        input_summary_json: str = "{}"
        input_char_count: int = 0
        input_byte_count: int = 0
        output_char_count: int = 0
        output_byte_count: int = 0
        is_final_trading_advice: bool = False
        is_trading_signal: bool = False
        is_executable: bool = False
        auto_trading_allowed: bool = False
        human_review_required: bool = True
        trigger_source: str = ""
        created_by: str = ""
        trace_id: str = ""
        error_code: str | None = None
        error_message: str | None = None
        hermes_enabled: bool = False
        hermes_status: str | None = None
        hermes_message: str | None = None
        hermes_error: str | None = None
        hermes_sent_at_utc: datetime | None = None
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


    @dataclass
    class ModelAnalysisResult:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        model_analysis_result_id: str = ""
        model_analysis_run_id: str = ""
        review_version_key: str = ""
        material_pack_id: str = ""
        aggregation_run_id: str = ""
        strategy_signal_run_id: str = ""
        review_decision: str = ""
        evidence_quality: str = ""
        logic_consistency: str = ""
        risk_acceptability: str = ""
        strategy_conflict_level: str = ""
        missing_evidence_json: str = "[]"
        rejection_reasons_json: str = "[]"
        risk_warnings_json: str = "[]"
        conditions_to_reconsider_json: str = "[]"
        validation_focus_json: str = "[]"
        human_review_questions_json: str = "[]"
        summary_text: str = ""
        not_trading_advice_text: str = ""
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


__all__ = ["ModelAnalysisRun", "ModelAnalysisResult"]
