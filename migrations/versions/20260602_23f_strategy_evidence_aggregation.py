"""Create stage-23F strategy evidence aggregation result table.

This migration belongs to stage 23F. It creates only the lightweight
`strategy_evidence_aggregation_result` table for strategy-domain evidence
aggregation summaries. It does not alter strategy signal tables, stage-18
material-pack tables, MarketContextSnapshot, formal Kline tables, scheduler
tables, Hermes, large-model, account/private trading state, or manual
execution tables, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260602_23f"
down_revision: str | None = "20260601_23a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the stage-23F aggregation table only."""

    op.create_table(
        "strategy_evidence_aggregation_result",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("aggregation_id", sa.String(length=160), nullable=False),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("candidate_bias", sa.String(length=32), nullable=False),
        sa.Column("candidate_confidence", sa.Numeric(10, 4), nullable=False),
        sa.Column("decision_readiness", sa.String(length=64), nullable=False),
        sa.Column("strategy_evidence_summary_json", sa.Text(), nullable=False),
        sa.Column("decision_source_chain_json", sa.Text(), nullable=False),
        sa.Column("role_coverage_matrix_json", sa.Text(), nullable=False),
        sa.Column("evidence_missing_json", sa.Text(), nullable=False),
        sa.Column("strategy_conflict_summary_json", sa.Text(), nullable=False),
        sa.Column("participation_summary_json", sa.Text(), nullable=False),
        sa.Column("observe_only_summary_json", sa.Text(), nullable=False),
        sa.Column("risk_gate_summary_json", sa.Text(), nullable=False),
        sa.Column("model_review_focus_json", sa.Text(), nullable=False),
        sa.Column("not_trading_advice", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_signal_run_id"],
            ["strategy_signal_run.run_id"],
            name="fk_strategy_evidence_signal_run_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("aggregation_id", name="uq_strategy_evidence_aggregation_id"),
        sa.UniqueConstraint("strategy_signal_run_id", name="uq_strategy_evidence_signal_run_id"),
    )
    op.create_index(
        "idx_strategy_evidence_status_created",
        "strategy_evidence_aggregation_result",
        ["status", "created_at_utc"],
        unique=False,
    )
    op.create_index(
        "idx_strategy_evidence_candidate",
        "strategy_evidence_aggregation_result",
        ["candidate_bias", "decision_readiness"],
        unique=False,
    )
    op.create_index(
        "idx_strategy_evidence_trace_id",
        "strategy_evidence_aggregation_result",
        ["trace_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop only the stage-23F aggregation table."""

    op.drop_index("idx_strategy_evidence_trace_id", table_name="strategy_evidence_aggregation_result")
    op.drop_index("idx_strategy_evidence_candidate", table_name="strategy_evidence_aggregation_result")
    op.drop_index("idx_strategy_evidence_status_created", table_name="strategy_evidence_aggregation_result")
    op.drop_table("strategy_evidence_aggregation_result")
