"""Create 27B weak model output quality check table.

This migration belongs to 27B. It creates only `weak_model_quality_check`,
which stores compact quality-review facts for already persisted 27A weak-model
outputs. It does not rerun weak models, alter weak_model_result or
weak_model_aggregation, modify configs, touch Kline tables, connect to external
services, read account/private trading state, send Hermes, or add automatic
trading capabilities, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260609_27b"
down_revision: str | None = "20260608_27a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the compact 27B weak model quality check table."""

    op.create_table(
        "weak_model_quality_check",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("quality_check_id", sa.String(length=180), nullable=False),
        sa.Column("weak_model_run_id", sa.String(length=180), nullable=False),
        sa.Column("weak_model_aggregation_id", sa.String(length=180), nullable=True),
        sa.Column("strategy_signal_run_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_interval", sa.String(length=16), nullable=False),
        sa.Column("higher_interval", sa.String(length=16), nullable=False),
        sa.Column("kline_slot_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("issue_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("critical_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("should_block_pipeline", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("issues_json", sa.Text(), nullable=False),
        sa.Column("checked_models_json", sa.Text(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["weak_model_run_id"], ["weak_model_run.weak_model_run_id"], name="fk_weak_model_quality_check_run"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quality_check_id", name="uq_weak_model_quality_check_id"),
        sa.UniqueConstraint("weak_model_run_id", name="uq_weak_model_quality_check_run"),
    )
    op.create_index("idx_weak_model_quality_check_aggregation", "weak_model_quality_check", ["weak_model_aggregation_id"])
    op.create_index(
        "idx_weak_model_quality_check_scope_slot",
        "weak_model_quality_check",
        ["symbol", "base_interval", "higher_interval", "kline_slot_utc"],
    )
    op.create_index(
        "idx_weak_model_quality_check_status",
        "weak_model_quality_check",
        ["status", "severity", "created_at_utc"],
    )


def downgrade() -> None:
    """Drop only the compact 27B weak model quality check table."""

    op.drop_index("idx_weak_model_quality_check_status", table_name="weak_model_quality_check")
    op.drop_index("idx_weak_model_quality_check_scope_slot", table_name="weak_model_quality_check")
    op.drop_index("idx_weak_model_quality_check_aggregation", table_name="weak_model_quality_check")
    op.drop_table("weak_model_quality_check")
