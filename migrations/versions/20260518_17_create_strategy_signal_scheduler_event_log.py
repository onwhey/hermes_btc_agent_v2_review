"""Create strategy signal scheduler event log table.

This migration belongs to stage 17. It creates only the scheduler
orchestration audit table for strategy signal runs. It does not alter formal
Kline tables, market context snapshots, strategy result payloads, Redis,
exchange, Hermes, large-model, private trading-state, or trading execution
tables, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260518_17"
down_revision: str | None = "20260518_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the stage-17 scheduler orchestration event table only."""

    op.create_table(
        "strategy_signal_scheduler_event_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("target_base_open_time_ms", sa.BigInteger(), nullable=False),
        sa.Column("target_base_open_time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_base_close_time_ms", sa.BigInteger(), nullable=False),
        sa.Column("target_base_close_time_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_higher_open_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("target_higher_open_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("trigger_reason", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("upstream_4h_collector_event_id", sa.BigInteger(), nullable=True),
        sa.Column("upstream_1d_collector_event_id", sa.BigInteger(), nullable=True),
        sa.Column("strategy_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("invalid_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("not_implemented_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("hermes_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hermes_status", sa.String(length=32), nullable=True),
        sa.Column("hermes_message", sa.Text(), nullable=True),
        sa.Column("hermes_error", sa.Text(), nullable=True),
        sa.Column("hermes_sent_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skip_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_skipped_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_skip_reason", sa.Text(), nullable=True),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "symbol",
            "base_interval",
            "higher_interval",
            "target_base_open_time_ms",
            name="uk_strategy_signal_scheduler_target",
        ),
        sa.UniqueConstraint("event_id", name="uq_strategy_signal_scheduler_event_id"),
    )
    op.create_index(
        "idx_strategy_signal_scheduler_status_created",
        "strategy_signal_scheduler_event_log",
        ["status", "created_at_utc"],
    )
    op.create_index(
        "idx_strategy_signal_scheduler_run_id",
        "strategy_signal_scheduler_event_log",
        ["run_id"],
    )
    op.create_index(
        "idx_strategy_signal_scheduler_snapshot_id",
        "strategy_signal_scheduler_event_log",
        ["snapshot_id"],
    )
    op.create_index(
        "idx_strategy_signal_scheduler_trace_id",
        "strategy_signal_scheduler_event_log",
        ["trace_id"],
    )
    op.create_index(
        "idx_strategy_signal_scheduler_target_close",
        "strategy_signal_scheduler_event_log",
        ["target_base_close_time_utc"],
    )


def downgrade() -> None:
    """Drop only the stage-17 scheduler orchestration event table."""

    op.drop_index("idx_strategy_signal_scheduler_target_close", table_name="strategy_signal_scheduler_event_log")
    op.drop_index("idx_strategy_signal_scheduler_trace_id", table_name="strategy_signal_scheduler_event_log")
    op.drop_index("idx_strategy_signal_scheduler_snapshot_id", table_name="strategy_signal_scheduler_event_log")
    op.drop_index("idx_strategy_signal_scheduler_run_id", table_name="strategy_signal_scheduler_event_log")
    op.drop_index("idx_strategy_signal_scheduler_status_created", table_name="strategy_signal_scheduler_event_log")
    op.drop_table("strategy_signal_scheduler_event_log")
