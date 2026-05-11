"""SQLAlchemy model for collector_event_log.

This file belongs to `app/storage/mysql/models`.
It defines only the event-log table used by Kline collection/backfill tasks.
It does not request Binance, parse Klines, send Hermes, read or write Redis,
call DeepSeek, repair market data, or execute trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.storage.mysql.base import Base

try:
    from sqlalchemy import BigInteger, DateTime, Index, String, Text
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = DateTime = Index = String = Text = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class CollectorEventLog(Base):
        """ORM mapping for one collector/backfill task event."""

        __tablename__ = "collector_event_log"
        __table_args__ = (
            Index(
                "idx_collector_event_log_symbol_interval_status_started",
                "symbol",
                "interval_value",
                "status",
                "started_at_utc",
            ),
            Index("idx_collector_event_log_event_type", "event_type"),
            Index("idx_collector_event_log_trigger_source", "trigger_source"),
            Index("idx_collector_event_log_trace_id", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        event_type: Mapped[str] = mapped_column(String(64), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        interval_value: Mapped[str] = mapped_column(String(16), nullable=False)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        data_source: Mapped[str] = mapped_column(String(64), nullable=False)
        status: Mapped[str] = mapped_column(String(32), nullable=False)
        severity: Mapped[str] = mapped_column(String(16), nullable=False)

        requested_start_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        requested_end_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        actual_start_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        actual_end_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

        requested_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        fetched_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        parsed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        closed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        inserted_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        skipped_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        conflict_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        filtered_unclosed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        issue_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

        quality_check_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        alert_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        first_issue_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
        first_issue_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
        details_json: Mapped[str | None] = mapped_column(Text, nullable=True)

        started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        started_at_prc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        finished_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        finished_at_prc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        created_at_prc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_prc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class CollectorEventLog:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        event_type: str = ""
        symbol: str = ""
        interval_value: str = ""
        trigger_source: str = ""
        data_source: str = ""
        status: str = ""
        severity: str = ""
        requested_start_open_time_ms: int | None = None
        requested_end_open_time_ms: int | None = None
        actual_start_open_time_ms: int | None = None
        actual_end_open_time_ms: int | None = None
        requested_count: int = 0
        fetched_count: int = 0
        parsed_count: int = 0
        closed_count: int = 0
        inserted_count: int = 0
        skipped_count: int = 0
        conflict_count: int = 0
        filtered_unclosed_count: int = 0
        issue_count: int = 0
        quality_check_id: int | None = None
        alert_message_id: int | None = None
        first_issue_type: str | None = None
        first_issue_message: str | None = None
        error_code: str | None = None
        error_message: str | None = None
        trace_id: str = ""
        report_json: str | None = None
        details_json: str | None = None
        started_at_utc: datetime | None = None
        started_at_prc: datetime | None = None
        finished_at_utc: datetime | None = None
        finished_at_prc: datetime | None = None
        created_at_utc: datetime | None = None
        created_at_prc: datetime | None = None
        updated_at_utc: datetime | None = None
        updated_at_prc: datetime | None = None

