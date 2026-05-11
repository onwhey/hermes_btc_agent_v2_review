"""SQLAlchemy model for the formal BTCUSDT 4h Kline table.

This file belongs to `app/storage/mysql/models`.
It defines only the `market_kline_4h` ORM mapping for phase 06.
It is called by Alembic metadata, the 4h Kline repository, check scripts, and tests.
It does not request Binance, parse raw Klines, send Hermes, read/write Redis, call
large language models, repair Kline data, or perform any trading action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.market_data.kline_constants import DEFAULT_EXCHANGE, DEFAULT_MARKET_TYPE
from app.storage.mysql.base import Base

try:
    from sqlalchemy import BigInteger, DateTime, Index, Numeric, String, Text, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    BigInteger = DateTime = Index = Numeric = String = Text = UniqueConstraint = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class MarketKline4h(Base):
        """ORM mapping for one formal BTCUSDT 4h Kline row.

        Parameters: field values are provided by repository conversion from a validated DTO.
        Return value: SQLAlchemy ORM object.
        Failure scenarios: SQLAlchemy raises mapping or database errors when used incorrectly.
        External service access: none at class definition time.
        Data impact: defines table metadata only; it does not connect to MySQL or send alerts.
        """

        __tablename__ = "market_kline_4h"
        __table_args__ = (
            UniqueConstraint(
                "symbol",
                "interval_value",
                "open_time_ms",
                name="uq_market_kline_4h_symbol_interval_open_time_ms",
            ),
            Index(
                "idx_market_kline_4h_symbol_interval_open_time_utc",
                "symbol",
                "interval_value",
                "open_time_utc",
            ),
            Index(
                "idx_market_kline_4h_symbol_interval_close_time_ms",
                "symbol",
                "interval_value",
                "close_time_ms",
            ),
            Index("idx_market_kline_4h_data_source", "data_source"),
            Index("idx_market_kline_4h_trigger_source", "trigger_source"),
            Index("idx_market_kline_4h_created_at_utc", "created_at_utc"),
        )

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
        exchange: Mapped[str] = mapped_column(String(32), nullable=False, default=DEFAULT_EXCHANGE)
        market_type: Mapped[str] = mapped_column(String(32), nullable=False, default=DEFAULT_MARKET_TYPE)
        symbol: Mapped[str] = mapped_column(String(32), nullable=False)
        interval_value: Mapped[str] = mapped_column(String(16), nullable=False)

        open_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
        open_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        open_time_prc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

        close_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
        close_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        close_time_prc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

        open_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        high_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        low_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        close_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)

        volume: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        quote_volume: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        trade_count: Mapped[int] = mapped_column(BigInteger, nullable=False)

        taker_buy_base_volume: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
        taker_buy_quote_volume: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)

        data_source: Mapped[str] = mapped_column(String(64), nullable=False)
        trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)

        raw_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
        raw_payload_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        created_at_prc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_prc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class MarketKline4h:  # type: ignore[no-redef]
        """Fallback value object used only when SQLAlchemy is unavailable."""

        id: int | None = None
        exchange: str = DEFAULT_EXCHANGE
        market_type: str = DEFAULT_MARKET_TYPE
        symbol: str = ""
        interval_value: str = ""
        open_time_ms: int = 0
        open_time_utc: datetime | None = None
        open_time_prc: datetime | None = None
        close_time_ms: int = 0
        close_time_utc: datetime | None = None
        close_time_prc: datetime | None = None
        open_price: Decimal = Decimal("0")
        high_price: Decimal = Decimal("0")
        low_price: Decimal = Decimal("0")
        close_price: Decimal = Decimal("0")
        volume: Decimal = Decimal("0")
        quote_volume: Decimal = Decimal("0")
        trade_count: int = 0
        taker_buy_base_volume: Decimal = Decimal("0")
        taker_buy_quote_volume: Decimal = Decimal("0")
        data_source: str = ""
        trigger_source: str = ""
        raw_payload_json: str | None = None
        raw_payload_hash: str | None = None
        created_at_utc: datetime | None = None
        created_at_prc: datetime | None = None
        updated_at_utc: datetime | None = None
        updated_at_prc: datetime | None = None

