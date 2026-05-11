"""Create data_quality_check table.

This migration belongs to phase 07. It only creates the data_quality_check table
for Kline quality-check reports and its indexes. It does not create collector,
strategy, suggestion, Redis, Binance, scheduler, formal Kline modification, or
trading tables, and it inserts no data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260511_07"
down_revision: str | None = "20260511_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the data_quality_check table."""

    op.create_table(
        "data_quality_check",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("check_type", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("interval_value", sa.String(length=16), nullable=False),
        sa.Column("check_trigger_source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("checked_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("issue_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("start_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("start_open_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_open_time_prc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("end_open_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_open_time_prc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report_json", sa.Text(), nullable=False),
        sa.Column("first_issue_type", sa.String(length=64), nullable=True),
        sa.Column("first_issue_message", sa.Text(), nullable=True),
        sa.Column("alert_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("alert_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at_prc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_prc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_data_quality_check_symbol_interval_status_created",
        "data_quality_check",
        ["symbol", "interval_value", "status", "created_at_utc"],
    )
    op.create_index("idx_data_quality_check_check_type", "data_quality_check", ["check_type"])
    op.create_index(
        "idx_data_quality_check_trigger_source",
        "data_quality_check",
        ["check_trigger_source"],
    )
    op.create_index(
        "idx_data_quality_check_created_at_utc",
        "data_quality_check",
        ["created_at_utc"],
    )


def downgrade() -> None:
    """Drop only the data_quality_check table."""

    op.drop_index("idx_data_quality_check_created_at_utc", table_name="data_quality_check")
    op.drop_index("idx_data_quality_check_trigger_source", table_name="data_quality_check")
    op.drop_index("idx_data_quality_check_check_type", table_name="data_quality_check")
    op.drop_index(
        "idx_data_quality_check_symbol_interval_status_created",
        table_name="data_quality_check",
    )
    op.drop_table("data_quality_check")
