"""Create stage-20B model review chain tables.

This migration belongs to stage 20B. It creates only compact chain/step state
tables used for mock model-review relay orchestration and resume tracking. It
does not alter formal Kline tables, scheduler jobs, strategy configs, Redis
state, model provider clients, private trading state, or trading execution
tables, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260525_20b"
down_revision: str | None = "20260524_20a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the stage-20B chain run and chain step tables only."""

    op.create_table(
        "model_review_chain_run",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chain_id", sa.String(length=160), nullable=False),
        sa.Column("material_pack_id", sa.String(length=160), nullable=False),
        sa.Column("aggregation_run_id", sa.String(length=160), nullable=True),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=True),
        sa.Column("snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("base_interval", sa.String(length=16), nullable=True),
        sa.Column("higher_interval", sa.String(length=16), nullable=True),
        sa.Column("chain_key", sa.String(length=128), nullable=False),
        sa.Column("chain_profile_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("current_step", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_steps", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("success_step_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("failed_step_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("timeout_step_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("skipped_step_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("blocked_step_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("max_retry_count", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_final_trading_advice", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_trading_signal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_executable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_trading_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["material_pack_id"],
            ["analysis_material_pack.material_pack_id"],
            name="fk_model_review_chain_run_material_pack_id",
        ),
        sa.ForeignKeyConstraint(
            ["aggregation_run_id"],
            ["strategy_aggregation_run.aggregation_run_id"],
            name="fk_model_review_chain_run_stage18_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_signal_run_id"],
            ["strategy_signal_run.run_id"],
            name="fk_model_review_chain_run_signal_run_id",
        ),
        sa.UniqueConstraint("chain_id", name="uq_model_review_chain_run_chain_id"),
    )
    op.create_index("idx_model_review_chain_run_material_pack", "model_review_chain_run", ["material_pack_id"])
    op.create_index("idx_model_review_chain_run_aggregation", "model_review_chain_run", ["aggregation_run_id"])
    op.create_index(
        "idx_model_review_chain_run_strategy_signal",
        "model_review_chain_run",
        ["strategy_signal_run_id"],
    )
    op.create_index(
        "idx_model_review_chain_run_status_created",
        "model_review_chain_run",
        ["status", "created_at_utc"],
    )
    op.create_index("idx_model_review_chain_run_trace_id", "model_review_chain_run", ["trace_id"])
    op.create_index("idx_model_review_chain_run_chain_key", "model_review_chain_run", ["chain_key"])

    op.create_table(
        "model_review_chain_step",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chain_step_id", sa.String(length=160), nullable=False),
        sa.Column("chain_id", sa.String(length=160), nullable=False),
        sa.Column("step_no", sa.BigInteger(), nullable=False),
        sa.Column("model_key", sa.String(length=96), nullable=False),
        sa.Column("model_role", sa.String(length=96), nullable=False),
        sa.Column("parent_step_id", sa.String(length=160), nullable=True),
        sa.Column("parent_model_analysis_run_id", sa.String(length=160), nullable=True),
        sa.Column("model_analysis_run_id", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_no", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("max_retry_count", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_after_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("step_input_hash", sa.String(length=64), nullable=True),
        sa.Column("step_output_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chain_id"],
            ["model_review_chain_run.chain_id"],
            name="fk_model_review_chain_step_chain_id",
        ),
        sa.ForeignKeyConstraint(
            ["parent_model_analysis_run_id"],
            ["model_analysis_run.model_analysis_run_id"],
            name="fk_model_review_chain_step_parent_model_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["model_analysis_run_id"],
            ["model_analysis_run.model_analysis_run_id"],
            name="fk_model_review_chain_step_model_run_id",
        ),
        sa.UniqueConstraint("chain_step_id", name="uq_model_review_chain_step_id"),
        sa.UniqueConstraint("chain_id", "step_no", name="uk_model_review_chain_step_chain_no"),
    )
    op.create_index("idx_model_review_chain_step_chain", "model_review_chain_step", ["chain_id"])
    op.create_index("idx_model_review_chain_step_status", "model_review_chain_step", ["status"])
    op.create_index("idx_model_review_chain_step_model_run", "model_review_chain_step", ["model_analysis_run_id"])


def downgrade() -> None:
    """Drop only the stage-20B chain tables."""

    op.drop_index("idx_model_review_chain_step_model_run", table_name="model_review_chain_step")
    op.drop_index("idx_model_review_chain_step_status", table_name="model_review_chain_step")
    op.drop_index("idx_model_review_chain_step_chain", table_name="model_review_chain_step")
    op.drop_table("model_review_chain_step")
    op.drop_index("idx_model_review_chain_run_chain_key", table_name="model_review_chain_run")
    op.drop_index("idx_model_review_chain_run_trace_id", table_name="model_review_chain_run")
    op.drop_index("idx_model_review_chain_run_status_created", table_name="model_review_chain_run")
    op.drop_index("idx_model_review_chain_run_strategy_signal", table_name="model_review_chain_run")
    op.drop_index("idx_model_review_chain_run_aggregation", table_name="model_review_chain_run")
    op.drop_index("idx_model_review_chain_run_material_pack", table_name="model_review_chain_run")
    op.drop_table("model_review_chain_run")
