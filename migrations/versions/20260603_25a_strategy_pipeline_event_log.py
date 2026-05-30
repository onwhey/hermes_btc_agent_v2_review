"""Create stage-25A manual strategy pipeline event log table.

This migration belongs to 25A. It creates only the lightweight
`strategy_pipeline_event_log` audit table used by the manual unified pipeline
entry. It does not alter Kline tables, strategy algorithms, Hermes tables,
large-model tables, account/private trading state, or automatic-trading
capabilities, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_25a"
down_revision: str | None = "20260602_23f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the 25A pipeline audit table only."""

    op.create_table(
        "strategy_pipeline_event_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pipeline_run_id", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("kline_slot_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kline_slot_source", sa.String(length=64), nullable=True),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_step", sa.String(length=96), nullable=True),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=True),
        sa.Column("strategy_evidence_aggregation_id", sa.String(length=160), nullable=True),
        sa.Column("material_pack_id", sa.String(length=160), nullable=True),
        sa.Column("model_analysis_run_id", sa.String(length=160), nullable=True),
        sa.Column("review_aggregation_run_id", sa.String(length=160), nullable=True),
        sa.Column("advice_id", sa.String(length=160), nullable=True),
        sa.Column("review_id", sa.String(length=160), nullable=True),
        sa.Column("notification_status", sa.String(length=64), nullable=True),
        sa.Column("model_review_invoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_review_reused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("real_model_called", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hermes_real_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id", name="uq_strategy_pipeline_run_id"),
    )
    op.create_index(
        "idx_strategy_pipeline_scope_slot",
        "strategy_pipeline_event_log",
        ["symbol", "base_interval", "higher_interval", "kline_slot_utc"],
        unique=False,
    )
    op.create_index(
        "idx_strategy_pipeline_status_created",
        "strategy_pipeline_event_log",
        ["status", "created_at_utc"],
        unique=False,
    )
    op.create_index(
        "idx_strategy_pipeline_trace_id",
        "strategy_pipeline_event_log",
        ["trace_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop only the 25A pipeline audit table and indexes."""

    op.drop_index("idx_strategy_pipeline_trace_id", table_name="strategy_pipeline_event_log")
    op.drop_index("idx_strategy_pipeline_status_created", table_name="strategy_pipeline_event_log")
    op.drop_index("idx_strategy_pipeline_scope_slot", table_name="strategy_pipeline_event_log")
    op.drop_table("strategy_pipeline_event_log")

