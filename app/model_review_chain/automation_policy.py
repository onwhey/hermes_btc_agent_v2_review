"""Policy gates for stage-20C automatic model-review worker.

This file belongs to `app/model_review_chain`. It evaluates configuration,
model-key whitelist, provider/profile availability, budget, and per-4h
frequency before the worker may ask stage 19 to perform one real model call.

Called by `app/model_review_chain/worker.py`.
External services: none. MySQL: read-only budget/frequency rows through the
caller-owned repository/session. Redis: none. Hermes: none. DeepSeek/GPT/Claude
calls: none in this file. Trading execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.config import AppSettings
from app.core.time_utils import UTC
from app.model_analysis.model_profile import ModelProfile
from app.model_analysis.model_registry import ModelRegistryError, resolve_model_review_profile

DEFAULT_PRECALL_INPUT_TOKEN_ESTIMATE = 3000
DEFAULT_PRECALL_OUTPUT_TOKEN_ESTIMATE = 1000
DEFAULT_PRECALL_COST_USD = Decimal("0.01")


@dataclass(frozen=True)
class AutomationPolicyDecision:
    """Result of one automatic provider-call gate evaluation."""

    allowed: bool
    error_code: str | None
    message: str
    model_key: str
    estimated_cost_usd: Decimal
    spent_today_usd: Decimal
    daily_budget_usd: Decimal
    current_4h_run_count: int
    max_runs_per_4h: int
    profile: ModelProfile | None = None
    is_temporary: bool = False
    retry_after_utc: datetime | None = None


def evaluate_automatic_step_policy(
    *,
    settings: AppSettings,
    repository: Any,
    db_session: Any,
    model_key: str,
    current_time_utc: datetime,
) -> AutomationPolicyDecision:
    """Return whether 20C may execute one automatic real model step.

    Parameters: unified settings, chain repository, caller-owned session, model
    key, and current UTC time.
    Return value: allow/block decision plus bounded cost metadata.
    Failure scenarios: invalid budget config, disabled gates, missing profile,
    whitelist miss, exceeded budget, or exceeded 4h frequency all block before
    stage 19 is called.
    External effects: read-only database queries for prior worker attempts.
    """

    active_now = _ensure_utc(current_time_utc)
    model_key = model_key.strip()
    if not settings.model_review_real_model_enabled:
        return _blocked(
            model_key=model_key,
            error_code="real_model_disabled",
            message="MODEL_REVIEW_REAL_MODEL_ENABLED=false blocks automatic real model calls.",
        )
    if not settings.model_review_auto_run_enabled:
        return _blocked(
            model_key=model_key,
            error_code="auto_run_disabled",
            message="MODEL_REVIEW_AUTO_RUN_ENABLED=false blocks automatic model review.",
        )
    if not settings.model_review_scheduler_enabled:
        return _blocked(
            model_key=model_key,
            error_code="scheduler_model_review_disabled",
            message="MODEL_REVIEW_SCHEDULER_ENABLED=false blocks the 20C worker.",
        )
    allowed_keys = parse_allowed_model_keys(settings.model_review_scheduler_allowed_model_keys)
    if model_key not in allowed_keys:
        return _blocked(
            model_key=model_key,
            error_code="model_key_not_in_scheduler_whitelist",
            message=f"model_key is not in MODEL_REVIEW_SCHEDULER_ALLOWED_MODEL_KEYS: {model_key}",
        )
    try:
        selection = resolve_model_review_profile(settings.model_review_config_dir, model_key=model_key)
    except ModelRegistryError as exc:
        return _blocked(model_key=model_key, error_code=exc.error_code, message=exc.message)
    profile = selection.profile
    provider_config = selection.provider_config
    if not profile.enabled:
        return _blocked(
            model_key=model_key,
            error_code="model_profile_disabled",
            message=f"model profile is disabled: {model_key}",
            profile=profile,
        )
    if provider_config is None:
        return _blocked(
            model_key=model_key,
            error_code="provider_config_missing",
            message=f"provider config is missing for model_key: {model_key}",
            profile=profile,
        )
    if not provider_config.enabled:
        return _blocked(
            model_key=model_key,
            error_code="model_provider_disabled",
            message=f"model provider is disabled: {provider_config.provider}",
            profile=profile,
        )

    daily_budget = decimal_or_zero(settings.model_review_daily_budget_usd)
    if daily_budget < Decimal("0"):
        return _blocked(
            model_key=model_key,
            error_code="invalid_daily_budget",
            message="MODEL_REVIEW_DAILY_BUDGET_USD must be zero or greater.",
            profile=profile,
        )
    estimated_cost = estimate_precall_step_cost_usd(profile)
    today_start = active_now.replace(hour=0, minute=0, second=0, microsecond=0)
    spent_today = sum_estimated_cost_usd(
        repository.list_worker_real_model_runs_between(
            db_session,
            start_at_utc=today_start,
            end_at_utc=today_start + timedelta(days=1),
        )
    )
    if spent_today + estimated_cost > daily_budget:
        return AutomationPolicyDecision(
            allowed=False,
            error_code="daily_budget_exceeded",
            message=(
                "MODEL_REVIEW_DAILY_BUDGET_USD would be exceeded before this automatic model call."
            ),
            model_key=model_key,
            estimated_cost_usd=estimated_cost,
            spent_today_usd=spent_today,
            daily_budget_usd=daily_budget,
            current_4h_run_count=0,
            max_runs_per_4h=settings.model_review_max_runs_per_4h,
            profile=profile,
            is_temporary=True,
            retry_after_utc=today_start + timedelta(days=1),
        )

    max_runs = int(settings.model_review_max_runs_per_4h)
    if max_runs < 0:
        return _blocked(
            model_key=model_key,
            error_code="invalid_max_runs_per_4h",
            message="MODEL_REVIEW_MAX_RUNS_PER_4H must be zero or greater.",
            estimated_cost=estimated_cost,
            spent_today=spent_today,
            daily_budget=daily_budget,
            max_runs=max_runs,
            profile=profile,
        )
    bucket_start = four_hour_bucket_start(active_now)
    current_4h_count = len(
        repository.list_worker_real_model_runs_between(
            db_session,
            start_at_utc=bucket_start,
            end_at_utc=bucket_start + timedelta(hours=4),
        )
    )
    if current_4h_count >= max_runs:
        return AutomationPolicyDecision(
            allowed=False,
            error_code="max_runs_per_4h_exceeded",
            message="MODEL_REVIEW_MAX_RUNS_PER_4H blocks another automatic model call in this 4h bucket.",
            model_key=model_key,
            estimated_cost_usd=estimated_cost,
            spent_today_usd=spent_today,
            daily_budget_usd=daily_budget,
            current_4h_run_count=current_4h_count,
            max_runs_per_4h=max_runs,
            profile=profile,
            is_temporary=True,
            retry_after_utc=bucket_start + timedelta(hours=4),
        )
    return AutomationPolicyDecision(
        allowed=True,
        error_code=None,
        message="automatic model-call policy gates passed.",
        model_key=model_key,
        estimated_cost_usd=estimated_cost,
        spent_today_usd=spent_today,
        daily_budget_usd=daily_budget,
        current_4h_run_count=current_4h_count,
        max_runs_per_4h=max_runs,
        profile=profile,
    )


def parse_allowed_model_keys(raw_value: str) -> frozenset[str]:
    """Parse comma-separated scheduler model-key whitelist text."""

    return frozenset(item.strip() for item in str(raw_value or "").split(",") if item.strip())


def estimate_precall_step_cost_usd(profile: ModelProfile) -> Decimal:
    """Estimate one step cost before the provider call is attempted."""

    for key in ("scheduler_estimated_cost_usd", "estimated_cost_usd", "estimated_cost"):
        configured = decimal_or_none(profile.cost_policy.get(key))
        if configured is not None:
            return configured
    input_price = decimal_or_none(profile.cost_policy.get("input_token_price"))
    output_price = decimal_or_none(profile.cost_policy.get("output_token_price"))
    if input_price is None or output_price is None:
        return DEFAULT_PRECALL_COST_USD
    input_tokens = int(profile.cost_policy.get("scheduler_estimated_input_tokens") or DEFAULT_PRECALL_INPUT_TOKEN_ESTIMATE)
    output_tokens = int(
        profile.cost_policy.get("scheduler_estimated_output_tokens")
        or profile.request_params.get("max_tokens")
        or DEFAULT_PRECALL_OUTPUT_TOKEN_ESTIMATE
    )
    cost = (Decimal(input_tokens) / Decimal(1_000_000) * input_price) + (
        Decimal(output_tokens) / Decimal(1_000_000) * output_price
    )
    return cost.quantize(Decimal("0.00000001"))


def sum_estimated_cost_usd(rows: tuple[Any, ...]) -> Decimal:
    """Sum compact string cost fields from existing stage-19 attempt rows."""

    total = Decimal("0")
    for row in rows:
        value = decimal_or_none(getattr(row, "estimated_cost", None))
        if value is not None:
            total += value
    return total


def four_hour_bucket_start(value: datetime) -> datetime:
    """Return UTC start of the 4h frequency bucket for a worker call."""

    active = _ensure_utc(value)
    bucket_hour = (active.hour // 4) * 4
    return active.replace(hour=bucket_hour, minute=0, second=0, microsecond=0)


def decimal_or_zero(value: Any) -> Decimal:
    """Convert config text to Decimal, treating blank text as zero."""

    if value is None or str(value).strip() == "":
        return Decimal("0")
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return Decimal("-1")


def decimal_or_none(value: Any) -> Decimal | None:
    """Return a Decimal or None for optional profile cost values."""

    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _blocked(
    *,
    model_key: str,
    error_code: str,
    message: str,
    estimated_cost: Decimal = Decimal("0"),
    spent_today: Decimal = Decimal("0"),
    daily_budget: Decimal = Decimal("0"),
    max_runs: int = 0,
    profile: ModelProfile | None = None,
) -> AutomationPolicyDecision:
    return AutomationPolicyDecision(
        allowed=False,
        error_code=error_code,
        message=message,
        model_key=model_key,
        estimated_cost_usd=estimated_cost,
        spent_today_usd=spent_today,
        daily_budget_usd=daily_budget,
        current_4h_run_count=0,
        max_runs_per_4h=max_runs,
        profile=profile,
        is_temporary=False,
        retry_after_utc=None,
    )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "AutomationPolicyDecision",
    "decimal_or_none",
    "decimal_or_zero",
    "estimate_precall_step_cost_usd",
    "evaluate_automatic_step_policy",
    "four_hour_bucket_start",
    "parse_allowed_model_keys",
    "sum_estimated_cost_usd",
]
