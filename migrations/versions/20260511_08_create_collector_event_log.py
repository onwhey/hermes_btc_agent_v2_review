"""Create collector_event_log table.

This migration belongs to phase 08. It only creates the collector_event_log table
for Kline collection/backfill task audit records and its indexes. It does not
modify market_kline_4h, create strategy/suggestion/trading tables, write Redis,
request Binance, send Hermes, or insert data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260511_08"
down_revision: str | None = "20260511_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the collector_event_log table."""

    op.create_table(
        "collector_event_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("interval_value", sa.String(length=16), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("data_source", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("requested_start_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("requested_end_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("actual_start_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("actual_end_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("requested_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("fetched_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("parsed_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("closed_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("inserted_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("conflict_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("filtered_unclosed_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("issue_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("quality_check_id", sa.BigInteger(), nullable=True),
        sa.Column("alert_message_id", sa.BigInteger(), nullable=True),
        sa.Column("first_issue_type", sa.String(length=64), nullable=True),
        sa.Column("first_issue_message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at_prc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at_prc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at_prc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_prc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_collector_event_log_symbol_interval_status_started",
        "collector_event_log",
        ["symbol", "interval_value", "status", "started_at_utc"],
    )
    op.create_index("idx_collector_event_log_event_type", "collector_event_log", ["event_type"])
    op.create_index(
        "idx_collector_event_log_trigger_source",
        "collector_event_log",
        ["trigger_source"],
    )
    op.create_index("idx_collector_event_log_trace_id", "collector_event_log", ["trace_id"])


def downgrade() -> None:
    """Drop only the collector_event_log table."""

    op.drop_index("idx_collector_event_log_trace_id", table_name="collector_event_log")
    op.drop_index("idx_collector_event_log_trigger_source", table_name="collector_event_log")
    op.drop_index("idx_collector_event_log_event_type", table_name="collector_event_log")
    op.drop_index(
        "idx_collector_event_log_symbol_interval_status_started",
        table_name="collector_event_log",
    )
    op.drop_table("collector_event_log")
