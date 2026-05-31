"""Create 27A weak model factor layer tables.

This migration belongs to 27A. It creates only `weak_model_run`,
`weak_model_result`, and `weak_model_aggregation` for local rule-based weak
model audit data. It does not alter strategy algorithms, material-pack logic,
model review logic, advice lifecycle logic, Kline tables, account/private
trading state, or automatic-trading capabilities, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260607_27a"
down_revision: str | None = "20260606_26c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create 27A weak model run/result/aggregation tables only."""

    op.create_table(
        "weak_model_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("weak_model_run_id", sa.String(length=180), nullable=False),
        sa.Column("pipeline_run_id", sa.String(length=160), nullable=True),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("kline_slot_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_status", sa.String(length=32), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("model_count_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("model_count_enabled", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("model_count_executed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("model_count_failed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["strategy_signal_run_id"], ["strategy_signal_run.run_id"], name="fk_weak_model_run_ssr"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("weak_model_run_id", name="uq_weak_model_run_id"),
    )
    op.create_index("idx_weak_model_run_ssr", "weak_model_run", ["strategy_signal_run_id"])
    op.create_index("idx_weak_model_run_snapshot", "weak_model_run", ["snapshot_id"])
    op.create_index(
        "idx_weak_model_run_scope_slot",
        "weak_model_run",
        ["symbol", "base_interval", "higher_interval", "kline_slot_utc"],
    )
    op.create_index("idx_weak_model_run_status_created", "weak_model_run", ["run_status", "created_at_utc"])
    op.create_index("idx_weak_model_run_trace", "weak_model_run", ["trace_id"])

    op.create_table(
        "weak_model_result",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("weak_model_result_id", sa.String(length=180), nullable=False),
        sa.Column("weak_model_run_id", sa.String(length=180), nullable=False),
        sa.Column("model_key", sa.String(length=128), nullable=False),
        sa.Column("model_role", sa.String(length=32), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("config_version", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("maturity_stage", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("participation_mode", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("kline_slot_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("signal_score", sa.Numeric(10, 6), nullable=True),
        sa.Column("direction_bias", sa.String(length=32), nullable=True),
        sa.Column("risk_score", sa.Numeric(10, 6), nullable=True),
        sa.Column("risk_level", sa.String(length=32), nullable=True),
        sa.Column("trade_permission", sa.String(length=32), nullable=True),
        sa.Column("veto_triggered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confirmation_score", sa.Numeric(10, 6), nullable=True),
        sa.Column("supports_direction", sa.String(length=32), nullable=True),
        sa.Column("context_regime", sa.String(length=64), nullable=True),
        sa.Column("context_score", sa.Numeric(10, 6), nullable=True),
        sa.Column("confidence", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("static_weight", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("effective_score", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("input_summary_json", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("raw_output_json", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["weak_model_run_id"], ["weak_model_run.weak_model_run_id"], name="fk_weak_model_result_run"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("weak_model_result_id", name="uq_weak_model_result_id"),
        sa.UniqueConstraint("weak_model_run_id", "model_key", name="uq_weak_model_result_run_model"),
    )
    op.create_index("idx_weak_model_result_run", "weak_model_result", ["weak_model_run_id"])
    op.create_index("idx_weak_model_result_model_role", "weak_model_result", ["model_key", "model_role"])
    op.create_index("idx_weak_model_result_status", "weak_model_result", ["status"])
    op.create_index(
        "idx_weak_model_result_scope_slot",
        "weak_model_result",
        ["symbol", "base_interval", "higher_interval", "kline_slot_utc"],
    )

    op.create_table(
        "weak_model_aggregation",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("weak_model_aggregation_id", sa.String(length=180), nullable=False),
        sa.Column("weak_model_run_id", sa.String(length=180), nullable=False),
        sa.Column("pipeline_run_id", sa.String(length=160), nullable=True),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("kline_slot_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("directional_score", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("directional_bias", sa.String(length=32), nullable=False),
        sa.Column("directional_confidence", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("trade_permission", sa.String(length=32), nullable=False),
        sa.Column("veto_triggered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supporting_factors_json", sa.Text(), nullable=False),
        sa.Column("opposing_factors_json", sa.Text(), nullable=False),
        sa.Column("conflict_factors_json", sa.Text(), nullable=False),
        sa.Column("low_confidence_factors_json", sa.Text(), nullable=False),
        sa.Column("context_summary_json", sa.Text(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["weak_model_run_id"], ["weak_model_run.weak_model_run_id"], name="fk_weak_model_aggregation_run"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("weak_model_aggregation_id", name="uq_weak_model_aggregation_id"),
        sa.UniqueConstraint("weak_model_run_id", name="uq_weak_model_aggregation_run"),
    )
    op.create_index("idx_weak_model_aggregation_ssr", "weak_model_aggregation", ["strategy_signal_run_id"])
    op.create_index("idx_weak_model_aggregation_snapshot", "weak_model_aggregation", ["snapshot_id"])
    op.create_index(
        "idx_weak_model_aggregation_scope_slot",
        "weak_model_aggregation",
        ["symbol", "base_interval", "higher_interval", "kline_slot_utc"],
    )


def downgrade() -> None:
    """Drop only the 27A weak model tables."""

    op.drop_index("idx_weak_model_aggregation_scope_slot", table_name="weak_model_aggregation")
    op.drop_index("idx_weak_model_aggregation_snapshot", table_name="weak_model_aggregation")
    op.drop_index("idx_weak_model_aggregation_ssr", table_name="weak_model_aggregation")
    op.drop_table("weak_model_aggregation")
    op.drop_index("idx_weak_model_result_scope_slot", table_name="weak_model_result")
    op.drop_index("idx_weak_model_result_status", table_name="weak_model_result")
    op.drop_index("idx_weak_model_result_model_role", table_name="weak_model_result")
    op.drop_index("idx_weak_model_result_run", table_name="weak_model_result")
    op.drop_table("weak_model_result")
    op.drop_index("idx_weak_model_run_trace", table_name="weak_model_run")
    op.drop_index("idx_weak_model_run_status_created", table_name="weak_model_run")
    op.drop_index("idx_weak_model_run_scope_slot", table_name="weak_model_run")
    op.drop_index("idx_weak_model_run_snapshot", table_name="weak_model_run")
    op.drop_index("idx_weak_model_run_ssr", table_name="weak_model_run")
    op.drop_table("weak_model_run")
