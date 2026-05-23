"""ID helpers for stage-21A strategy advice lifecycle rows.

This file belongs to `app/strategy_advice`. It creates deterministic, bounded
business IDs and user-readable advice codes. It does not read/write databases,
call external services, touch Redis, send Hermes, call model providers, or
perform trading.
"""

from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from app.core.time_utils import UTC


def build_strategy_advice_id(*, review_aggregation_run_id: str, version_no: int, trace_id: str) -> str:
    """Build one compact business id for a strategy advice version."""

    digest = uuid5(
        NAMESPACE_URL,
        f"stage21a-advice:{review_aggregation_run_id}:{version_no}:{trace_id}",
    ).hex[:24].upper()
    return f"ADV-{digest}"


def build_strategy_advice_review_id(*, review_aggregation_run_id: str, trace_id: str) -> str:
    """Build one compact id for a lifecycle review row."""

    digest = uuid5(NAMESPACE_URL, f"stage21a-review:{review_aggregation_run_id}:{trace_id}").hex[:24].upper()
    return f"ADVR-{digest}"


def build_strategy_advice_event_id(*, review_id: str, event_type: str, sequence_no: int) -> str:
    """Build one compact lifecycle event id."""

    digest = uuid5(NAMESPACE_URL, f"stage21a-event:{review_id}:{event_type}:{sequence_no}").hex[:24].upper()
    return f"ADVE-{digest}"


def build_strategy_advice_setup_id(*, advice_id: str, setup_rank: int) -> str:
    """Build one compact setup id for an advice/setup pair."""

    digest = uuid5(NAMESPACE_URL, f"stage21a-setup:{advice_id}:{setup_rank}").hex[:24].upper()
    return f"SETUP-{digest}"


def build_advice_code(*, symbol: str, created_at_utc: datetime, version_no: int) -> str:
    """Build a UTC user-readable advice code such as `20260522-BTCUSDT-04-v1`."""

    active_time = created_at_utc
    if active_time.tzinfo is None:
        active_time = active_time.replace(tzinfo=UTC)
    else:
        active_time = active_time.astimezone(UTC)
    return f"{active_time:%Y%m%d}-{symbol.upper()}-{active_time:%H}-v{int(version_no)}"


__all__ = [
    "build_advice_code",
    "build_strategy_advice_event_id",
    "build_strategy_advice_id",
    "build_strategy_advice_review_id",
    "build_strategy_advice_setup_id",
]
