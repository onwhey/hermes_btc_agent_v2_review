"""SQLAlchemy models for stage-18 strategy aggregation persistence.

This file belongs to `app/storage/mysql/models`. It defines only
`strategy_aggregation_run` and `analysis_material_pack` metadata.
It is called by Alembic metadata, the stage-18 repository, and tests.
It does not request Binance, read/write Redis, send Hermes, call DeepSeek or
any large language model, generate final trading advice, modify formal Kline
tables, read private trading state, or perform trading.
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

    class StrategyAggregationRun(Base):
        """ORM mapping for one stage-18 aggregation run.

        Parameters: values are supplied by the stage-18 repository.
        Return value: SQLAlchemy ORM row.
        Failure scenarios: SQLAlchemy raises mapping or database errors when used.
        External service access: none at class definition time.
        Data impact: defines table metadata only; it never writes formal Kline
        tables and never stores final trading advice.
        """

        __tablename__ = "strategy_aggregation_run"
        __table_args__ = (
            UniqueConstraint("aggregation_run_id", name="uq_strategy_aggregation_run_id"),
            Index(
                "idx_strategy_aggregation_version_status",
                "strategy_signal_run_id",
                "aggregation_version",
                "material_schema_version",
                "indicator_version",
                "candidate_scenario_version",
                "status",
            ),
            Index("idx_strategy_aggregation_strategy_signal_run", "strategy_signal_run_id"),
            Index("idx_strategy_aggregation_snapshot_id", "snapshot_id"),
            Index("idx_strategy_aggregation_status_created", "status", "created_at_utc"),
            Index("idx_strategy_aggregation_hypothesis", "analysis_hypothesis_direction", "risk_gate_status"),
            Index("idx_strategy_aggregation_trace_id", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        aggregation_run_id: Mapped[str] = mapped_column(String(160), nullable=False)
        strategy_signal_run_id: Mapped[str] = mapped_column(
            String(128),
            ForeignKey("strategy_signal_run.run_id"),
            nullable=False,
        )
        snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        aggregation_version: Mapped[str] = mapped_column(String(64), nullable=False)
        material_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
        indicator_version: Mapped[str] = mapped_column(String(64), nullable=False)
        candidate_scenario_version: Mapped[str] = mapped_column(String(64), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        input_strategy_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        input_success_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        input_failed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        input_invalid_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        input_not_implemented_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        effective_strategy_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        analysis_hypothesis_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
        analysis_hypothesis_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
        analysis_hypothesis_semantics: Mapped[str] = mapped_column(
            String(64),
            nullable=False,
            default="analysis_hypothesis_only",
        )
        direction_projection_source: Mapped[str] = mapped_column(String(128), nullable=False)
        stop_trading_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
        risk_gate_projection_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
        is_strategy_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        is_trading_advice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        is_executable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        strategy_logic_implemented: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        promotion_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        promotion_requires_future_strategy_and_llm_stage: Mapped[bool] = mapped_column(
            Boolean,
            nullable=False,
            default=True,
        )
        risk_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
        risk_gate_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
        conflict_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
        direction_consensus: Mapped[str | None] = mapped_column(String(32), nullable=True)
        long_strategies_json: Mapped[str] = mapped_column(Text, nullable=False)
        short_strategies_json: Mapped[str] = mapped_column(Text, nullable=False)
        neutral_strategies_json: Mapped[str] = mapped_column(Text, nullable=False)
        supporting_strategies_json: Mapped[str] = mapped_column(Text, nullable=False)
        opposing_strategies_json: Mapped[str] = mapped_column(Text, nullable=False)
        risk_strategies_json: Mapped[str] = mapped_column(Text, nullable=False)
        not_implemented_strategies_json: Mapped[str] = mapped_column(Text, nullable=False)
        failed_strategies_json: Mapped[str] = mapped_column(Text, nullable=False)
        invalid_strategies_json: Mapped[str] = mapped_column(Text, nullable=False)
        candidate_scenarios_json: Mapped[str] = mapped_column(Text, nullable=False)
        summary_json: Mapped[str] = mapped_column(Text, nullable=False)
        evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
        conflict_json: Mapped[str] = mapped_column(Text, nullable=False)
        validation_plan_json: Mapped[str] = mapped_column(Text, nullable=False)
        message: Mapped[str | None] = mapped_column(Text, nullable=True)
        error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        created_by: Mapped[str] = mapped_column(String(64), nullable=False)
        hermes_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        hermes_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
        hermes_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        hermes_error: Mapped[str | None] = mapped_column(Text, nullable=True)
        hermes_sent_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    class AnalysisMaterialPack(Base):
        """ORM mapping for deterministic stage-18 analysis material packs."""

        __tablename__ = "analysis_material_pack"
        __table_args__ = (
            UniqueConstraint("material_pack_id", name="uq_analysis_material_pack_id"),
            UniqueConstraint("aggregation_run_id", name="uq_analysis_material_pack_aggregation_run_id"),
            UniqueConstraint(
                "strategy_signal_run_id",
                "aggregation_version",
                "material_schema_version",
                "indicator_version",
                "candidate_scenario_version",
                name="uk_analysis_material_pack_version",
            ),
            Index("idx_analysis_material_pack_strategy_signal_run", "strategy_signal_run_id"),
            Index("idx_analysis_material_pack_snapshot_id", "snapshot_id"),
            Index("idx_analysis_material_pack_status_created", "status", "created_at_utc"),
            Index("idx_analysis_material_pack_trace_id", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        material_pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
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
        aggregation_version: Mapped[str] = mapped_column(String(64), nullable=False)
        material_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
        indicator_version: Mapped[str] = mapped_column(String(64), nullable=False)
        candidate_scenario_version: Mapped[str] = mapped_column(String(64), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        material_json: Mapped[str] = mapped_column(Text, nullable=False)
        question_json: Mapped[str] = mapped_column(Text, nullable=False)
        validation_plan_json: Mapped[str] = mapped_column(Text, nullable=False)
        summary_json: Mapped[str] = mapped_column(Text, nullable=False)
        data_window_json: Mapped[str] = mapped_column(Text, nullable=False)
        future_leakage_guard_json: Mapped[str] = mapped_column(Text, nullable=False)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        created_by: Mapped[str] = mapped_column(String(64), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class StrategyAggregationRun:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        aggregation_run_id: str = ""
        strategy_signal_run_id: str = ""
        snapshot_id: str | None = None
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        aggregation_version: str = ""
        material_schema_version: str = ""
        indicator_version: str = ""
        candidate_scenario_version: str = ""
        status: str = ""
        input_strategy_count: int = 0
        input_success_count: int = 0
        input_failed_count: int = 0
        input_invalid_count: int = 0
        input_not_implemented_count: int = 0
        effective_strategy_count: int = 0
        analysis_hypothesis_direction: str | None = None
        analysis_hypothesis_confidence: str | None = None
        analysis_hypothesis_semantics: str = "analysis_hypothesis_only"
        direction_projection_source: str = "fixture_or_existing_signal_projection"
        stop_trading_source: str | None = None
        risk_gate_projection_source: str | None = None
        is_strategy_signal: bool = False
        is_trading_advice: bool = False
        is_executable: bool = False
        strategy_logic_implemented: bool = False
        promotion_allowed: bool = False
        promotion_requires_future_strategy_and_llm_stage: bool = True
        risk_level: str | None = None
        risk_gate_status: str | None = None
        conflict_level: str | None = None
        direction_consensus: str | None = None
        long_strategies_json: str = "{}"
        short_strategies_json: str = "{}"
        neutral_strategies_json: str = "{}"
        supporting_strategies_json: str = "{}"
        opposing_strategies_json: str = "{}"
        risk_strategies_json: str = "{}"
        not_implemented_strategies_json: str = "{}"
        failed_strategies_json: str = "{}"
        invalid_strategies_json: str = "{}"
        candidate_scenarios_json: str = "{}"
        summary_json: str = "{}"
        evidence_json: str = "{}"
        conflict_json: str = "{}"
        validation_plan_json: str = "{}"
        message: str | None = None
        error_code: str | None = None
        error_message: str | None = None
        trace_id: str = ""
        trigger_source: str = ""
        created_by: str = ""
        hermes_enabled: bool = False
        hermes_status: str | None = None
        hermes_message: str | None = None
        hermes_error: str | None = None
        hermes_sent_at_utc: datetime | None = None
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


    @dataclass
    class AnalysisMaterialPack:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        material_pack_id: str = ""
        aggregation_run_id: str = ""
        strategy_signal_run_id: str = ""
        snapshot_id: str = ""
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        aggregation_version: str = ""
        material_schema_version: str = ""
        indicator_version: str = ""
        candidate_scenario_version: str = ""
        status: str = ""
        material_json: str = "{}"
        question_json: str = "{}"
        validation_plan_json: str = "{}"
        summary_json: str = "{}"
        data_window_json: str = "{}"
        future_leakage_guard_json: str = "{}"
        trace_id: str = ""
        created_by: str = ""
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


__all__ = ["AnalysisMaterialPack", "StrategyAggregationRun"]
