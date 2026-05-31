"""SQLAlchemy model for 26B strategy evidence quality gate results.

本文件属于 `app/storage/mysql/models` 存储层。
本文件负责定义 `strategy_evidence_quality_check_result` 表结构，供 Alembic
metadata、26B repository 和测试导入。
本文件不负责执行质量判断，不负责发送 Hermes，不请求 Binance，不读写 Redis，
不调用 DeepSeek 或其他大模型，不读取账户或仓位，不生成订单，不自动交易。
数据库影响：仅定义 ORM metadata；实际写入由 26B repository 在 caller-owned
session 中完成。
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

    class StrategyEvidenceQualityCheckResult(Base):
        """ORM mapping for one 26B quality gate result.

        Parameters: fields are supplied by
        `app/strategy/evidence_quality/repository.py`.
        Return value: SQLAlchemy ORM row.
        Failure scenarios: SQLAlchemy raises mapping/database errors when used.
        External service access: none at class definition time.
        Data impact: defines compact quality facts and summaries only; it does
        not store full strategy payloads, Kline windows, model prompts, model
        responses, account data, or trading execution data.
        """

        __tablename__ = "strategy_evidence_quality_check_result"
        __table_args__ = (
            UniqueConstraint("quality_check_id", name="uq_strategy_evidence_quality_check_id"),
            UniqueConstraint(
                "pipeline_run_id",
                "trigger_source",
                name="uq_strategy_evidence_quality_pipeline_trigger",
            ),
            Index("idx_strategy_evidence_quality_pipeline", "pipeline_run_id"),
            Index("idx_strategy_evidence_quality_signal", "strategy_signal_run_id"),
            Index("idx_strategy_evidence_quality_status_created", "status", "created_at_utc"),
            Index("idx_strategy_evidence_quality_alert", "alert_status"),
            Index("idx_strategy_evidence_quality_trace_id", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        quality_check_id: Mapped[str] = mapped_column(String(160), nullable=False)
        pipeline_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
        strategy_signal_run_id: Mapped[str] = mapped_column(
            String(128),
            ForeignKey("strategy_signal_run.run_id"),
            nullable=False,
        )
        evidence_aggregation_id: Mapped[str] = mapped_column(
            String(160),
            ForeignKey("strategy_evidence_aggregation_result.aggregation_id"),
            nullable=False,
        )
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        kline_slot_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        severity: Mapped[str] = mapped_column(String(32), nullable=False)
        should_block_pipeline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        failed_checks_json: Mapped[str] = mapped_column(Text, nullable=False)
        warning_checks_json: Mapped[str] = mapped_column(Text, nullable=False)
        strategy_quality_json: Mapped[str] = mapped_column(Text, nullable=False)
        role_quality_json: Mapped[str] = mapped_column(Text, nullable=False)
        config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
        alert_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        alert_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_required")
        alert_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        not_trading_advice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class StrategyEvidenceQualityCheckResult:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        quality_check_id: str = ""
        pipeline_run_id: str | None = None
        strategy_signal_run_id: str = ""
        evidence_aggregation_id: str = ""
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        kline_slot_utc: datetime | None = None
        status: str = ""
        severity: str = ""
        should_block_pipeline: bool = False
        error_code: str | None = None
        error_message: str | None = None
        failed_checks_json: str = "[]"
        warning_checks_json: str = "[]"
        strategy_quality_json: str = "{}"
        role_quality_json: str = "{}"
        config_snapshot_json: str = "{}"
        alert_required: bool = False
        alert_status: str = "not_required"
        alert_message_id: int | None = None
        not_trading_advice: bool = True
        trigger_source: str = ""
        trace_id: str = ""
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


__all__ = ["StrategyEvidenceQualityCheckResult"]
