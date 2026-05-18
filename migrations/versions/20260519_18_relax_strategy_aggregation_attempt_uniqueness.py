"""Relax stage-18 aggregation attempt uniqueness.

This migration belongs to stage 18. It changes only the
`strategy_aggregation_run` uniqueness policy so blocked/failed audit attempts
do not permanently prevent reruns for the same version tuple. The final
success/partial_success idempotency guard remains on `analysis_material_pack`.

It does not modify Kline tables, request external services, read/write Redis,
send Hermes, call DeepSeek or other large models, or implement trading.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260519_18"
down_revision: str | None = "20260518_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace all-status aggregation uniqueness with a non-unique lookup index."""

    op.drop_constraint(
        "uk_strategy_aggregation_version",
        "strategy_aggregation_run",
        type_="unique",
    )
    op.create_index(
        "idx_strategy_aggregation_version_status",
        "strategy_aggregation_run",
        [
            "strategy_signal_run_id",
            "aggregation_version",
            "material_schema_version",
            "indicator_version",
            "candidate_scenario_version",
            "status",
        ],
        unique=False,
    )


def downgrade() -> None:
    """Restore the original all-status version uniqueness if rolled back."""

    op.drop_index("idx_strategy_aggregation_version_status", table_name="strategy_aggregation_run")
    op.create_unique_constraint(
        "uk_strategy_aggregation_version",
        "strategy_aggregation_run",
        [
            "strategy_signal_run_id",
            "aggregation_version",
            "material_schema_version",
            "indicator_version",
            "candidate_scenario_version",
        ],
    )
