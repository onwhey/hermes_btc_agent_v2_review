"""ID helpers for stage-22A manual execution feedback.

This file belongs to `app/manual_execution`. It creates bounded business IDs
for manual position summaries and execution records. It does not read/write
databases, call external services, touch Redis, send Hermes, call DeepSeek, or
perform automatic trading.
"""

from __future__ import annotations

from uuid import uuid4


def build_manual_position_id() -> str:
    """Build one compact business id for a user-reported manual position."""

    return f"MP-{uuid4().hex[:24].upper()}"


def build_manual_execution_id() -> str:
    """Build one compact business id for a user-reported execution record."""

    return f"MEX-{uuid4().hex[:24].upper()}"


__all__ = ["build_manual_execution_id", "build_manual_position_id"]
