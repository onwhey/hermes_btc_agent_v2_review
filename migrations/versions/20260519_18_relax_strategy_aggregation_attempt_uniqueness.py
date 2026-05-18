"""No-op stage-18 migration kept to preserve the Alembic revision chain.

The preceding stage-18 migration now creates the corrected byte-safe schema
directly, so this revision intentionally performs no database operation.

It does not modify Kline tables, request external services, read/write Redis,
send Hermes, call DeepSeek or other large models, or implement trading.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "20260519_18"
down_revision: str | None = "20260518_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep revision continuity without changing the database schema."""

    pass


def downgrade() -> None:
    """Keep revision continuity without changing the database schema."""

    pass
