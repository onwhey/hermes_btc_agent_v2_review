"""Create stage-21A strategy advice lifecycle tables.

This migration belongs to stage 21A. It creates compact tables for human
strategy advice, lifecycle reviews, lifecycle events, and conditional setup
structures. It does not alter formal Kline tables, scheduler jobs, model
provider clients, Redis state, private trading state, or trading execution
tables, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260526_21a"
down_revision: str | None = "20260525_20b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the stage-21A strategy advice lifecycle tables only."""

    op.create_table(
        "strategy_advice",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("advice_id", sa.String(length=160), nullable=False),
        sa.Column("advice_code", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("parent_advice_id", sa.String(length=160), nullable=True),
        sa.Column("root_advice_id", sa.String(length=160), nullable=False),
        sa.Column("previous_advice_id", sa.String(length=160), nullable=True),
        sa.Column("advice_path", sa.Text(), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("advice_status", sa.String(length=32), nullable=False),
        sa.Column("advice_action", sa.String(length=64), nullable=False),
        sa.Column("directional_bias", sa.String(length=32), nullable=False),
        sa.Column("trade_permission", sa.String(length=64), nullable=False),
        sa.Column("source_review_aggregation_run_id", sa.String(length=160), nullable=False),
        sa.Column("source_material_pack_id", sa.String(length=160), nullable=False),
        sa.Column("source_strategy_signal_run_id", sa.String(length=128), nullable=True),
        sa.Column("source_snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("source_model_chain_id", sa.String(length=160), nullable=True),
        sa.Column("model_review_invoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_review_invocation_mode", sa.String(length=64), nullable=False),
        sa.Column("model_review_reused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reused_model_analysis_run_id", sa.String(length=160), nullable=True),
        sa.Column("model_review_basis", sa.String(length=96), nullable=False),
        sa.Column("model_review_expired", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_review_chain_status", sa.String(length=32), nullable=False),
        sa.Column("latest_model_review_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_review_status_summary_json", sa.Text(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("risk_summary_json", sa.Text(), nullable=False),
        sa.Column("strategy_summary_json", sa.Text(), nullable=False),
        sa.Column("model_summary_json", sa.Text(), nullable=False),
        sa.Column("is_trading_signal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_executable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_trading_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_strategy_advice_parent_advice_id",
        ),
        sa.ForeignKeyConstraint(
            ["previous_advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_strategy_advice_previous_advice_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_review_aggregation_run_id"],
            ["model_review_aggregation_run.review_aggregation_run_id"],
            name="fk_strategy_advice_source_review_aggregation_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_material_pack_id"],
            ["analysis_material_pack.material_pack_id"],
            name="fk_strategy_advice_source_material_pack_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_strategy_signal_run_id"],
            ["strategy_signal_run.run_id"],
            name="fk_strategy_advice_source_strategy_signal_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_model_chain_id"],
            ["model_review_chain_run.chain_id"],
            name="fk_strategy_advice_source_model_chain_id",
        ),
        sa.UniqueConstraint("advice_id", name="uq_strategy_advice_advice_id"),
    )
    op.create_index(
        "idx_strategy_advice_symbol_status",
        "strategy_advice",
        ["symbol", "base_interval", "higher_interval", "advice_status"],
    )
    op.create_index("idx_strategy_advice_root", "strategy_advice", ["root_advice_id"])
    op.create_index("idx_strategy_advice_parent", "strategy_advice", ["parent_advice_id"])
    op.create_index("idx_strategy_advice_source_review", "strategy_advice", ["source_review_aggregation_run_id"])
    op.create_index("idx_strategy_advice_material_pack", "strategy_advice", ["source_material_pack_id"])
    op.create_index("idx_strategy_advice_created", "strategy_advice", ["created_at_utc"])

    op.create_table(
        "strategy_advice_lifecycle_review",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("review_id", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("reviewed_advice_id", sa.String(length=160), nullable=True),
        sa.Column("result_advice_id", sa.String(length=160), nullable=True),
        sa.Column("previous_advice_id", sa.String(length=160), nullable=True),
        sa.Column("lifecycle_action", sa.String(length=64), nullable=False),
        sa.Column("lifecycle_reason", sa.Text(), nullable=False),
        sa.Column("source_review_aggregation_run_id", sa.String(length=160), nullable=False),
        sa.Column("source_material_pack_id", sa.String(length=160), nullable=False),
        sa.Column("source_strategy_signal_run_id", sa.String(length=128), nullable=True),
        sa.Column("source_snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("model_review_invoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_review_invocation_mode", sa.String(length=64), nullable=False),
        sa.Column("model_review_reused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reused_model_analysis_run_id", sa.String(length=160), nullable=True),
        sa.Column("model_review_basis", sa.String(length=96), nullable=False),
        sa.Column("model_review_expired", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_review_chain_status", sa.String(length=32), nullable=False),
        sa.Column("notification_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notification_level", sa.String(length=32), nullable=False),
        sa.Column("notification_reason", sa.Text(), nullable=False),
        sa.Column("notification_payload_json", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reviewed_advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_strategy_advice_lifecycle_reviewed_advice_id",
        ),
        sa.ForeignKeyConstraint(
            ["result_advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_strategy_advice_lifecycle_result_advice_id",
        ),
        sa.ForeignKeyConstraint(
            ["previous_advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_strategy_advice_lifecycle_previous_advice_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_review_aggregation_run_id"],
            ["model_review_aggregation_run.review_aggregation_run_id"],
            name="fk_strategy_advice_lifecycle_source_review_aggregation_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_material_pack_id"],
            ["analysis_material_pack.material_pack_id"],
            name="fk_strategy_advice_lifecycle_source_material_pack_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_strategy_signal_run_id"],
            ["strategy_signal_run.run_id"],
            name="fk_strategy_advice_lifecycle_source_strategy_signal_run_id",
        ),
        sa.UniqueConstraint("review_id", name="uq_strategy_advice_lifecycle_review_id"),
    )
    op.create_index(
        "idx_strategy_advice_lifecycle_symbol",
        "strategy_advice_lifecycle_review",
        ["symbol", "base_interval", "higher_interval"],
    )
    op.create_index(
        "idx_strategy_advice_lifecycle_reviewed",
        "strategy_advice_lifecycle_review",
        ["reviewed_advice_id"],
    )
    op.create_index(
        "idx_strategy_advice_lifecycle_result",
        "strategy_advice_lifecycle_review",
        ["result_advice_id"],
    )
    op.create_index(
        "idx_strategy_advice_lifecycle_source_review",
        "strategy_advice_lifecycle_review",
        ["source_review_aggregation_run_id"],
    )
    op.create_index(
        "idx_strategy_advice_lifecycle_created",
        "strategy_advice_lifecycle_review",
        ["created_at_utc"],
    )

    op.create_table(
        "strategy_advice_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=160), nullable=False),
        sa.Column("advice_id", sa.String(length=160), nullable=True),
        sa.Column("related_review_id", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_reason", sa.Text(), nullable=False),
        sa.Column("event_payload_json", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_strategy_advice_event_advice_id",
        ),
        sa.ForeignKeyConstraint(
            ["related_review_id"],
            ["strategy_advice_lifecycle_review.review_id"],
            name="fk_strategy_advice_event_related_review_id",
        ),
        sa.UniqueConstraint("event_id", name="uq_strategy_advice_event_id"),
    )
    op.create_index("idx_strategy_advice_event_advice", "strategy_advice_event", ["advice_id"])
    op.create_index("idx_strategy_advice_event_review", "strategy_advice_event", ["related_review_id"])
    op.create_index(
        "idx_strategy_advice_event_type_created",
        "strategy_advice_event",
        ["event_type", "created_at_utc"],
    )

    op.create_table(
        "strategy_advice_trade_setup",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("setup_id", sa.String(length=160), nullable=False),
        sa.Column("advice_id", sa.String(length=160), nullable=False),
        sa.Column("setup_rank", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("setup_type", sa.String(length=96), nullable=False),
        sa.Column("side", sa.String(length=32), nullable=False),
        sa.Column("entry_zone_json", sa.Text(), nullable=False),
        sa.Column("trigger_condition_json", sa.Text(), nullable=False),
        sa.Column("invalid_condition_json", sa.Text(), nullable=False),
        sa.Column("stop_loss_json", sa.Text(), nullable=False),
        sa.Column("target_zones_json", sa.Text(), nullable=False),
        sa.Column("expiry_base_bars", sa.BigInteger(), nullable=True),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.Column("source_strategy_names_json", sa.Text(), nullable=False),
        sa.Column("source_model_keys_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_strategy_advice_trade_setup_advice_id",
        ),
        sa.UniqueConstraint("setup_id", name="uq_strategy_advice_trade_setup_id"),
        sa.UniqueConstraint("advice_id", "setup_rank", name="uk_strategy_advice_trade_setup_rank"),
    )
    op.create_index("idx_strategy_advice_trade_setup_advice", "strategy_advice_trade_setup", ["advice_id"])
    op.create_index("idx_strategy_advice_trade_setup_status", "strategy_advice_trade_setup", ["status"])


def downgrade() -> None:
    """Drop only the stage-21A strategy advice lifecycle tables."""

    op.drop_index("idx_strategy_advice_trade_setup_status", table_name="strategy_advice_trade_setup")
    op.drop_index("idx_strategy_advice_trade_setup_advice", table_name="strategy_advice_trade_setup")
    op.drop_table("strategy_advice_trade_setup")
    op.drop_index("idx_strategy_advice_event_type_created", table_name="strategy_advice_event")
    op.drop_index("idx_strategy_advice_event_review", table_name="strategy_advice_event")
    op.drop_index("idx_strategy_advice_event_advice", table_name="strategy_advice_event")
    op.drop_table("strategy_advice_event")
    op.drop_index("idx_strategy_advice_lifecycle_created", table_name="strategy_advice_lifecycle_review")
    op.drop_index("idx_strategy_advice_lifecycle_source_review", table_name="strategy_advice_lifecycle_review")
    op.drop_index("idx_strategy_advice_lifecycle_result", table_name="strategy_advice_lifecycle_review")
    op.drop_index("idx_strategy_advice_lifecycle_reviewed", table_name="strategy_advice_lifecycle_review")
    op.drop_index("idx_strategy_advice_lifecycle_symbol", table_name="strategy_advice_lifecycle_review")
    op.drop_table("strategy_advice_lifecycle_review")
    op.drop_index("idx_strategy_advice_created", table_name="strategy_advice")
    op.drop_index("idx_strategy_advice_material_pack", table_name="strategy_advice")
    op.drop_index("idx_strategy_advice_source_review", table_name="strategy_advice")
    op.drop_index("idx_strategy_advice_parent", table_name="strategy_advice")
    op.drop_index("idx_strategy_advice_root", table_name="strategy_advice")
    op.drop_index("idx_strategy_advice_symbol_status", table_name="strategy_advice")
    op.drop_table("strategy_advice")
