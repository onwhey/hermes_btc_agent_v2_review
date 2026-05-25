"""Create stage-22B manual execution confirmation intent table.

This migration belongs to stage 22B. It creates only the Hermes/WeChat manual
execution intent table used before a user confirms MEI-xxx. It does not alter
formal Kline tables, strategy advice lifecycle rows, model provider clients,
private exchange state, Hermes secrets, or any automatic trading capability,
and it inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260531_22b"
down_revision: str | None = "20260530_22a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DECIMAL_38_18 = sa.Numeric(precision=38, scale=18)


def upgrade() -> None:
    """Create only the stage-22B confirmation-intent table."""

    op.create_table(
        "strategy_advice_manual_execution_intent",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("intent_id", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_channel", sa.String(length=32), nullable=False),
        sa.Column("source_message_id", sa.String(length=160), nullable=True),
        sa.Column("source_user_id", sa.String(length=160), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("parsed_action", sa.String(length=32), nullable=True),
        sa.Column("parsed_symbol", sa.String(length=32), nullable=True),
        sa.Column("parsed_side", sa.String(length=16), nullable=True),
        sa.Column("parsed_manual_position_id", sa.String(length=160), nullable=True),
        sa.Column("parsed_advice_id", sa.String(length=160), nullable=True),
        sa.Column("parsed_price", DECIMAL_38_18, nullable=True),
        sa.Column("parsed_notional_usdt", DECIMAL_38_18, nullable=True),
        sa.Column("parsed_margin_usdt", DECIMAL_38_18, nullable=True),
        sa.Column("parsed_reason", sa.Text(), nullable=True),
        sa.Column("parsed_note", sa.Text(), nullable=True),
        sa.Column("parsed_payload_json", sa.Text(), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("validation_error_code", sa.String(length=128), nullable=True),
        sa.Column("validation_error_message", sa.Text(), nullable=True),
        sa.Column("missing_fields_json", sa.Text(), nullable=False),
        sa.Column("dry_run_snapshot_json", sa.Text(), nullable=False),
        sa.Column("executed_manual_position_id", sa.String(length=160), nullable=True),
        sa.Column("executed_execution_id", sa.String(length=160), nullable=True),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_trading_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "status in "
            "('pending_confirmation', 'confirmed', 'executed', 'cancelled', 'expired', "
            "'parse_failed', 'validation_failed', 'execution_failed', 'failed')",
            name="ck_manual_execution_intent_status",
        ),
        sa.UniqueConstraint("intent_id", name="uq_manual_execution_intent_id"),
    )
    op.create_index(
        "idx_manual_execution_intent_status_expires",
        "strategy_advice_manual_execution_intent",
        ["status", "expires_at_utc"],
    )
    op.create_index(
        "idx_manual_execution_intent_source_message",
        "strategy_advice_manual_execution_intent",
        ["source_channel", "source_message_id"],
    )
    op.create_index("idx_manual_execution_intent_trace", "strategy_advice_manual_execution_intent", ["trace_id"])
    op.create_index("idx_manual_execution_intent_created", "strategy_advice_manual_execution_intent", ["created_at_utc"])


def downgrade() -> None:
    """Drop only the stage-22B confirmation-intent table."""

    op.drop_index("idx_manual_execution_intent_created", table_name="strategy_advice_manual_execution_intent")
    op.drop_index("idx_manual_execution_intent_trace", table_name="strategy_advice_manual_execution_intent")
    op.drop_index("idx_manual_execution_intent_source_message", table_name="strategy_advice_manual_execution_intent")
    op.drop_index("idx_manual_execution_intent_status_expires", table_name="strategy_advice_manual_execution_intent")
    op.drop_table("strategy_advice_manual_execution_intent")
