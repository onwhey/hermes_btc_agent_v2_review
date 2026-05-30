"""Internal utility helpers for the stage-25A strategy pipeline service.

This file belongs to `app/strategy_pipeline`. It stores only small in-memory
state and formatting helpers used by the pipeline service.

Called by `app/strategy_pipeline/service.py`. External services: none. MySQL:
none. Redis: none. Hermes: none. Large models: none. Trading execution: none.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.time_utils import UTC, ensure_utc_aware
from app.strategy_pipeline.types import StrategyPipelineRequest, status_value


class PipelineState:
    """Mutable in-memory progress state for one service call only."""

    def __init__(self, *, pipeline_run_id: str, kline_slot_utc: datetime | None = None) -> None:
        self.pipeline_run_id = pipeline_run_id
        self.kline_slot_utc = kline_slot_utc
        self.kline_slot_source: str | None = None
        self.strategy_signal_run_id: str | None = None
        self.strategy_evidence_aggregation_id: str | None = None
        self.material_pack_id: str | None = None
        self.model_analysis_run_id: str | None = None
        self.review_aggregation_run_id: str | None = None
        self.advice_id: str | None = None
        self.review_id: str | None = None
        self.notification_status: str | None = None
        self.model_review_invoked = False
        self.model_review_reused = False
        self.real_model_called = False
        self.hermes_real_sent = False
        self.current_step: str | None = None
        self.lock_key: str | None = None
        self.details: dict[str, Any] = {}

    @classmethod
    def from_request(cls, request: StrategyPipelineRequest, *, pipeline_run_id: str) -> "PipelineState":
        """Build initial state from request without side effects."""

        return cls(pipeline_run_id=pipeline_run_id, kline_slot_utc=ensure_utc_aware(request.kline_slot_utc))


def compact_object(value: Any) -> dict[str, Any]:
    """Return bounded stage-result details for pipeline audit JSON."""

    keys = (
        "status",
        "run_id",
        "snapshot_id",
        "aggregation_id",
        "aggregation_run_id",
        "material_pack_id",
        "review_aggregation_run_id",
        "lifecycle_review_id",
        "review_id",
        "trace_id",
        "message",
        "summary_text",
        "error_code",
        "error_message",
    )
    result: dict[str, Any] = {}
    for key in keys:
        if hasattr(value, key):
            item = getattr(value, key)
            result[key] = status_value(item) if key == "status" else item
    if hasattr(value, "details"):
        details = getattr(value, "details", {}) or {}
        if isinstance(details, dict):
            result["details_keys"] = sorted(str(key) for key in details.keys())[:20]
    return result


def text_or_none(value: Any) -> str | None:
    """Convert scalar values to non-empty strings for compact result IDs."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def require_slot(value: datetime | None) -> datetime:
    """Return a UTC-aware Kline slot or raise a clear programming error."""

    slot = ensure_utc_aware(value)
    if slot is None:
        raise ValueError("kline_slot_utc is required")
    return slot.astimezone(UTC)


def commit_if_possible(db_session: Any) -> None:
    """Commit caller-owned sessions that expose a commit method."""

    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def rollback_if_possible(db_session: Any) -> None:
    """Rollback caller-owned sessions that expose a rollback method."""

    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = [
    "PipelineState",
    "commit_if_possible",
    "compact_object",
    "require_slot",
    "rollback_if_possible",
    "text_or_none",
]

