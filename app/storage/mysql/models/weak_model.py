"""SQLAlchemy models for 27A/27B weak model / factor layer.

本文件属于 `app/storage/mysql/models` 存储层，负责定义 `weak_model_run`、
`weak_model_result`、`weak_model_aggregation` 和 `weak_model_quality_check` 表。
本文件不负责运行弱模型，不请求 Binance，不发送 Hermes，不读写 Redis，
不调用 DeepSeek/GPT/Claude，不读取账户或仓位，不生成订单，不自动交易。
数据库影响：仅定义 ORM metadata；实际写入由 27A repository 在 caller-owned
session 中完成，27B 只在 confirm-write 时写入质量检查结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.storage.mysql.base import Base

try:
    from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = Boolean = DateTime = ForeignKey = Index = Numeric = String = Text = UniqueConstraint = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class WeakModelRun(Base):
        """ORM mapping for one weak model batch run."""

        __tablename__ = "weak_model_run"
        __table_args__ = (
            UniqueConstraint("weak_model_run_id", name="uq_weak_model_run_id"),
            Index("idx_weak_model_run_ssr", "strategy_signal_run_id"),
            Index("idx_weak_model_run_snapshot", "snapshot_id"),
            Index("idx_weak_model_run_scope_slot", "symbol", "base_interval", "higher_interval", "kline_slot_utc"),
            Index("idx_weak_model_run_status_created", "run_status", "created_at_utc"),
            Index("idx_weak_model_run_trace", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        weak_model_run_id: Mapped[str] = mapped_column(String(180), nullable=False)
        pipeline_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        strategy_signal_run_id: Mapped[str] = mapped_column(
            String(128),
            ForeignKey("strategy_signal_run.run_id"),
            nullable=False,
        )
        snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        kline_slot_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        run_status: Mapped[str] = mapped_column(String(32), nullable=False)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        model_count_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        model_count_enabled: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        model_count_executed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        model_count_failed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        details_json: Mapped[str] = mapped_column(Text, nullable=False)


    class WeakModelResult(Base):
        """ORM mapping for one weak model result row."""

        __tablename__ = "weak_model_result"
        __table_args__ = (
            UniqueConstraint("weak_model_result_id", name="uq_weak_model_result_id"),
            UniqueConstraint("weak_model_run_id", "model_key", name="uq_weak_model_result_run_model"),
            Index("idx_weak_model_result_run", "weak_model_run_id"),
            Index("idx_weak_model_result_model_role", "model_key", "model_role"),
            Index("idx_weak_model_result_status", "status"),
            Index("idx_weak_model_result_scope_slot", "symbol", "base_interval", "higher_interval", "kline_slot_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        weak_model_result_id: Mapped[str] = mapped_column(String(180), nullable=False)
        weak_model_run_id: Mapped[str] = mapped_column(
            String(180),
            ForeignKey("weak_model_run.weak_model_run_id"),
            nullable=False,
        )
        model_key: Mapped[str] = mapped_column(String(128), nullable=False)
        model_role: Mapped[str] = mapped_column(String(32), nullable=False)
        model_version: Mapped[str] = mapped_column(String(64), nullable=False)
        config_version: Mapped[str] = mapped_column(String(64), nullable=False)
        config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
        maturity_stage: Mapped[str] = mapped_column(String(32), nullable=False)
        enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
        participation_mode: Mapped[str] = mapped_column(String(32), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        kline_slot_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        signal_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
        direction_bias: Mapped[str | None] = mapped_column(String(32), nullable=True)
        risk_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
        risk_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
        trade_permission: Mapped[str | None] = mapped_column(String(32), nullable=True)
        veto_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        confirmation_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
        supports_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
        context_regime: Mapped[str | None] = mapped_column(String(64), nullable=True)
        context_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
        confidence: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=0)
        static_weight: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=0)
        effective_score: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=0)
        input_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
        evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
        raw_output_json: Mapped[str] = mapped_column(Text, nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    class WeakModelAggregation(Base):
        """ORM mapping for one weak model aggregation summary."""

        __tablename__ = "weak_model_aggregation"
        __table_args__ = (
            UniqueConstraint("weak_model_aggregation_id", name="uq_weak_model_aggregation_id"),
            UniqueConstraint("weak_model_run_id", name="uq_weak_model_aggregation_run"),
            Index("idx_weak_model_aggregation_ssr", "strategy_signal_run_id"),
            Index("idx_weak_model_aggregation_snapshot", "snapshot_id"),
            Index("idx_weak_model_aggregation_scope_slot", "symbol", "base_interval", "higher_interval", "kline_slot_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        weak_model_aggregation_id: Mapped[str] = mapped_column(String(180), nullable=False)
        weak_model_run_id: Mapped[str] = mapped_column(
            String(180),
            ForeignKey("weak_model_run.weak_model_run_id"),
            nullable=False,
        )
        pipeline_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        strategy_signal_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
        snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        kline_slot_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        directional_score: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=0)
        directional_bias: Mapped[str] = mapped_column(String(32), nullable=False)
        directional_confidence: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=0)
        risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
        trade_permission: Mapped[str] = mapped_column(String(32), nullable=False)
        veto_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        supporting_factors_json: Mapped[str] = mapped_column(Text, nullable=False)
        opposing_factors_json: Mapped[str] = mapped_column(Text, nullable=False)
        conflict_factors_json: Mapped[str] = mapped_column(Text, nullable=False)
        low_confidence_factors_json: Mapped[str] = mapped_column(Text, nullable=False)
        veto_factors_json: Mapped[str] = mapped_column(Text, nullable=False)
        context_summary_json: Mapped[str] = mapped_column(Text, nullable=False)
        summary_text: Mapped[str] = mapped_column(Text, nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        details_json: Mapped[str] = mapped_column(Text, nullable=False)


    class WeakModelQualityCheck(Base):
        """ORM mapping for one 27B output quality check result."""

        __tablename__ = "weak_model_quality_check"
        __table_args__ = (
            UniqueConstraint("quality_check_id", name="uq_weak_model_quality_check_id"),
            UniqueConstraint("weak_model_run_id", name="uq_weak_model_quality_check_run"),
            Index("idx_weak_model_quality_check_aggregation", "weak_model_aggregation_id"),
            Index("idx_weak_model_quality_check_scope_slot", "symbol", "base_interval", "higher_interval", "kline_slot_utc"),
            Index("idx_weak_model_quality_check_status", "status", "severity", "created_at_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        quality_check_id: Mapped[str] = mapped_column(String(180), nullable=False)
        weak_model_run_id: Mapped[str] = mapped_column(
            String(180),
            ForeignKey("weak_model_run.weak_model_run_id"),
            nullable=False,
        )
        weak_model_aggregation_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
        strategy_signal_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
        snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        kline_slot_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        severity: Mapped[str] = mapped_column(String(32), nullable=False)
        issue_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        warning_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        critical_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        should_block_pipeline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        issues_json: Mapped[str] = mapped_column(Text, nullable=False)
        checked_models_json: Mapped[str] = mapped_column(Text, nullable=False)
        summary_text: Mapped[str] = mapped_column(Text, nullable=False)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        details_json: Mapped[str] = mapped_column(Text, nullable=False)

else:

    @dataclass
    class WeakModelRun:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        weak_model_run_id: str = ""
        pipeline_run_id: str | None = None
        strategy_signal_run_id: str = ""
        snapshot_id: str | None = None
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        kline_slot_utc: datetime | None = None
        run_status: str = ""
        trigger_source: str = ""
        model_count_total: int = 0
        model_count_enabled: int = 0
        model_count_executed: int = 0
        model_count_failed: int = 0
        trace_id: str = ""
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None
        details_json: str = "{}"


    @dataclass
    class WeakModelResult:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        weak_model_result_id: str = ""
        weak_model_run_id: str = ""
        model_key: str = ""
        model_role: str = ""
        model_version: str = ""
        config_version: str = ""
        config_hash: str = ""
        maturity_stage: str = ""
        enabled: bool = True
        participation_mode: str = ""
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        kline_slot_utc: datetime | None = None
        snapshot_id: str = ""
        status: str = ""
        error_code: str | None = None
        error_message: str | None = None
        created_at_utc: datetime | None = None


    @dataclass
    class WeakModelAggregation:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        weak_model_aggregation_id: str = ""
        weak_model_run_id: str = ""
        strategy_signal_run_id: str = ""
        snapshot_id: str = ""
        veto_factors_json: str = "[]"


    @dataclass
    class WeakModelQualityCheck:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        quality_check_id: str = ""
        weak_model_run_id: str = ""
        weak_model_aggregation_id: str | None = None
        status: str = ""
        severity: str = ""
        issues_json: str = "[]"


__all__ = ["WeakModelAggregation", "WeakModelQualityCheck", "WeakModelResult", "WeakModelRun"]
