"""Candidate filtering rules for stage-20A model review aggregation.

This file belongs to `app/model_review_aggregation`. It contains deterministic
checks for already-persisted stage-19 rows. It does not access external
services, databases, Redis, Hermes, large models, formal Kline tables, or any
trading execution capability.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from app.core.config import AppSettings
from app.model_analysis.prompt_builder import PROMPT_TEMPLATE_HASH

SUCCESS_MODEL_RUN_STATUSES = frozenset({"success"})


def count_model_run_statuses(model_runs: Sequence[Any]) -> Mapping[str, int]:
    """Count failed/blocked/skipped stage-19 attempts for stage-20A output."""

    counts = {"failed": 0, "blocked": 0, "skipped": 0}
    for run in model_runs:
        status = str(getattr(run, "status", "") or "")
        if status == "failed":
            counts["failed"] += 1
        elif status == "blocked":
            counts["blocked"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
    return counts


def filter_exact_material_candidates(candidates: Sequence[Any], material_pack_id: str) -> tuple[Any, ...]:
    """Return successful stage-19 candidates belonging to the current material pack."""

    return tuple(candidate for candidate in candidates if candidate_material_pack_id(candidate) == material_pack_id)


def candidate_material_pack_id(candidate: Any) -> str:
    """Return the candidate material pack business id."""

    return str(getattr(candidate.material_pack, "material_pack_id", "") or "")


def candidate_run_id(candidate: Any) -> str:
    """Return the candidate stage-19 model analysis run id."""

    return str(getattr(candidate.model_analysis_run, "model_analysis_run_id", "") or "")


def candidate_result_created_at(candidate: Any | None) -> datetime | None:
    """Return the candidate result creation time when present."""

    if candidate is None:
        return None
    created_at = getattr(candidate.model_analysis_result, "created_at_utc", None)
    return created_at if isinstance(created_at, datetime) else None


def candidate_boundary_fields_are_false(run: Any) -> bool:
    """Ensure a stage-19 row did not become a trading output.

    Failure to satisfy this check prevents stage 20A from accepting or reusing
    the row. The caller decides whether to block or skip.
    """

    return not any(
        bool(getattr(run, field_name, False))
        for field_name in (
            "is_final_trading_advice",
            "is_trading_signal",
            "is_executable",
            "auto_trading_allowed",
        )
    )


def candidate_metadata_is_compatible(run: Any, *, settings: AppSettings) -> bool:
    """Check schema/profile/prompt metadata before accepting a review row."""

    schema_version = str(getattr(run, "review_schema_version", "") or "")
    if schema_version and schema_version != settings.model_review_schema_version:
        return False
    prompt_template_hash = str(getattr(run, "prompt_template_hash", "") or "")
    if prompt_template_hash and prompt_template_hash != PROMPT_TEMPLATE_HASH:
        return False
    provider = str(getattr(run, "model_provider", "") or "").lower()
    profile_hash = str(getattr(run, "profile_hash", "") or "")
    if provider and provider != "mock" and not profile_hash:
        return False
    return True


def text_attr(value: Any | None, field_name: str) -> str:
    """Return a string attribute value for compact summaries."""

    if value is None:
        return ""
    return str(getattr(value, field_name, "") or "")


__all__ = [
    "SUCCESS_MODEL_RUN_STATUSES",
    "candidate_boundary_fields_are_false",
    "candidate_material_pack_id",
    "candidate_metadata_is_compatible",
    "candidate_result_created_at",
    "candidate_run_id",
    "count_model_run_statuses",
    "filter_exact_material_candidates",
    "text_attr",
]
