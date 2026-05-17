"""Remove stage-15 per-Kline reference table.

This migration keeps `market_context_snapshot` as the stage-15 window-index
table and removes `market_context_snapshot_kline_ref` if a local database has
already applied the earlier branch migration. It does not modify formal Kline
tables, request Binance, insert business data, or widen payload storage to hide
full Kline copies.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260517_15_remove_snapshot_kline_ref"
down_revision: str | None = "20260516_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the obsolete per-Kline reference table and keep payload as summary text."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if "market_context_snapshot_kline_ref" in table_names:
        op.drop_table("market_context_snapshot_kline_ref")

    if "market_context_snapshot" in table_names:
        column_names = {column["name"] for column in inspector.get_columns("market_context_snapshot")}
        if "snapshot_payload_json" in column_names:
            op.alter_column(
                "market_context_snapshot",
                "snapshot_payload_json",
                existing_type=sa.Text(),
                type_=sa.Text(),
                nullable=False,
            )


def downgrade() -> None:
    """Recreate the removed reference table for rollback only; dropped rows cannot be recovered."""

    op.create_table(
        "market_context_snapshot_kline_ref",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("interval_value", sa.String(length=16), nullable=False),
        sa.Column("market_kline_id", sa.BigInteger(), nullable=False),
        sa.Column("open_time_ms", sa.BigInteger(), nullable=False),
        sa.Column("open_time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "snapshot_id",
            "interval_value",
            "sequence_no",
            name="uq_market_context_snapshot_ref_sequence",
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "interval_value",
            "open_time_ms",
            name="uq_market_context_snapshot_ref_open_time",
        ),
    )
    op.create_index(
        "idx_market_context_snapshot_kline_ref_snapshot_id",
        "market_context_snapshot_kline_ref",
        ["snapshot_id"],
    )
    op.create_index(
        "idx_market_context_snapshot_kline_ref_symbol_interval_open",
        "market_context_snapshot_kline_ref",
        ["symbol", "interval_value", "open_time_ms"],
    )
