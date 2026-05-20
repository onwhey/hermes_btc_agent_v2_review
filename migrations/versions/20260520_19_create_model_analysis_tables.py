"""Create stage-19 model analysis review-gate tables.

This migration belongs to stage 19A. It creates only the model review attempt
and final-result tables. It does not alter formal Kline tables, strategy
configuration, scheduler jobs, Redis state, exchange clients, Hermes config,
real model-provider clients, private trading-state tables, or trading
execution tables, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260520_19"
down_revision: str | None = "20260519_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create stage-19 review attempt and final-result tables only."""

    op.create_table(
        "model_analysis_run",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("model_analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("review_version_key", sa.String(length=64), nullable=False),
        sa.Column("material_pack_id", sa.String(length=160), nullable=False),
        sa.Column("aggregation_run_id", sa.String(length=160), nullable=True),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=True),
        sa.Column("snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("base_interval", sa.String(length=16), nullable=True),
        sa.Column("higher_interval", sa.String(length=16), nullable=True),
        sa.Column("review_schema_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_template_version", sa.String(length=64), nullable=False),
        sa.Column("model_provider", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=96), nullable=False),
        sa.Column("model_version", sa.String(length=96), nullable=False),
        sa.Column("review_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_material_hash", sa.String(length=64), nullable=False),
        sa.Column("input_summary_json", sa.Text(), nullable=False),
        sa.Column("input_char_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("input_byte_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_char_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_byte_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_final_trading_advice", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_trading_signal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_executable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_trading_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("human_review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("hermes_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hermes_status", sa.String(length=32), nullable=True),
        sa.Column("hermes_message", sa.Text(), nullable=True),
        sa.Column("hermes_error", sa.Text(), nullable=True),
        sa.Column("hermes_sent_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("model_analysis_run_id", name="uq_model_analysis_run_id"),
    )
    op.create_index("idx_model_analysis_run_material_pack", "model_analysis_run", ["material_pack_id"])
    op.create_index("idx_model_analysis_run_aggregation", "model_analysis_run", ["aggregation_run_id"])
    op.create_index("idx_model_analysis_run_strategy_signal", "model_analysis_run", ["strategy_signal_run_id"])
    op.create_index("idx_model_analysis_run_review_version_key", "model_analysis_run", ["review_version_key"])
    op.create_index("idx_model_analysis_run_status_created", "model_analysis_run", ["status", "created_at_utc"])
    op.create_index("idx_model_analysis_run_trace_id", "model_analysis_run", ["trace_id"])

    op.create_table(
        "model_analysis_result",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("model_analysis_result_id", sa.String(length=160), nullable=False),
        sa.Column("model_analysis_run_id", sa.String(length=160), nullable=False),
        sa.Column("review_version_key", sa.String(length=64), nullable=False),
        sa.Column("material_pack_id", sa.String(length=160), nullable=False),
        sa.Column("aggregation_run_id", sa.String(length=160), nullable=False),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=False),
        sa.Column("review_decision", sa.String(length=64), nullable=False),
        sa.Column("evidence_quality", sa.String(length=32), nullable=False),
        sa.Column("logic_consistency", sa.String(length=32), nullable=False),
        sa.Column("risk_acceptability", sa.String(length=32), nullable=False),
        sa.Column("strategy_conflict_level", sa.String(length=32), nullable=False),
        sa.Column("missing_evidence_json", sa.Text(), nullable=False),
        sa.Column("rejection_reasons_json", sa.Text(), nullable=False),
        sa.Column("risk_warnings_json", sa.Text(), nullable=False),
        sa.Column("conditions_to_reconsider_json", sa.Text(), nullable=False),
        sa.Column("validation_focus_json", sa.Text(), nullable=False),
        sa.Column("human_review_questions_json", sa.Text(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("not_trading_advice_text", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_analysis_run_id"],
            ["model_analysis_run.model_analysis_run_id"],
            name="fk_model_analysis_result_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["material_pack_id"],
            ["analysis_material_pack.material_pack_id"],
            name="fk_model_analysis_result_material_pack_id",
        ),
        sa.ForeignKeyConstraint(
            ["aggregation_run_id"],
            ["strategy_aggregation_run.aggregation_run_id"],
            name="fk_model_analysis_result_aggregation_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_signal_run_id"],
            ["strategy_signal_run.run_id"],
            name="fk_model_analysis_result_signal_run_id",
        ),
        sa.UniqueConstraint("model_analysis_result_id", name="uq_model_analysis_result_id"),
        sa.UniqueConstraint("review_version_key", name="uk_model_analysis_result_review_version_key"),
    )
    op.create_index("idx_model_analysis_result_run", "model_analysis_result", ["model_analysis_run_id"])
    op.create_index("idx_model_analysis_result_material_pack", "model_analysis_result", ["material_pack_id"])
    op.create_index("idx_model_analysis_result_aggregation", "model_analysis_result", ["aggregation_run_id"])
    op.create_index("idx_model_analysis_result_strategy_signal", "model_analysis_result", ["strategy_signal_run_id"])
    op.create_index("idx_model_analysis_result_created_at", "model_analysis_result", ["created_at_utc"])


def downgrade() -> None:
    """Drop only the stage-19 review-gate tables."""

    op.drop_index("idx_model_analysis_result_created_at", table_name="model_analysis_result")
    op.drop_index("idx_model_analysis_result_strategy_signal", table_name="model_analysis_result")
    op.drop_index("idx_model_analysis_result_aggregation", table_name="model_analysis_result")
    op.drop_index("idx_model_analysis_result_material_pack", table_name="model_analysis_result")
    op.drop_index("idx_model_analysis_result_run", table_name="model_analysis_result")
    op.drop_table("model_analysis_result")

    op.drop_index("idx_model_analysis_run_trace_id", table_name="model_analysis_run")
    op.drop_index("idx_model_analysis_run_status_created", table_name="model_analysis_run")
    op.drop_index("idx_model_analysis_run_review_version_key", table_name="model_analysis_run")
    op.drop_index("idx_model_analysis_run_strategy_signal", table_name="model_analysis_run")
    op.drop_index("idx_model_analysis_run_aggregation", table_name="model_analysis_run")
    op.drop_index("idx_model_analysis_run_material_pack", table_name="model_analysis_run")
    op.drop_table("model_analysis_run")
