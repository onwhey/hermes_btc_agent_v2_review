"""Add stage-19B real model provider metadata and artifact table.

This migration belongs to stage 19B. It adds compact provider/profile/token/
cost/hash metadata to `model_analysis_run` and creates an isolated artifact
table for raw provider payload references. It does not alter formal Kline
tables, scheduler jobs, strategy configs, Redis state, private trading state,
or trading execution tables, and inserts no business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260523_19b"
down_revision: str | None = "20260522_19a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add only compact model-provider metadata and artifact references."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for column in (
        sa.Column("profile_version", sa.String(length=64), nullable=True),
        sa.Column("profile_hash", sa.String(length=64), nullable=True),
        sa.Column("api_style", sa.String(length=64), nullable=True),
        sa.Column("provider_request_id", sa.String(length=160), nullable=True),
        sa.Column("finish_reason", sa.String(length=64), nullable=True),
        sa.Column("request_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("rendered_prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("prompt_template_hash", sa.String(length=64), nullable=True),
        sa.Column("request_params_summary_json", sa.Text(), nullable=True),
        sa.Column("capabilities_json", sa.Text(), nullable=True),
        sa.Column("response_metadata_summary_json", sa.Text(), nullable=True),
        sa.Column("provider_usage_json", sa.Text(), nullable=True),
        sa.Column("raw_request_hash", sa.String(length=64), nullable=True),
        sa.Column("raw_response_hash", sa.String(length=64), nullable=True),
        sa.Column("raw_request_storage_ref", sa.String(length=512), nullable=True),
        sa.Column("raw_response_storage_ref", sa.String(length=512), nullable=True),
        sa.Column("raw_response_char_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("raw_response_byte_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("input_token_count", sa.BigInteger(), nullable=True),
        sa.Column("output_token_count", sa.BigInteger(), nullable=True),
        sa.Column("total_token_count", sa.BigInteger(), nullable=True),
        sa.Column("estimated_cost", sa.String(length=64), nullable=True),
        sa.Column("cost_currency", sa.String(length=16), nullable=True),
    ):
        _add_column_if_missing(inspector, "model_analysis_run", column)

    refreshed = sa.inspect(bind)
    _create_index_if_missing(
        refreshed,
        "idx_model_analysis_run_profile_hash",
        "model_analysis_run",
        ["profile_hash"],
    )

    if not _table_exists(refreshed, "model_provider_call_artifact"):
        op.create_table(
            "model_provider_call_artifact",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("artifact_id", sa.String(length=160), nullable=False),
            sa.Column("model_analysis_run_id", sa.String(length=160), nullable=False),
            sa.Column("artifact_type", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("model_key", sa.String(length=96), nullable=False),
            sa.Column("model_name", sa.String(length=96), nullable=False),
            sa.Column("model_version", sa.String(length=96), nullable=False),
            sa.Column("profile_hash", sa.String(length=64), nullable=False),
            sa.Column("storage_ref", sa.String(length=512), nullable=False),
            sa.Column("sha256_hash", sa.String(length=64), nullable=False),
            sa.Column("char_count", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("byte_count", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("capture_reason", sa.String(length=160), nullable=False),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["model_analysis_run_id"],
                ["model_analysis_run.model_analysis_run_id"],
                name="fk_model_provider_call_artifact_run_id",
            ),
            sa.UniqueConstraint("artifact_id", name="uq_model_provider_call_artifact_id"),
        )
    refreshed = sa.inspect(bind)
    _create_index_if_missing(
        refreshed,
        "idx_model_provider_call_artifact_run",
        "model_provider_call_artifact",
        ["model_analysis_run_id"],
    )
    _create_index_if_missing(
        refreshed,
        "idx_model_provider_call_artifact_model_key",
        "model_provider_call_artifact",
        ["model_key"],
    )
    _create_index_if_missing(
        refreshed,
        "idx_model_provider_call_artifact_created",
        "model_provider_call_artifact",
        ["created_at_utc"],
    )


def downgrade() -> None:
    """Remove only stage-19B provider metadata and artifacts."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "model_provider_call_artifact"):
        _drop_index_if_present(inspector, "idx_model_provider_call_artifact_created", "model_provider_call_artifact")
        _drop_index_if_present(inspector, "idx_model_provider_call_artifact_model_key", "model_provider_call_artifact")
        _drop_index_if_present(inspector, "idx_model_provider_call_artifact_run", "model_provider_call_artifact")
        op.drop_table("model_provider_call_artifact")

    refreshed = sa.inspect(bind)
    _drop_index_if_present(refreshed, "idx_model_analysis_run_profile_hash", "model_analysis_run")
    refreshed = sa.inspect(bind)
    for column_name in (
        "cost_currency",
        "estimated_cost",
        "total_token_count",
        "output_token_count",
        "input_token_count",
        "raw_response_byte_count",
        "raw_response_char_count",
        "raw_response_storage_ref",
        "raw_request_storage_ref",
        "raw_response_hash",
        "raw_request_hash",
        "provider_usage_json",
        "response_metadata_summary_json",
        "capabilities_json",
        "request_params_summary_json",
        "prompt_template_hash",
        "rendered_prompt_hash",
        "request_payload_hash",
        "finish_reason",
        "provider_request_id",
        "api_style",
        "profile_hash",
        "profile_version",
    ):
        _drop_column_if_present(refreshed, "model_analysis_run", column_name)


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
    if _table_exists(inspector, table_name) and not _index_exists(inspector, table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_present(inspector: sa.Inspector, index_name: str, table_name: str) -> None:
    if _table_exists(inspector, table_name) and _index_exists(inspector, table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str | None) -> bool:
    if not column_name or not _table_exists(inspector, table_name):
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()
