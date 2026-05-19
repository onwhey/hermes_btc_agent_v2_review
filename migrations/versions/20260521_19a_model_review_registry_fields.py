"""Add stage-19A model registry and human-review result fields.

This safe follow-up migration belongs to stage 19A. It adds only model-review
metadata columns and compact result flags. It does not alter formal Kline
tables, strategy configuration, scheduler jobs, Redis state, exchange clients,
real model-provider clients, private trading-state tables, or trading
execution tables, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260521_19a"
down_revision: str | None = "20260520_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add registry metadata and human-review fields using small indexes only."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _add_column_if_missing(
        inspector,
        "model_analysis_run",
        sa.Column("model_key", sa.String(length=96), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "model_analysis_run",
        sa.Column("model_role", sa.String(length=96), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "model_analysis_run",
        sa.Column("analysis_mode", sa.String(length=32), nullable=False, server_default="single"),
    )
    _add_column_if_missing(
        inspector,
        "model_analysis_run",
        sa.Column("chain_id", sa.String(length=160), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "model_analysis_run",
        sa.Column("chain_step", sa.BigInteger(), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "model_analysis_run",
        sa.Column("parent_model_analysis_run_id", sa.String(length=160), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "model_analysis_run",
        sa.Column("comparison_group_id", sa.String(length=160), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "model_analysis_result",
        sa.Column("human_review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    refreshed = sa.inspect(bind)
    _create_index_if_missing(refreshed, "idx_model_analysis_run_model_key", "model_analysis_run", ["model_key"])
    _create_index_if_missing(
        refreshed,
        "idx_model_analysis_run_analysis_mode",
        "model_analysis_run",
        ["analysis_mode"],
    )
    _create_index_if_missing(refreshed, "idx_model_analysis_run_chain_id", "model_analysis_run", ["chain_id"])
    _create_index_if_missing(
        refreshed,
        "idx_model_analysis_run_comparison_group_id",
        "model_analysis_run",
        ["comparison_group_id"],
    )


def downgrade() -> None:
    """Remove only fields added by this stage-19A follow-up migration."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _drop_index_if_present(inspector, "idx_model_analysis_run_comparison_group_id", "model_analysis_run")
    _drop_index_if_present(inspector, "idx_model_analysis_run_chain_id", "model_analysis_run")
    _drop_index_if_present(inspector, "idx_model_analysis_run_analysis_mode", "model_analysis_run")
    _drop_index_if_present(inspector, "idx_model_analysis_run_model_key", "model_analysis_run")

    refreshed = sa.inspect(bind)
    for table_name, column_name in (
        ("model_analysis_result", "human_review_required"),
        ("model_analysis_run", "comparison_group_id"),
        ("model_analysis_run", "parent_model_analysis_run_id"),
        ("model_analysis_run", "chain_step"),
        ("model_analysis_run", "chain_id"),
        ("model_analysis_run", "analysis_mode"),
        ("model_analysis_run", "model_role"),
        ("model_analysis_run", "model_key"),
    ):
        _drop_column_if_present(refreshed, table_name, column_name)


def _add_column_if_missing(inspector: sa.Inspector, table_name: str, column: sa.Column) -> None:
    if not _column_exists(inspector, table_name, column.name):
        op.add_column(table_name, column)


def _drop_column_if_present(inspector: sa.Inspector, table_name: str, column_name: str) -> None:
    if _column_exists(inspector, table_name, column_name):
        op.drop_column(table_name, column_name)


def _create_index_if_missing(
    inspector: sa.Inspector,
    index_name: str,
    table_name: str,
    columns: list[str],
) -> None:
    if not _index_exists(inspector, table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_present(inspector: sa.Inspector, index_name: str, table_name: str) -> None:
    if _index_exists(inspector, table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str | None) -> bool:
    if not column_name:
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))
