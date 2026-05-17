"""Create strategy signal run and result tables.

This migration belongs to stage 16. It creates only strategy signal persistence
tables. It does not alter market_context_snapshot, formal Kline tables,
scheduler, Redis, exchange, Hermes, large-model, account, position, or execution
tables, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260518_16"
down_revision: str | None = "20260516_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create stage-16 strategy signal tables only."""

    op.create_table(
        "strategy_signal_run",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval_value", sa.String(length=16), nullable=False),
        sa.Column("higher_interval_value", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("strategy_count", sa.BigInteger(), nullable=False),
        sa.Column("success_count", sa.BigInteger(), nullable=False),
        sa.Column("failed_count", sa.BigInteger(), nullable=False),
        sa.Column("invalid_count", sa.BigInteger(), nullable=False),
        sa.Column("not_implemented_count", sa.BigInteger(), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_strategy_signal_run_run_id"),
    )
    op.create_index("idx_strategy_signal_run_snapshot_id", "strategy_signal_run", ["snapshot_id"])
    op.create_index(
        "idx_strategy_signal_run_symbol_intervals_created",
        "strategy_signal_run",
        ["symbol", "base_interval_value", "higher_interval_value", "created_at_utc"],
    )
    op.create_index("idx_strategy_signal_run_status_created", "strategy_signal_run", ["status", "created_at_utc"])
    op.create_index("idx_strategy_signal_run_trace_id", "strategy_signal_run", ["trace_id"])

    op.create_table(
        "strategy_signal_result",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval_value", sa.String(length=16), nullable=False),
        sa.Column("higher_interval_value", sa.String(length=16), nullable=False),
        sa.Column("strategy_name", sa.String(length=128), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("strategy_status", sa.String(length=32), nullable=False),
        sa.Column("direction_bias", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("signal_strength", sa.Numeric(10, 4), nullable=False),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("debug_json", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["strategy_signal_run.run_id"],
            name="fk_strategy_signal_result_run_id",
        ),
    )
    op.create_index("idx_strategy_signal_result_run_id", "strategy_signal_result", ["run_id"])
    op.create_index("idx_strategy_signal_result_snapshot_id", "strategy_signal_result", ["snapshot_id"])
    op.create_index(
        "idx_strategy_signal_result_strategy",
        "strategy_signal_result",
        ["strategy_name", "strategy_version"],
    )
    op.create_index(
        "idx_strategy_signal_result_strategy_status",
        "strategy_signal_result",
        ["strategy_status"],
    )
    op.create_index("idx_strategy_signal_result_direction_bias", "strategy_signal_result", ["direction_bias"])
    op.create_index("idx_strategy_signal_result_risk_level", "strategy_signal_result", ["risk_level"])
    op.create_index("idx_strategy_signal_result_trace_id", "strategy_signal_result", ["trace_id"])


def downgrade() -> None:
    """Drop only the stage-16 strategy signal tables."""

    op.drop_index("idx_strategy_signal_result_trace_id", table_name="strategy_signal_result")
    op.drop_index("idx_strategy_signal_result_risk_level", table_name="strategy_signal_result")
    op.drop_index("idx_strategy_signal_result_direction_bias", table_name="strategy_signal_result")
    op.drop_index("idx_strategy_signal_result_strategy_status", table_name="strategy_signal_result")
    op.drop_index("idx_strategy_signal_result_strategy", table_name="strategy_signal_result")
    op.drop_index("idx_strategy_signal_result_snapshot_id", table_name="strategy_signal_result")
    op.drop_index("idx_strategy_signal_result_run_id", table_name="strategy_signal_result")
    op.drop_table("strategy_signal_result")

    op.drop_index("idx_strategy_signal_run_trace_id", table_name="strategy_signal_run")
    op.drop_index("idx_strategy_signal_run_status_created", table_name="strategy_signal_run")
    op.drop_index("idx_strategy_signal_run_symbol_intervals_created", table_name="strategy_signal_run")
    op.drop_index("idx_strategy_signal_run_snapshot_id", table_name="strategy_signal_run")
    op.drop_table("strategy_signal_run")
