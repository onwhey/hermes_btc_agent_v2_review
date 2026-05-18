"""Create strategy aggregation and analysis material pack tables.

This migration belongs to stage 18. It creates only deterministic strategy
aggregation and material-pack persistence tables. It does not alter formal
Kline tables, strategy signal payloads, scheduler event semantics, Redis,
exchange, Hermes, large-model, private trading-state, or trading execution
tables, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260518_18"
down_revision: str | None = "20260518_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the stage-18 aggregation and material-pack tables only."""

    op.create_table(
        "strategy_aggregation_run",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("aggregation_run_id", sa.String(length=160), nullable=False),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("aggregation_version", sa.String(length=64), nullable=False),
        sa.Column("material_schema_version", sa.String(length=64), nullable=False),
        sa.Column("indicator_version", sa.String(length=64), nullable=False),
        sa.Column("candidate_scenario_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_strategy_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("input_success_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("input_failed_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("input_invalid_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("input_not_implemented_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("effective_strategy_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("analysis_hypothesis_direction", sa.String(length=32), nullable=True),
        sa.Column("analysis_hypothesis_confidence", sa.String(length=32), nullable=True),
        sa.Column(
            "analysis_hypothesis_semantics",
            sa.String(length=64),
            nullable=False,
            server_default="analysis_hypothesis_only",
        ),
        sa.Column(
            "direction_projection_source",
            sa.String(length=128),
            nullable=False,
            server_default="fixture_or_existing_signal_projection",
        ),
        sa.Column("stop_trading_source", sa.String(length=128), nullable=True),
        sa.Column("risk_gate_projection_source", sa.String(length=128), nullable=True),
        sa.Column("is_strategy_signal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_trading_advice", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_executable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("strategy_logic_implemented", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("promotion_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "promotion_requires_future_strategy_and_llm_stage",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("risk_level", sa.String(length=32), nullable=True),
        sa.Column("risk_gate_status", sa.String(length=64), nullable=True),
        sa.Column("conflict_level", sa.String(length=32), nullable=True),
        sa.Column("direction_consensus", sa.String(length=32), nullable=True),
        sa.Column("long_strategies_json", sa.Text(), nullable=False),
        sa.Column("short_strategies_json", sa.Text(), nullable=False),
        sa.Column("neutral_strategies_json", sa.Text(), nullable=False),
        sa.Column("supporting_strategies_json", sa.Text(), nullable=False),
        sa.Column("opposing_strategies_json", sa.Text(), nullable=False),
        sa.Column("risk_strategies_json", sa.Text(), nullable=False),
        sa.Column("not_implemented_strategies_json", sa.Text(), nullable=False),
        sa.Column("failed_strategies_json", sa.Text(), nullable=False),
        sa.Column("invalid_strategies_json", sa.Text(), nullable=False),
        sa.Column("candidate_scenarios_json", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("conflict_json", sa.Text(), nullable=False),
        sa.Column("validation_plan_json", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("hermes_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hermes_status", sa.String(length=32), nullable=True),
        sa.Column("hermes_message", sa.Text(), nullable=True),
        sa.Column("hermes_error", sa.Text(), nullable=True),
        sa.Column("hermes_sent_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_signal_run_id"],
            ["strategy_signal_run.run_id"],
            name="fk_strategy_aggregation_signal_run_id",
        ),
        sa.UniqueConstraint("aggregation_run_id", name="uq_strategy_aggregation_run_id"),
        sa.UniqueConstraint(
            "strategy_signal_run_id",
            "aggregation_version",
            "material_schema_version",
            "indicator_version",
            "candidate_scenario_version",
            name="uk_strategy_aggregation_version",
        ),
    )
    op.create_index("idx_strategy_aggregation_strategy_signal_run", "strategy_aggregation_run", ["strategy_signal_run_id"])
    op.create_index("idx_strategy_aggregation_snapshot_id", "strategy_aggregation_run", ["snapshot_id"])
    op.create_index("idx_strategy_aggregation_status_created", "strategy_aggregation_run", ["status", "created_at_utc"])
    op.create_index(
        "idx_strategy_aggregation_hypothesis",
        "strategy_aggregation_run",
        ["analysis_hypothesis_direction", "risk_gate_status"],
    )
    op.create_index("idx_strategy_aggregation_trace_id", "strategy_aggregation_run", ["trace_id"])

    op.create_table(
        "analysis_material_pack",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("material_pack_id", sa.String(length=160), nullable=False),
        sa.Column("aggregation_run_id", sa.String(length=160), nullable=False),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("aggregation_version", sa.String(length=64), nullable=False),
        sa.Column("material_schema_version", sa.String(length=64), nullable=False),
        sa.Column("indicator_version", sa.String(length=64), nullable=False),
        sa.Column("candidate_scenario_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("material_json", sa.Text(), nullable=False),
        sa.Column("question_json", sa.Text(), nullable=False),
        sa.Column("validation_plan_json", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.Column("data_window_json", sa.Text(), nullable=False),
        sa.Column("future_leakage_guard_json", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["aggregation_run_id"],
            ["strategy_aggregation_run.aggregation_run_id"],
            name="fk_analysis_material_pack_aggregation_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_signal_run_id"],
            ["strategy_signal_run.run_id"],
            name="fk_analysis_material_pack_signal_run_id",
        ),
        sa.UniqueConstraint("material_pack_id", name="uq_analysis_material_pack_id"),
        sa.UniqueConstraint("aggregation_run_id", name="uq_analysis_material_pack_aggregation_run_id"),
        sa.UniqueConstraint(
            "strategy_signal_run_id",
            "aggregation_version",
            "material_schema_version",
            "indicator_version",
            "candidate_scenario_version",
            name="uk_analysis_material_pack_version",
        ),
    )
    op.create_index("idx_analysis_material_pack_strategy_signal_run", "analysis_material_pack", ["strategy_signal_run_id"])
    op.create_index("idx_analysis_material_pack_snapshot_id", "analysis_material_pack", ["snapshot_id"])
    op.create_index("idx_analysis_material_pack_status_created", "analysis_material_pack", ["status", "created_at_utc"])
    op.create_index("idx_analysis_material_pack_trace_id", "analysis_material_pack", ["trace_id"])


def downgrade() -> None:
    """Drop only the stage-18 aggregation and material-pack tables."""

    op.drop_index("idx_analysis_material_pack_trace_id", table_name="analysis_material_pack")
    op.drop_index("idx_analysis_material_pack_status_created", table_name="analysis_material_pack")
    op.drop_index("idx_analysis_material_pack_snapshot_id", table_name="analysis_material_pack")
    op.drop_index("idx_analysis_material_pack_strategy_signal_run", table_name="analysis_material_pack")
    op.drop_table("analysis_material_pack")

    op.drop_index("idx_strategy_aggregation_trace_id", table_name="strategy_aggregation_run")
    op.drop_index("idx_strategy_aggregation_hypothesis", table_name="strategy_aggregation_run")
    op.drop_index("idx_strategy_aggregation_status_created", table_name="strategy_aggregation_run")
    op.drop_index("idx_strategy_aggregation_snapshot_id", table_name="strategy_aggregation_run")
    op.drop_index("idx_strategy_aggregation_strategy_signal_run", table_name="strategy_aggregation_run")
    op.drop_table("strategy_aggregation_run")
