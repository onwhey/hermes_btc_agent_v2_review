"""Create stage-20A model review aggregation table.

This migration belongs to stage 20A. It creates only the compact
`model_review_aggregation_run` table used to persist deterministic aggregation
and reuse decisions over stage-19 model review results. It does not alter
formal Kline tables, scheduler jobs, strategy configs, Redis state, model
provider clients, private trading state, or trading execution tables, and
inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260524_20a"
down_revision: str | None = "20260523_19b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the stage-20A aggregation table only."""

    op.create_table(
        "model_review_aggregation_run",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("review_aggregation_run_id", sa.String(length=160), nullable=False),
        sa.Column("material_pack_id", sa.String(length=160), nullable=False),
        sa.Column("aggregation_run_id", sa.String(length=160), nullable=False),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("input_model_run_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("input_model_result_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("accepted_model_result_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("failed_model_result_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("blocked_model_result_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("skipped_model_result_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("aggregation_mode", sa.String(length=32), nullable=False),
        sa.Column("model_review_invoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_review_invocation_mode", sa.String(length=32), nullable=False),
        sa.Column("model_review_reused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reused_model_analysis_run_id", sa.String(length=160), nullable=True),
        sa.Column("reused_model_review_created_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_review_skip_reason", sa.Text(), nullable=False),
        sa.Column("model_review_block_reason", sa.Text(), nullable=True),
        sa.Column("invoked_model_keys_json", sa.Text(), nullable=False),
        sa.Column("invoked_model_roles_json", sa.Text(), nullable=False),
        sa.Column("model_review_chain_status", sa.String(length=32), nullable=False),
        sa.Column("model_review_partial_failure_reason", sa.Text(), nullable=True),
        sa.Column("latest_model_review_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_review_basis", sa.String(length=64), nullable=False),
        sa.Column("model_review_reuse_status", sa.String(length=64), nullable=False),
        sa.Column("model_review_reuse_base_bars", sa.BigInteger(), nullable=True),
        sa.Column("model_review_reuse_max_base_bars", sa.BigInteger(), nullable=False, server_default="3"),
        sa.Column("model_review_expired", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("review_input_fingerprint_version", sa.String(length=64), nullable=False),
        sa.Column("review_decision_summary", sa.String(length=160), nullable=False),
        sa.Column("evidence_quality_summary", sa.String(length=160), nullable=False),
        sa.Column("risk_acceptability_summary", sa.String(length=160), nullable=False),
        sa.Column("strategy_conflict_summary", sa.String(length=160), nullable=False),
        sa.Column("model_consensus_level", sa.String(length=32), nullable=False),
        sa.Column("allowed_advice_mode", sa.String(length=32), nullable=False),
        sa.Column("directional_trade_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_results_summary_json", sa.Text(), nullable=False),
        sa.Column("model_disagreement_json", sa.Text(), nullable=False),
        sa.Column("risk_warnings_json", sa.Text(), nullable=False),
        sa.Column("missing_evidence_json", sa.Text(), nullable=False),
        sa.Column("human_review_questions_json", sa.Text(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("is_final_trading_advice", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_trading_signal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_executable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_trading_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["material_pack_id"],
            ["analysis_material_pack.material_pack_id"],
            name="fk_model_review_aggregation_material_pack_id",
        ),
        sa.ForeignKeyConstraint(
            ["aggregation_run_id"],
            ["strategy_aggregation_run.aggregation_run_id"],
            name="fk_model_review_aggregation_stage18_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_signal_run_id"],
            ["strategy_signal_run.run_id"],
            name="fk_model_review_aggregation_signal_run_id",
        ),
        sa.UniqueConstraint("review_aggregation_run_id", name="uq_model_review_aggregation_run_id"),
    )
    op.create_index("idx_model_review_aggregation_material_pack", "model_review_aggregation_run", ["material_pack_id"])
    op.create_index("idx_model_review_aggregation_stage18", "model_review_aggregation_run", ["aggregation_run_id"])
    op.create_index(
        "idx_model_review_aggregation_strategy_signal",
        "model_review_aggregation_run",
        ["strategy_signal_run_id"],
    )
    op.create_index(
        "idx_model_review_aggregation_status_created",
        "model_review_aggregation_run",
        ["status", "created_at_utc"],
    )
    op.create_index("idx_model_review_aggregation_trace_id", "model_review_aggregation_run", ["trace_id"])


def downgrade() -> None:
    """Drop only the stage-20A aggregation table."""

    op.drop_index("idx_model_review_aggregation_trace_id", table_name="model_review_aggregation_run")
    op.drop_index("idx_model_review_aggregation_status_created", table_name="model_review_aggregation_run")
    op.drop_index("idx_model_review_aggregation_strategy_signal", table_name="model_review_aggregation_run")
    op.drop_index("idx_model_review_aggregation_stage18", table_name="model_review_aggregation_run")
    op.drop_index("idx_model_review_aggregation_material_pack", table_name="model_review_aggregation_run")
    op.drop_table("model_review_aggregation_run")
