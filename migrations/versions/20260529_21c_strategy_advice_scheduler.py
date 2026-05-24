"""Add stage-21C strategy advice scheduler idempotency and audit log.

This migration belongs to stage 21C. It adds the MRAG idempotency constraint
for lifecycle reviews and creates a lightweight scheduler audit table. It does
not alter Kline tables, model provider clients, private trading state, Hermes
secrets, or any trading execution capability, and it inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260529_21c"
down_revision: str | None = "20260528_21b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create stage-21C scheduler audit table and MRAG idempotency guard."""

    op.create_unique_constraint(
        "uq_strategy_advice_lifecycle_source_review",
        "strategy_advice_lifecycle_review",
        ["source_review_aggregation_run_id"],
    )
    op.create_table(
        "strategy_advice_scheduler_event_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=160), nullable=False),
        sa.Column("job_name", sa.String(length=96), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("base_interval", sa.String(length=16), nullable=True),
        sa.Column("higher_interval", sa.String(length=16), nullable=True),
        sa.Column("review_aggregation_run_id", sa.String(length=160), nullable=True),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_strategy_advice_scheduler_event_id"),
    )
    op.create_index(
        "idx_strategy_advice_scheduler_job_created",
        "strategy_advice_scheduler_event_log",
        ["job_name", "created_at_utc"],
    )
    op.create_index(
        "idx_strategy_advice_scheduler_mrag",
        "strategy_advice_scheduler_event_log",
        ["review_aggregation_run_id"],
    )
    op.create_index(
        "idx_strategy_advice_scheduler_status",
        "strategy_advice_scheduler_event_log",
        ["status", "created_at_utc"],
    )
    op.create_index(
        "idx_strategy_advice_scheduler_trace",
        "strategy_advice_scheduler_event_log",
        ["trace_id"],
    )


def downgrade() -> None:
    """Remove stage-21C scheduler audit table and MRAG idempotency guard."""

    op.drop_index("idx_strategy_advice_scheduler_trace", table_name="strategy_advice_scheduler_event_log")
    op.drop_index("idx_strategy_advice_scheduler_status", table_name="strategy_advice_scheduler_event_log")
    op.drop_index("idx_strategy_advice_scheduler_mrag", table_name="strategy_advice_scheduler_event_log")
    op.drop_index("idx_strategy_advice_scheduler_job_created", table_name="strategy_advice_scheduler_event_log")
    op.drop_table("strategy_advice_scheduler_event_log")
    op.drop_constraint(
        "uq_strategy_advice_lifecycle_source_review",
        "strategy_advice_lifecycle_review",
        type_="unique",
    )
