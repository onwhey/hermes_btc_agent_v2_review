"""Create 26B strategy evidence quality gate result table.

This migration belongs to 26B. It creates only the compact
`strategy_evidence_quality_check_result` table used by the strategy evidence
quality gate before stage 18. It does not alter Kline tables, strategy
algorithms, material-pack logic, model review logic, advice lifecycle logic,
account/private trading state, or automatic-trading capabilities, and inserts
no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260604_26b"
down_revision: str | None = "20260603_25a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the 26B quality gate audit table only."""

    op.create_table(
        "strategy_evidence_quality_check_result",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("quality_check_id", sa.String(length=160), nullable=False),
        sa.Column("pipeline_run_id", sa.String(length=160), nullable=True),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=False),
        sa.Column("evidence_aggregation_id", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("kline_slot_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("should_block_pipeline", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("failed_checks_json", sa.Text(), nullable=False),
        sa.Column("warning_checks_json", sa.Text(), nullable=False),
        sa.Column("strategy_quality_json", sa.Text(), nullable=False),
        sa.Column("role_quality_json", sa.Text(), nullable=False),
        sa.Column("config_snapshot_json", sa.Text(), nullable=False),
        sa.Column("alert_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("alert_status", sa.String(length=32), nullable=False, server_default="not_required"),
        sa.Column("alert_message_id", sa.BigInteger(), nullable=True),
        sa.Column("not_trading_advice", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_signal_run_id"],
            ["strategy_signal_run.run_id"],
            name="fk_strategy_evidence_quality_signal_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_aggregation_id"],
            ["strategy_evidence_aggregation_result.aggregation_id"],
            name="fk_strategy_evidence_quality_aggregation_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quality_check_id", name="uq_strategy_evidence_quality_check_id"),
        sa.UniqueConstraint(
            "evidence_aggregation_id",
            "trigger_source",
            name="uq_strategy_evidence_quality_evidence_trigger",
        ),
    )
    op.create_index(
        "idx_strategy_evidence_quality_pipeline",
        "strategy_evidence_quality_check_result",
        ["pipeline_run_id"],
    )
    op.create_index(
        "idx_strategy_evidence_quality_signal",
        "strategy_evidence_quality_check_result",
        ["strategy_signal_run_id"],
    )
    op.create_index(
        "idx_strategy_evidence_quality_status_created",
        "strategy_evidence_quality_check_result",
        ["status", "created_at_utc"],
    )
    op.create_index(
        "idx_strategy_evidence_quality_alert",
        "strategy_evidence_quality_check_result",
        ["alert_status"],
    )
    op.create_index(
        "idx_strategy_evidence_quality_trace_id",
        "strategy_evidence_quality_check_result",
        ["trace_id"],
    )


def downgrade() -> None:
    """Drop only the 26B quality gate audit table."""

    op.drop_index("idx_strategy_evidence_quality_trace_id", table_name="strategy_evidence_quality_check_result")
    op.drop_index("idx_strategy_evidence_quality_alert", table_name="strategy_evidence_quality_check_result")
    op.drop_index("idx_strategy_evidence_quality_status_created", table_name="strategy_evidence_quality_check_result")
    op.drop_index("idx_strategy_evidence_quality_signal", table_name="strategy_evidence_quality_check_result")
    op.drop_index("idx_strategy_evidence_quality_pipeline", table_name="strategy_evidence_quality_check_result")
    op.drop_table("strategy_evidence_quality_check_result")
