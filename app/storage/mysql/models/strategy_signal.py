"""SQLAlchemy models for stage-16 strategy signal persistence.

This file belongs to `app/storage/mysql/models`.
It defines only `strategy_signal_run` and `strategy_signal_result` metadata.
It is called by Alembic metadata, the strategy result repository, and tests.
It does not request Binance, read/write Redis, send Hermes, call DeepSeek or
any large language model, read account/position state, generate final advice,
modify formal Kline tables, or perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.storage.mysql.base import Base

try:
    from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = DateTime = ForeignKey = Index = Numeric = String = Text = UniqueConstraint = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class StrategySignalRun(Base):
        """ORM mapping for one strategy signal run batch.

        Parameters: field values are supplied by the stage-16 result repository.
        Return value: SQLAlchemy ORM object.
        Failure scenarios: SQLAlchemy raises mapping or database errors when used.
        External service access: none at class definition time.
        Data impact: defines table metadata only; it never writes Kline tables.
        """

        __tablename__ = "strategy_signal_run"
        __table_args__ = (
            UniqueConstraint("run_id", name="uq_strategy_signal_run_run_id"),
            Index("idx_strategy_signal_run_snapshot_id", "snapshot_id"),
            Index(
                "idx_strategy_signal_run_symbol_intervals_created",
                "symbol",
                "base_interval_value",
                "higher_interval_value",
                "created_at_utc",
            ),
            Index("idx_strategy_signal_run_status_created", "status", "created_at_utc"),
            Index("idx_strategy_signal_run_trace_id", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        run_id: Mapped[str] = mapped_column(String(128), nullable=False)
        snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval_value: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval_value: Mapped[str] = mapped_column(String(16), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        strategy_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        success_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        failed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        invalid_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        not_implemented_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        finished_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


    class StrategySignalResult(Base):
        """ORM mapping for one independent strategy signal result."""

        __tablename__ = "strategy_signal_result"
        __table_args__ = (
            Index("idx_strategy_signal_result_run_id", "run_id"),
            Index("idx_strategy_signal_result_snapshot_id", "snapshot_id"),
            Index("idx_strategy_signal_result_strategy", "strategy_name", "strategy_version"),
            Index("idx_strategy_signal_result_strategy_status", "strategy_status"),
            Index("idx_strategy_signal_result_direction_bias", "direction_bias"),
            Index("idx_strategy_signal_result_risk_level", "risk_level"),
            Index("idx_strategy_signal_result_trace_id", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        run_id: Mapped[str] = mapped_column(
            String(128),
            ForeignKey("strategy_signal_run.run_id"),
            nullable=False,
        )
        snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval_value: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval_value: Mapped[str] = mapped_column(String(16), nullable=False)
        strategy_name: Mapped[str] = mapped_column(String(128), nullable=False)
        strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
        strategy_status: Mapped[str] = mapped_column(String(32), nullable=False)
        direction_bias: Mapped[str] = mapped_column(String(32), nullable=False)
        risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
        signal_strength: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
        reason_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
        reason_text: Mapped[str] = mapped_column(Text, nullable=False)
        metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
        debug_json: Mapped[str] = mapped_column(Text, nullable=False)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class StrategySignalRun:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        run_id: str = ""
        snapshot_id: str | None = None
        symbol: str = ""
        base_interval_value: str = ""
        higher_interval_value: str = ""
        status: str = ""
        trigger_source: str = ""
        strategy_count: int = 0
        success_count: int = 0
        failed_count: int = 0
        invalid_count: int = 0
        not_implemented_count: int = 0
        blocked_reason: str | None = None
        error_message: str | None = None
        trace_id: str = ""
        started_at_utc: datetime | None = None
        finished_at_utc: datetime | None = None
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


    @dataclass
    class StrategySignalResult:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        run_id: str = ""
        snapshot_id: str = ""
        symbol: str = ""
        base_interval_value: str = ""
        higher_interval_value: str = ""
        strategy_name: str = ""
        strategy_version: str = ""
        strategy_status: str = ""
        direction_bias: str = ""
        risk_level: str = ""
        signal_strength: Decimal | float = Decimal("0")
        reason_codes_json: str = "[]"
        reason_text: str = ""
        metrics_json: str = "{}"
        debug_json: str = "{}"
        error_message: str | None = None
        trace_id: str = ""
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None
