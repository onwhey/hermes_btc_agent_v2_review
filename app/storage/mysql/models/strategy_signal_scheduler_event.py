"""SQLAlchemy model for stage-17 strategy signal scheduler events.

This file belongs to `app/storage/mysql/models`. It defines only
`strategy_signal_scheduler_event_log`, the scheduler orchestration audit table
that links a successful collector period to one stage-16 strategy signal run.
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
    from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = Boolean = DateTime = Index = String = Text = UniqueConstraint = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class StrategySignalSchedulerEventLog(Base):
        """ORM mapping for one stage-17 scheduler orchestration event.

        Parameters: values are supplied by the stage-17 scheduler repository.
        Return value: SQLAlchemy ORM row.
        Failure scenarios: SQLAlchemy raises mapping or database errors when used.
        External service access: none at class definition time.
        Data impact: defines table metadata only; it never writes formal Kline
        tables and never stores strategy final advice.
        """

        __tablename__ = "strategy_signal_scheduler_event_log"
        __table_args__ = (
            UniqueConstraint(
                "symbol",
                "base_interval",
                "higher_interval",
                "target_base_open_time_ms",
                name="uk_strategy_signal_scheduler_target",
            ),
            UniqueConstraint("event_id", name="uq_strategy_signal_scheduler_event_id"),
            Index("idx_strategy_signal_scheduler_status_created", "status", "created_at_utc"),
            Index("idx_strategy_signal_scheduler_run_id", "run_id"),
            Index("idx_strategy_signal_scheduler_snapshot_id", "snapshot_id"),
            Index("idx_strategy_signal_scheduler_trace_id", "trace_id"),
            Index("idx_strategy_signal_scheduler_target_close", "target_base_close_time_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        event_id: Mapped[str] = mapped_column(String(128), nullable=False)

        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval: Mapped[str] = mapped_column(String(16), nullable=False)

        target_base_open_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
        target_base_open_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        target_base_close_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
        target_base_close_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        target_higher_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        target_higher_open_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

        status: Mapped[str] = mapped_column(String(32), nullable=False)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        trigger_reason: Mapped[str] = mapped_column(String(64), nullable=False)

        run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
        snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

        upstream_4h_collector_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        upstream_1d_collector_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

        strategy_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        success_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        failed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        invalid_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        not_implemented_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

        message: Mapped[str | None] = mapped_column(Text, nullable=True)
        error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)

        hermes_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        hermes_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
        hermes_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        hermes_error: Mapped[str | None] = mapped_column(Text, nullable=True)
        hermes_sent_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

        skip_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        last_skipped_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        last_skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

        started_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        finished_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class StrategySignalSchedulerEventLog:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        event_id: str = ""
        symbol: str = ""
        base_interval: str = ""
        higher_interval: str = ""
        target_base_open_time_ms: int = 0
        target_base_open_time_utc: datetime | None = None
        target_base_close_time_ms: int = 0
        target_base_close_time_utc: datetime | None = None
        target_higher_open_time_ms: int | None = None
        target_higher_open_time_utc: datetime | None = None
        status: str = ""
        trigger_source: str = ""
        trigger_reason: str = ""
        run_id: str | None = None
        snapshot_id: str | None = None
        upstream_4h_collector_event_id: int | None = None
        upstream_1d_collector_event_id: int | None = None
        strategy_count: int = 0
        success_count: int = 0
        failed_count: int = 0
        invalid_count: int = 0
        not_implemented_count: int = 0
        message: str | None = None
        error_code: str | None = None
        error_message: str | None = None
        trace_id: str = ""
        hermes_enabled: bool = False
        hermes_status: str | None = None
        hermes_message: str | None = None
        hermes_error: str | None = None
        hermes_sent_at_utc: datetime | None = None
        skip_count: int = 0
        last_skipped_at_utc: datetime | None = None
        last_skip_reason: str | None = None
        started_at_utc: datetime | None = None
        finished_at_utc: datetime | None = None
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None


__all__ = ["StrategySignalSchedulerEventLog"]
