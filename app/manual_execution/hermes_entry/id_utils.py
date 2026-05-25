"""ID helpers for stage-22B manual execution confirmation intents.

This file belongs to `app/manual_execution/hermes_entry`. It creates compact
MEI business IDs. It does not read/write databases, call external services,
touch Redis, send Hermes, call model providers, or perform automatic trading.
"""

from __future__ import annotations

from uuid import uuid4


def build_manual_execution_intent_id() -> str:
    """Build one compact business id for a pending manual execution intent."""

    return f"MEI-{uuid4().hex[:16].upper()}"


__all__ = ["build_manual_execution_intent_id"]
