"""Create 26C strategy pipeline observation index table.

This migration belongs to 26C-A. It creates only the compact
`strategy_pipeline_observation` table used to index existing strategy pipeline
results by formal 4h Kline slot. It does not rerun strategy stages, request
Binance, call large models, send Hermes, read private trading state, or create
trading execution capability, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260606_26c"
down_revision: str | None = "20260605_26b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the 26C observation index table only."""

    op.create_table(
        "strategy_pipeline_observation",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("observation_id", sa.String(length=180), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("kline_slot_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kline_open_time_prc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kline_close_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kline_close_time_prc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canonical_pipeline_run_id", sa.String(length=160), nullable=True),
        sa.Column("canonical_trigger_source", sa.String(length=32), nullable=True),
        sa.Column("canonical_reason", sa.String(length=160), nullable=False),
        sa.Column("duplicate_pipeline_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("excluded_pipeline_run_ids_json", sa.Text(), nullable=False),
        sa.Column("observation_status", sa.String(length=64), nullable=False),
        sa.Column("eligible_for_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "eligible_for_advice_performance_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("pipeline_status", sa.String(length=32), nullable=True),
        sa.Column("pipeline_current_step", sa.String(length=96), nullable=True),
        sa.Column("pipeline_error_code", sa.String(length=128), nullable=True),
        sa.Column("pipeline_error_message", sa.Text(), nullable=True),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=True),
        sa.Column("strategy_evidence_aggregation_id", sa.String(length=160), nullable=True),
        sa.Column("evidence_quality_check_id", sa.String(length=160), nullable=True),
        sa.Column("material_pack_id", sa.String(length=160), nullable=True),
        sa.Column("model_analysis_run_id", sa.String(length=160), nullable=True),
        sa.Column("review_aggregation_run_id", sa.String(length=160), nullable=True),
        sa.Column("advice_id", sa.String(length=160), nullable=True),
        sa.Column("review_id", sa.String(length=160), nullable=True),
        sa.Column("alert_message_id", sa.BigInteger(), nullable=True),
        sa.Column("evidence_quality_status", sa.String(length=32), nullable=True),
        sa.Column("evidence_quality_should_block", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence_quality_failed_roles_json", sa.Text(), nullable=False),
        sa.Column("evidence_quality_failed_strategies_json", sa.Text(), nullable=False),
        sa.Column("model_review_invoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_review_reused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("real_model_called", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("real_model_blocked_by_config", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hermes_real_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notification_status", sa.String(length=64), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("observation_id", name="uq_strategy_pipeline_observation_id"),
        sa.UniqueConstraint(
            "symbol",
            "base_interval",
            "higher_interval",
            "kline_slot_utc",
            name="uq_strategy_pipeline_observation_scope_slot",
        ),
    )
    op.create_index(
        "idx_strategy_pipeline_observation_status",
        "strategy_pipeline_observation",
        ["observation_status", "updated_at_utc"],
    )
    op.create_index(
        "idx_strategy_pipeline_observation_canonical",
        "strategy_pipeline_observation",
        ["canonical_pipeline_run_id"],
    )
    op.create_index(
        "idx_strategy_pipeline_observation_eqc",
        "strategy_pipeline_observation",
        ["evidence_quality_check_id"],
    )
    op.create_index(
        "idx_strategy_pipeline_observation_review",
        "strategy_pipeline_observation",
        ["review_aggregation_run_id"],
    )
    op.create_index(
        "idx_strategy_pipeline_observation_advice",
        "strategy_pipeline_observation",
        ["advice_id"],
    )


def downgrade() -> None:
    """Drop only the 26C observation index table."""

    op.drop_index("idx_strategy_pipeline_observation_advice", table_name="strategy_pipeline_observation")
    op.drop_index("idx_strategy_pipeline_observation_review", table_name="strategy_pipeline_observation")
    op.drop_index("idx_strategy_pipeline_observation_eqc", table_name="strategy_pipeline_observation")
    op.drop_index("idx_strategy_pipeline_observation_canonical", table_name="strategy_pipeline_observation")
    op.drop_index("idx_strategy_pipeline_observation_status", table_name="strategy_pipeline_observation")
    op.drop_table("strategy_pipeline_observation")
