"""Add alert_message related fields for stage-21B advice notifications.

This migration belongs to stage 21B. It only adds nullable related_type and
related_id columns to the existing alert_message table so strategy-advice
notifications can be audited without creating a duplicate notification table.
It does not alter formal Kline tables, strategy advice lifecycle decisions,
model-review tables, Redis state, Hermes configuration, private trading state,
or trading execution data, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260527_21b"
down_revision: str | None = "20260526_21a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable related columns and indexes to alert_message."""

    op.add_column("alert_message", sa.Column("related_type", sa.String(length=64), nullable=True))
    op.add_column("alert_message", sa.Column("related_id", sa.String(length=160), nullable=True))
    op.create_index("ix_alert_message_related_type", "alert_message", ["related_type"])
    op.create_index("ix_alert_message_related_id", "alert_message", ["related_id"])


def downgrade() -> None:
    """Remove only the stage-21B related columns from alert_message."""

    op.drop_index("ix_alert_message_related_id", table_name="alert_message")
    op.drop_index("ix_alert_message_related_type", table_name="alert_message")
    op.drop_column("alert_message", "related_id")
    op.drop_column("alert_message", "related_type")
