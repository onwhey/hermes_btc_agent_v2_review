"""Add explicit veto factors column to 27A weak model aggregation.

This migration belongs to a 27A audit fix. It adds only
`weak_model_aggregation.veto_factors_json` so veto sources are queryable as a
first-class aggregation field. It does not change strategy algorithms, stage 18
material-pack logic, model-review logic, advice lifecycle logic, Kline tables,
account/private trading state, scheduler jobs, Hermes dispatch, or automatic
trading capabilities, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260608_27a"
down_revision: str | None = "20260607_27a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the explicit veto factors JSON text column."""

    op.add_column("weak_model_aggregation", sa.Column("veto_factors_json", sa.Text(), nullable=True))
    op.execute("UPDATE weak_model_aggregation SET veto_factors_json = '[]' WHERE veto_factors_json IS NULL")
    op.alter_column("weak_model_aggregation", "veto_factors_json", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    """Remove only the explicit veto factors JSON text column."""

    op.drop_column("weak_model_aggregation", "veto_factors_json")
