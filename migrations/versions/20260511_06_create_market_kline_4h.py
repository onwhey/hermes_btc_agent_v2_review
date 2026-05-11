"""Create market_kline_4h table.

This migration belongs to phase 06. It only creates the formal BTCUSDT 4h Kline
table and its indexes. It does not create collector, quality, alert, strategy,
suggestion, scheduler, Redis, Binance, or trading tables, and it inserts no data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260511_06"
down_revision: str | None = "20260511_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the market_kline_4h table."""

    op.create_table(
        "market_kline_4h",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("market_type", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("interval_value", sa.String(length=16), nullable=False),
        sa.Column("open_time_ms", sa.BigInteger(), nullable=False),
        sa.Column("open_time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_time_prc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time_ms", sa.BigInteger(), nullable=False),
        sa.Column("close_time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time_prc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("high_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("low_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("close_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("volume", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("quote_volume", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("trade_count", sa.BigInteger(), nullable=False),
        sa.Column("taker_buy_base_volume", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("taker_buy_quote_volume", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("data_source", sa.String(length=64), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("raw_payload_json", sa.Text(), nullable=True),
        sa.Column("raw_payload_hash", sa.String(length=128), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at_prc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_prc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "symbol",
            "interval_value",
            "open_time_ms",
            name="uq_market_kline_4h_symbol_interval_open_time_ms",
        ),
    )
    op.create_index(
        "idx_market_kline_4h_symbol_interval_open_time_utc",
        "market_kline_4h",
        ["symbol", "interval_value", "open_time_utc"],
    )
    op.create_index(
        "idx_market_kline_4h_symbol_interval_close_time_ms",
        "market_kline_4h",
        ["symbol", "interval_value", "close_time_ms"],
    )
    op.create_index("idx_market_kline_4h_data_source", "market_kline_4h", ["data_source"])
    op.create_index("idx_market_kline_4h_trigger_source", "market_kline_4h", ["trigger_source"])
    op.create_index("idx_market_kline_4h_created_at_utc", "market_kline_4h", ["created_at_utc"])


def downgrade() -> None:
    """Drop only the market_kline_4h table."""

    op.drop_index("idx_market_kline_4h_created_at_utc", table_name="market_kline_4h")
    op.drop_index("idx_market_kline_4h_trigger_source", table_name="market_kline_4h")
    op.drop_index("idx_market_kline_4h_data_source", table_name="market_kline_4h")
    op.drop_index("idx_market_kline_4h_symbol_interval_close_time_ms", table_name="market_kline_4h")
    op.drop_index("idx_market_kline_4h_symbol_interval_open_time_utc", table_name="market_kline_4h")
    op.drop_table("market_kline_4h")

