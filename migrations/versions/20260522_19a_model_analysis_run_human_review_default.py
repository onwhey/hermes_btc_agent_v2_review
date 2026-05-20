"""Set model_analysis_run human_review_required default to false.

This stage-19A follow-up migration fixes the attempt-table default only. It
does not alter formal Kline tables, strategy configuration, scheduler jobs,
Redis state, exchange clients, real model-provider clients, private trading
state, or trading execution tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260522_19a"
down_revision: str | None = "20260521_19a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make attempt rows default to not requiring human review."""

    inspector = sa.inspect(op.get_bind())
    if _column_exists(inspector, "model_analysis_run", "human_review_required"):
        op.alter_column(
            "model_analysis_run",
            "human_review_required",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )


def downgrade() -> None:
    """Restore the pre-fix default used by the initial 19A migration."""

    inspector = sa.inspect(op.get_bind())
    if _column_exists(inspector, "model_analysis_run", "human_review_required"):
        op.alter_column(
            "model_analysis_run",
            "human_review_required",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        )


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))
