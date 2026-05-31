"""Use pipeline_run_id as the 26B quality-check idempotency key.

This migration belongs to the 26B audit-consistency fix. It changes only the
unique constraint for `strategy_evidence_quality_check_result` so different
pipeline runs can keep independent quality rows even when they reuse the same
SEA. It does not alter Kline tables, strategy algorithms, material-pack logic,
model review logic, advice lifecycle logic, account/private trading state, or
automatic-trading capabilities, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260605_26b"
down_revision: str | None = "20260604_26b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace SEA-level 26B uniqueness with pipeline-level uniqueness."""

    op.drop_constraint(
        "uq_strategy_evidence_quality_evidence_trigger",
        "strategy_evidence_quality_check_result",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_strategy_evidence_quality_pipeline_trigger",
        "strategy_evidence_quality_check_result",
        ["pipeline_run_id", "trigger_source"],
    )


def downgrade() -> None:
    """Restore the original SEA-level uniqueness for 26B quality rows."""

    op.drop_constraint(
        "uq_strategy_evidence_quality_pipeline_trigger",
        "strategy_evidence_quality_check_result",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_strategy_evidence_quality_evidence_trigger",
        "strategy_evidence_quality_check_result",
        ["evidence_aggregation_id", "trigger_source"],
    )
