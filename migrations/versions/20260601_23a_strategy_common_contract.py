"""Add stage-23A strategy common contract fields.

This migration belongs to stage 23A. It extends only `strategy_signal_result`
with nullable common-contract persistence columns so existing stage-16/17/18
rows remain compatible. It does not alter MarketContextSnapshot, formal Kline
tables, scheduler tables, Redis, exchange, Hermes, large-model, account,
private trading state, or manual execution tables, and inserts no business
data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260601_23a"
down_revision: str | None = "20260531_22b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable 23A columns to strategy_signal_result only."""

    op.add_column("strategy_signal_result", sa.Column("contract_version", sa.String(length=64), nullable=True))
    op.add_column("strategy_signal_result", sa.Column("strategy_role", sa.String(length=64), nullable=True))
    op.add_column("strategy_signal_result", sa.Column("common_payload_json", sa.Text(), nullable=True))
    op.add_column("strategy_signal_result", sa.Column("strategy_model_material_json", sa.Text(), nullable=True))
    op.add_column("strategy_signal_result", sa.Column("strategy_payload_json", sa.Text(), nullable=True))
    op.add_column("strategy_signal_result", sa.Column("extension_payload_json", sa.Text(), nullable=True))
    op.add_column("strategy_signal_result", sa.Column("common_payload_hash", sa.String(length=64), nullable=True))
    op.add_column("strategy_signal_result", sa.Column("validation_status", sa.String(length=32), nullable=True))
    op.add_column("strategy_signal_result", sa.Column("validation_errors_json", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove only the 23A nullable columns."""

    op.drop_column("strategy_signal_result", "validation_errors_json")
    op.drop_column("strategy_signal_result", "validation_status")
    op.drop_column("strategy_signal_result", "common_payload_hash")
    op.drop_column("strategy_signal_result", "extension_payload_json")
    op.drop_column("strategy_signal_result", "strategy_payload_json")
    op.drop_column("strategy_signal_result", "strategy_model_material_json")
    op.drop_column("strategy_signal_result", "common_payload_json")
    op.drop_column("strategy_signal_result", "strategy_role")
    op.drop_column("strategy_signal_result", "contract_version")
