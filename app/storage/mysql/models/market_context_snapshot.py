"""SQLAlchemy models for MarketContextSnapshot persistence.

This file belongs to `app/storage/mysql/models`.
It defines only the market-context snapshot metadata and Kline-reference ORM
tables for stage 15. It is called by Alembic metadata, the market-context
repository, and tests.
It does not request Binance, read/write Redis, send Hermes, call DeepSeek,
generate strategy advice, modify formal Kline tables, or perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.storage.mysql.base import Base

try:
    from sqlalchemy import BigInteger, DateTime, Index, String, Text, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = DateTime = Index = String = Text = UniqueConstraint = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class MarketContextSnapshot(Base):
        """ORM mapping for one market context snapshot record.

        Parameters: field values are provided by the stage-15 repository after
        the service has checked 4h and 1d Kline readiness.
        Return value: SQLAlchemy ORM object.
        Failure scenarios: SQLAlchemy raises mapping or database errors when used.
        External service access: none at class definition time.
        Data impact: defines table metadata only; it never writes Kline tables.
        """

        __tablename__ = "market_context_snapshot"
        __table_args__ = (
            UniqueConstraint("snapshot_id", name="uq_market_context_snapshot_snapshot_id"),
            Index(
                "idx_market_context_snapshot_symbol_intervals_created",
                "symbol",
                "base_interval_value",
                "higher_interval_value",
                "created_at_utc",
            ),
            Index("idx_market_context_snapshot_status_created", "status", "created_at_utc"),
            Index("idx_market_context_snapshot_trace_id", "trace_id"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        base_interval_value: Mapped[str] = mapped_column(String(16), nullable=False)
        higher_interval_value: Mapped[str] = mapped_column(String(16), nullable=False)
        status: Mapped[str] = mapped_column(String(16), nullable=False)
        blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

        latest_4h_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        latest_4h_open_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        latest_1d_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        latest_1d_open_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

        lookback_4h_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        lookback_1d_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        actual_4h_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        actual_1d_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

        start_4h_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        end_4h_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        start_1d_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        end_1d_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

        latest_4h_data_quality_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
        latest_1d_data_quality_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
        latest_4h_collector_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        latest_1d_collector_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        latest_4h_quality_check_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        latest_1d_quality_check_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

        snapshot_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
        created_by: Mapped[str] = mapped_column(String(64), nullable=False)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    class MarketContextSnapshotKlineRef(Base):
        """ORM mapping for one Kline reference used by a market snapshot.

        Parameters: field values point to either a formal 4h or 1d Kline row.
        Return value: SQLAlchemy ORM object.
        Failure scenarios: database uniqueness errors occur if duplicate refs are
        inserted for one snapshot and interval.
        External service access: none.
        Data impact: records references only; it never copies or edits OHLCV data.
        """

        __tablename__ = "market_context_snapshot_kline_ref"
        __table_args__ = (
            Index("idx_market_context_snapshot_kline_ref_snapshot_id", "snapshot_id"),
            Index(
                "idx_market_context_snapshot_kline_ref_symbol_interval_open",
                "symbol",
                "interval_value",
                "open_time_ms",
            ),
            UniqueConstraint(
                "snapshot_id",
                "interval_value",
                "sequence_no",
                name="uq_market_context_snapshot_ref_sequence",
            ),
            UniqueConstraint(
                "snapshot_id",
                "interval_value",
                "open_time_ms",
                name="uq_market_context_snapshot_ref_open_time",
            ),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        interval_value: Mapped[str] = mapped_column(String(16), nullable=False)
        market_kline_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
        open_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
        open_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class MarketContextSnapshot:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        snapshot_id: str = ""
        symbol: str = ""
        base_interval_value: str = ""
        higher_interval_value: str = ""
        status: str = ""
        blocked_reason: str | None = None
        error_message: str | None = None
        latest_4h_open_time_ms: int | None = None
        latest_4h_open_time_utc: datetime | None = None
        latest_1d_open_time_ms: int | None = None
        latest_1d_open_time_utc: datetime | None = None
        lookback_4h_count: int = 0
        lookback_1d_count: int = 0
        actual_4h_count: int = 0
        actual_1d_count: int = 0
        start_4h_open_time_ms: int | None = None
        end_4h_open_time_ms: int | None = None
        start_1d_open_time_ms: int | None = None
        end_1d_open_time_ms: int | None = None
        latest_4h_data_quality_status: str | None = None
        latest_1d_data_quality_status: str | None = None
        latest_4h_collector_event_id: int | None = None
        latest_1d_collector_event_id: int | None = None
        latest_4h_quality_check_id: int | None = None
        latest_1d_quality_check_id: int | None = None
        snapshot_payload_json: str = "{}"
        created_by: str = ""
        trigger_source: str = ""
        trace_id: str = ""
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None

    @dataclass
    class MarketContextSnapshotKlineRef:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        snapshot_id: str = ""
        symbol: str = ""
        interval_value: str = ""
        market_kline_id: int = 0
        open_time_ms: int = 0
        open_time_utc: datetime | None = None
        sequence_no: int = 0
        created_at_utc: datetime | None = None
