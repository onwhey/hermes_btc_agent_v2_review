"""Create stage-22A manual execution feedback tables.

This migration belongs to stage 22A. It creates only the user-reported manual
position summary table and manual execution record table. It does not alter
formal Kline tables, strategy advice lifecycle rows, model provider clients,
private exchange state, Hermes secrets, or any automatic trading capability,
and it inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260530_22a"
down_revision: str | None = "20260529_21c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DECIMAL_38_18 = sa.Numeric(precision=38, scale=18)


def upgrade() -> None:
    """Create only the stage-22A manual execution feedback tables."""

    op.create_table(
        "strategy_advice_manual_position",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("manual_position_id", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("opened_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_by_advice_id", sa.String(length=160), nullable=False),
        sa.Column("latest_related_advice_id", sa.String(length=160), nullable=False),
        sa.Column("closed_by_advice_id", sa.String(length=160), nullable=True),
        sa.Column("initial_entry_price", DECIMAL_38_18, nullable=False),
        sa.Column("avg_entry_price", DECIMAL_38_18, nullable=False),
        sa.Column("close_price", DECIMAL_38_18, nullable=True),
        sa.Column("current_quantity_base_asset", DECIMAL_38_18, nullable=False),
        sa.Column("current_cost_basis_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("margin_basis_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("effective_leverage", DECIMAL_38_18, nullable=False),
        sa.Column("total_open_notional_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("total_close_notional_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("total_fee_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("gross_realized_pnl_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("net_realized_pnl_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("net_pnl_ratio_on_margin", DECIMAL_38_18, nullable=False),
        sa.Column("open_reason", sa.String(length=1000), nullable=True),
        sa.Column("open_decision_context", sa.String(length=2000), nullable=True),
        sa.Column("review_status", sa.String(length=32), nullable=False, server_default="not_reviewed"),
        sa.Column("review_summary", sa.String(length=2000), nullable=True),
        sa.Column("review_correctness", sa.String(length=64), nullable=True),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_trading_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("side in ('long', 'short')", name="ck_manual_position_side"),
        sa.CheckConstraint("status in ('open', 'closed')", name="ck_manual_position_status"),
        sa.ForeignKeyConstraint(
            ["opened_by_advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_manual_position_opened_by_advice_id",
        ),
        sa.ForeignKeyConstraint(
            ["latest_related_advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_manual_position_latest_related_advice_id",
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_manual_position_closed_by_advice_id",
        ),
        sa.UniqueConstraint("manual_position_id", name="uq_manual_position_id"),
    )
    op.create_index(
        "idx_manual_position_symbol_side_status",
        "strategy_advice_manual_position",
        ["symbol", "side", "status"],
    )
    op.create_index("idx_manual_position_opened_advice", "strategy_advice_manual_position", ["opened_by_advice_id"])
    op.create_index("idx_manual_position_latest_advice", "strategy_advice_manual_position", ["latest_related_advice_id"])
    op.create_index("idx_manual_position_closed_advice", "strategy_advice_manual_position", ["closed_by_advice_id"])
    op.create_index("idx_manual_position_trace", "strategy_advice_manual_position", ["trace_id"])
    op.create_index("idx_manual_position_created", "strategy_advice_manual_position", ["created_at_utc"])

    op.create_table(
        "strategy_advice_execution_record",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("execution_id", sa.String(length=160), nullable=False),
        sa.Column("manual_position_id", sa.String(length=160), nullable=False),
        sa.Column("execution_action", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("price", DECIMAL_38_18, nullable=False),
        sa.Column("notional_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("quantity_base_asset", DECIMAL_38_18, nullable=False),
        sa.Column("margin_usdt", DECIMAL_38_18, nullable=True),
        sa.Column("fee_rate", DECIMAL_38_18, nullable=False),
        sa.Column("fee_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("gross_pnl_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("net_pnl_usdt", DECIMAL_38_18, nullable=False),
        sa.Column("advice_id", sa.String(length=160), nullable=False),
        sa.Column("review_id", sa.String(length=160), nullable=True),
        sa.Column("setup_id", sa.String(length=160), nullable=True),
        sa.Column("advice_resolution_method", sa.String(length=64), nullable=False),
        sa.Column("setup_resolution_method", sa.String(length=64), nullable=False),
        sa.Column("manual_position_resolution_method", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("note", sa.String(length=2000), nullable=True),
        sa.Column("executed_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_trading_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("side in ('long', 'short')", name="ck_manual_execution_side"),
        sa.CheckConstraint(
            "execution_action in "
            "('open_position', 'add_position', 'reduce_position', 'close_position', 'take_profit', 'stop_loss')",
            name="ck_manual_execution_action",
        ),
        sa.ForeignKeyConstraint(
            ["manual_position_id"],
            ["strategy_advice_manual_position.manual_position_id"],
            name="fk_manual_execution_manual_position_id",
        ),
        sa.ForeignKeyConstraint(
            ["advice_id"],
            ["strategy_advice.advice_id"],
            name="fk_manual_execution_advice_id",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["strategy_advice_lifecycle_review.review_id"],
            name="fk_manual_execution_review_id",
        ),
        sa.ForeignKeyConstraint(
            ["setup_id"],
            ["strategy_advice_trade_setup.setup_id"],
            name="fk_manual_execution_setup_id",
        ),
        sa.UniqueConstraint("execution_id", name="uq_manual_execution_id"),
    )
    op.create_index("idx_manual_execution_position", "strategy_advice_execution_record", ["manual_position_id"])
    op.create_index("idx_manual_execution_advice", "strategy_advice_execution_record", ["advice_id"])
    op.create_index("idx_manual_execution_review", "strategy_advice_execution_record", ["review_id"])
    op.create_index("idx_manual_execution_setup", "strategy_advice_execution_record", ["setup_id"])
    op.create_index(
        "idx_manual_execution_action_created",
        "strategy_advice_execution_record",
        ["execution_action", "created_at_utc"],
    )
    op.create_index("idx_manual_execution_trace", "strategy_advice_execution_record", ["trace_id"])


def downgrade() -> None:
    """Drop only the stage-22A manual execution feedback tables."""

    op.drop_index("idx_manual_execution_trace", table_name="strategy_advice_execution_record")
    op.drop_index("idx_manual_execution_action_created", table_name="strategy_advice_execution_record")
    op.drop_index("idx_manual_execution_setup", table_name="strategy_advice_execution_record")
    op.drop_index("idx_manual_execution_review", table_name="strategy_advice_execution_record")
    op.drop_index("idx_manual_execution_advice", table_name="strategy_advice_execution_record")
    op.drop_index("idx_manual_execution_position", table_name="strategy_advice_execution_record")
    op.drop_table("strategy_advice_execution_record")
    op.drop_index("idx_manual_position_created", table_name="strategy_advice_manual_position")
    op.drop_index("idx_manual_position_trace", table_name="strategy_advice_manual_position")
    op.drop_index("idx_manual_position_closed_advice", table_name="strategy_advice_manual_position")
    op.drop_index("idx_manual_position_latest_advice", table_name="strategy_advice_manual_position")
    op.drop_index("idx_manual_position_opened_advice", table_name="strategy_advice_manual_position")
    op.drop_index("idx_manual_position_symbol_side_status", table_name="strategy_advice_manual_position")
    op.drop_table("strategy_advice_manual_position")
