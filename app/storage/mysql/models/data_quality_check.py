"""SQLAlchemy model for phase-07 Kline data quality check records.

This file belongs to `app/storage/mysql/models`.
It defines only the `data_quality_check` ORM mapping used to persist quality
reports. It is called by Alembic metadata, the quality-check repository, and tests.
It does not request Binance, parse Klines, send Hermes, read or write Redis, call
DeepSeek, modify formal Kline rows, repair data, or perform trading execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.storage.mysql.base import Base

try:
    from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = Boolean = DateTime = Index = String = Text = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class DataQualityCheck(Base):
        """ORM mapping for one quality-check report record.

        Parameters: field values are supplied by `DataQualityCheckRepository`.
        Return value: SQLAlchemy ORM object.
        Failure scenarios: SQLAlchemy mapping or database errors occur only when used.
        External service access: none.
        Data impact: defines metadata only; it does not connect to MySQL or write rows.
        """

        __tablename__ = "data_quality_check"
        __table_args__ = (
            Index(
                "idx_data_quality_check_symbol_interval_status_created",
                "symbol",
                "interval_value",
                "status",
                "created_at_utc",
            ),
            Index("idx_data_quality_check_check_type", "check_type"),
            Index("idx_data_quality_check_trigger_source", "check_trigger_source"),
            Index("idx_data_quality_check_created_at_utc", "created_at_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        check_type: Mapped[str] = mapped_column(String(64), nullable=False)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        interval_value: Mapped[str] = mapped_column(String(16), nullable=False)
        check_trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
        status: Mapped[str] = mapped_column(String(16), nullable=False)
        severity: Mapped[str] = mapped_column(String(16), nullable=False)
        checked_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        issue_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
        start_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        start_open_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        start_open_time_prc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        end_open_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        end_open_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        end_open_time_prc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        report_json: Mapped[str] = mapped_column(Text, nullable=False)
        first_issue_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
        first_issue_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        alert_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
        alert_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        created_at_prc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_prc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class DataQualityCheck:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        check_type: str = ""
        symbol: str = ""
        interval_value: str = ""
        check_trigger_source: str = ""
        status: str = ""
        severity: str = ""
        checked_count: int = 0
        issue_count: int = 0
        start_open_time_ms: int | None = None
        start_open_time_utc: datetime | None = None
        start_open_time_prc: datetime | None = None
        end_open_time_ms: int | None = None
        end_open_time_utc: datetime | None = None
        end_open_time_prc: datetime | None = None
        report_json: str = ""
        first_issue_type: str | None = None
        first_issue_message: str | None = None
        alert_sent: bool = False
        alert_message_id: int | None = None
        created_at_utc: datetime | None = None
        created_at_prc: datetime | None = None
        updated_at_utc: datetime | None = None
        updated_at_prc: datetime | None = None
