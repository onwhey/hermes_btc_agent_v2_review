"""SQLAlchemy model for 26C strategy pipeline observation index.

本文件属于 `app/storage/mysql/models` 存储层。
本文件负责定义 `strategy_pipeline_observation` 表结构，供 Alembic
metadata、26C repository 和测试导入。
本文件不负责运行 pipeline，不负责复盘分析，不请求 Binance，不发送 Hermes，
不读写 Redis，不调用 DeepSeek 或其他大模型，不读取账户或仓位，不生成订单，
不自动交易。
数据库影响：仅定义 ORM metadata；实际写入由 26C repository 在 caller-owned
session 中完成。
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

    class StrategyPipelineObservation(Base):
        """ORM mapping for one 26C observation row per formal 4h Kline slot.

        Parameters: values are supplied by
        `app/strategy_pipeline_observation/repository.py`.
        Return value: SQLAlchemy ORM row.
        Failure scenarios: SQLAlchemy raises mapping/database errors when used.
        External service access: none at class definition time.
        Data impact: stores only compact observation identifiers and status
        summaries. It does not store full Kline windows, strategy payloads,
        model prompts, model responses, account data, or trading execution data.
        """

        __tablename__ = "strategy_pipeline_observation"
        __table_args__ = (
            UniqueConstraint("observation_id", name="uq_strategy_pipeline_observation_id"),
            UniqueConstraint(
                "symbol",
                "base_interval",
                "higher_interval",
                "kline_slot_utc",
                name="uq_strategy_pipeline_observation_scope_slot",
            ),
            Index("idx_strategy_pipeline_observation_status", "observation_status", "updated_at_utc"),
            Index("idx_strategy_pipeline_observation_canonical", "canonical_pipeline_run_id"),
            Index("idx_strategy_pipeline_observation_eqc", "evidence_quality_check_id"),
            Index("idx_strategy_pipeline_observation_review", "review_aggregation_run_id"),
            Index("idx_strategy_pipeline_observation_advice", "advice_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        observation_id: Mapped[str] = mapped_column(String(180), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        kline_slot_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        kline_open_time_prc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        kline_close_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        kline_close_time_prc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

        canonical_pipeline_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        canonical_trigger_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
        canonical_reason: Mapped[str] = mapped_column(String(160), nullable=False)
        duplicate_pipeline_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        excluded_pipeline_run_ids_json: Mapped[str] = mapped_column(Text, nullable=False)

        observation_status: Mapped[str] = mapped_column(String(64), nullable=False)
        eligible_for_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        eligible_for_advice_performance_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

        pipeline_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
        pipeline_current_step: Mapped[str | None] = mapped_column(String(96), nullable=True)
        pipeline_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        pipeline_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

        strategy_signal_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        strategy_evidence_aggregation_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        evidence_quality_check_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        material_pack_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        model_analysis_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        review_aggregation_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        advice_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        review_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        alert_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

        evidence_quality_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
        evidence_quality_should_block: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        evidence_quality_failed_roles_json: Mapped[str] = mapped_column(Text, nullable=False)
        evidence_quality_failed_strategies_json: Mapped[str] = mapped_column(Text, nullable=False)

        model_review_invoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        model_review_reused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        real_model_called: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        real_model_blocked_by_config: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

        hermes_real_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        notification_status: Mapped[str | None] = mapped_column(String(64), nullable=True)

        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        details_json: Mapped[str] = mapped_column(Text, nullable=False)

else:

    @dataclass
    class StrategyPipelineObservation:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        observation_id: str = ""
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        kline_slot_utc: datetime | None = None
        kline_open_time_prc: datetime | None = None
        kline_close_time_utc: datetime | None = None
        kline_close_time_prc: datetime | None = None
        canonical_pipeline_run_id: str | None = None
        canonical_trigger_source: str | None = None
        canonical_reason: str = ""
        duplicate_pipeline_count: int = 0
        excluded_pipeline_run_ids_json: str = "[]"
        observation_status: str = ""
        eligible_for_review: bool = False
        eligible_for_advice_performance_review: bool = False
        pipeline_status: str | None = None
        pipeline_current_step: str | None = None
        pipeline_error_code: str | None = None
        pipeline_error_message: str | None = None
        strategy_signal_run_id: str | None = None
        strategy_evidence_aggregation_id: str | None = None
        evidence_quality_check_id: str | None = None
        material_pack_id: str | None = None
        model_analysis_run_id: str | None = None
        review_aggregation_run_id: str | None = None
        advice_id: str | None = None
        review_id: str | None = None
        alert_message_id: int | None = None
        evidence_quality_status: str | None = None
        evidence_quality_should_block: bool = False
        evidence_quality_failed_roles_json: str = "[]"
        evidence_quality_failed_strategies_json: str = "[]"
        model_review_invoked: bool = False
        model_review_reused: bool = False
        real_model_called: bool = False
        real_model_blocked_by_config: bool = False
        hermes_real_sent: bool = False
        notification_status: str | None = None
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None
        details_json: str = "{}"


__all__ = ["StrategyPipelineObservation"]
