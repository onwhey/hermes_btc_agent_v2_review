"""Create alert_message table.

This migration belongs to phase 04 alerting. It only creates the alert_message
table for sanitized Hermes alert request/result records. It does not create
market data, strategy, suggestion, Redis, Binance, scheduler, or trading tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260511_04"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the alert_message table."""

    op.create_table(
        "alert_message",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("alert_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("channel_response", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("occurred_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alert_message_alert_type", "alert_message", ["alert_type"])
    op.create_index("ix_alert_message_status", "alert_message", ["status"])
    op.create_index("ix_alert_message_trace_id", "alert_message", ["trace_id"])


def downgrade() -> None:
    """Drop only the alert_message table."""

    op.drop_index("ix_alert_message_trace_id", table_name="alert_message")
    op.drop_index("ix_alert_message_status", table_name="alert_message")
    op.drop_index("ix_alert_message_alert_type", table_name="alert_message")
    op.drop_table("alert_message")

