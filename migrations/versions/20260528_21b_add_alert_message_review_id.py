"""Add review-level alert idempotency field for stage-21B.

This migration belongs to the stage-21B notification idempotency fix. It adds
only a nullable related_review_id column to the existing alert_message table so
successful strategy-advice notifications can be deduplicated by lifecycle
review_id instead of by advice_id. It does not alter formal Kline tables,
strategy advice lifecycle decisions, model-review tables, Redis state, Hermes
configuration, private trading state, or trading execution data, and inserts no
business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260528_21b"
down_revision: str | None = "20260527_21b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable review id field and index to alert_message."""

    op.add_column("alert_message", sa.Column("related_review_id", sa.String(length=160), nullable=True))
    op.create_index("ix_alert_message_related_review_id", "alert_message", ["related_review_id"])


def downgrade() -> None:
    """Remove only the stage-21B review idempotency field from alert_message."""

    op.drop_index("ix_alert_message_related_review_id", table_name="alert_message")
    op.drop_column("alert_message", "related_review_id")
