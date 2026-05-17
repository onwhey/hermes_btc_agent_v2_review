"""Create MarketContextSnapshot table.

This migration belongs to stage 15. It creates only the market-context snapshot
main table. It does not alter existing formal Kline tables, create later
analysis, scheduler, Redis, exchange, or execution tables, and inserts no
business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260516_15"
down_revision: str | None = "20260516_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the market context snapshot main table."""

    op.create_table(
        "market_context_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval_value", sa.String(length=16), nullable=False),
        sa.Column("higher_interval_value", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latest_4h_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("latest_4h_open_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_1d_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("latest_1d_open_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lookback_4h_count", sa.BigInteger(), nullable=False),
        sa.Column("lookback_1d_count", sa.BigInteger(), nullable=False),
        sa.Column("actual_4h_count", sa.BigInteger(), nullable=False),
        sa.Column("actual_1d_count", sa.BigInteger(), nullable=False),
        sa.Column("start_4h_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("end_4h_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("start_1d_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("end_1d_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("latest_4h_data_quality_status", sa.String(length=32), nullable=True),
        sa.Column("latest_1d_data_quality_status", sa.String(length=32), nullable=True),
        sa.Column("latest_4h_collector_event_id", sa.BigInteger(), nullable=True),
        sa.Column("latest_1d_collector_event_id", sa.BigInteger(), nullable=True),
        sa.Column("latest_4h_quality_check_id", sa.BigInteger(), nullable=True),
        sa.Column("latest_1d_quality_check_id", sa.BigInteger(), nullable=True),
        sa.Column("snapshot_payload_json", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("snapshot_id", name="uq_market_context_snapshot_snapshot_id"),
    )
    op.create_index(
        "idx_market_context_snapshot_symbol_intervals_created",
        "market_context_snapshot",
        ["symbol", "base_interval_value", "higher_interval_value", "created_at_utc"],
    )
    op.create_index(
        "idx_market_context_snapshot_status_created",
        "market_context_snapshot",
        ["status", "created_at_utc"],
    )
    op.create_index(
        "idx_market_context_snapshot_trace_id",
        "market_context_snapshot",
        ["trace_id"],
    )


def downgrade() -> None:
    """Drop only the market context snapshot main table."""

    op.drop_index("idx_market_context_snapshot_trace_id", table_name="market_context_snapshot")
    op.drop_index("idx_market_context_snapshot_status_created", table_name="market_context_snapshot")
    op.drop_index("idx_market_context_snapshot_symbol_intervals_created", table_name="market_context_snapshot")
    op.drop_table("market_context_snapshot")
