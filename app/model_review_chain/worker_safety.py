"""Safety helpers for stage-20C model-review worker recovery.

This file belongs to `app/model_review_chain`. It centralizes bounded safety
decisions for CLI cost confirmation, temporary retry windows, provider
temporary failures, and stale RUNNING step timeout checks.

Called by `app/model_review_chain/worker.py`. It does not access external
services, does not read or write MySQL, does not read or write Redis, does not
send Hermes, does not call DeepSeek/GPT/Claude, and does not involve trading
execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.time_utils import UTC
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_review_chain.schema import ModelReviewChainStepStatus

DEFAULT_TEMPORARY_RETRY_AFTER_SECONDS = 60
TEMPORARY_STAGE19_ERROR_MARKERS = (
    "timeout",
    "rate_limit",
    "temporarily",
    "temporary",
    "unavailable",
    "too_many_requests",
    "429",
)


@dataclass(frozen=True)
class StaleRunningStepRecovery:
    """One stale RUNNING step transition calculated without database writes."""

    step_row: Any
    status: ModelReviewChainStepStatus
    attempt_no: int
    finished_at_utc: datetime
    retry_after_utc: datetime | None
    error_code: str
    error_message: str


@dataclass(frozen=True)
class StaleRunningRecoveryPlan:
    """Batch of stale RUNNING step transitions for one chain scan."""

    updates: tuple[StaleRunningStepRecovery, ...]
    retryable_timeout_found: bool
    timeout_seconds: int

    @property
    def changed(self) -> bool:
        """Return whether any step needs persistence."""

        return bool(self.updates)


def cli_real_model_cost_confirmation_missing(request: Any) -> bool:
    """Return whether a CLI-triggered worker tick lacks cost confirmation."""

    return (
        str(getattr(request, "trigger_source", "") or "") == TRIGGER_SOURCE_CLI
        and not bool(getattr(request, "confirm_real_model_cost", False))
    )


def temporary_retry_after_utc(
    current_time_utc: datetime,
    *,
    seconds: int = DEFAULT_TEMPORARY_RETRY_AFTER_SECONDS,
) -> datetime:
    """Return a short retry-after timestamp for recoverable worker waits."""

    return ensure_utc(current_time_utc) + timedelta(seconds=max(1, int(seconds)))


def ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC, interpreting naive values as UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utc_or_none(value: Any) -> datetime | None:
    """Return a UTC datetime or None for nullable persisted timestamps."""

    if not isinstance(value, datetime):
        return None
    return ensure_utc(value)


def running_step_timed_out(
    *,
    started_at_utc: Any,
    current_time_utc: datetime,
    timeout_seconds: int,
) -> bool:
    """Return whether a RUNNING step has exceeded the configured timeout."""

    started = utc_or_none(started_at_utc)
    if started is None:
        return False
    elapsed = ensure_utc(current_time_utc) - started
    return elapsed.total_seconds() > max(1, int(timeout_seconds))


def retry_available_after_attempt(*, attempt_no: int, max_retry_count: int) -> bool:
    """Return whether another attempt is allowed after the current attempt."""

    retries_used = max(0, int(attempt_no) - 1)
    return retries_used < max(0, int(max_retry_count))


def stage19_result_is_temporary_failure(stage19_result: Any) -> bool:
    """Return whether a stage-19 failure looks recoverable for retry waiting."""

    text = " ".join(
        str(value or "").lower()
        for value in (
            getattr(stage19_result, "error_code", None),
            getattr(stage19_result, "error_message", None),
            getattr(stage19_result, "message", None),
        )
    )
    return any(marker in text for marker in TEMPORARY_STAGE19_ERROR_MARKERS)


def build_stale_running_recovery_plan(
    *,
    step_rows: tuple[Any, ...],
    current_time_utc: datetime,
    timeout_seconds: int,
) -> StaleRunningRecoveryPlan:
    """Build timeout transitions for stale RUNNING steps without side effects."""

    current_time = ensure_utc(current_time_utc)
    timeout_value = max(1, int(timeout_seconds))
    updates: list[StaleRunningStepRecovery] = []
    retryable_timeout_found = False
    for step_row in step_rows:
        if _step_status_value(step_row) != ModelReviewChainStepStatus.RUNNING.value:
            continue
        if not running_step_timed_out(
            started_at_utc=getattr(step_row, "started_at_utc", None),
            current_time_utc=current_time,
            timeout_seconds=timeout_value,
        ):
            continue
        attempt_no = int(getattr(step_row, "attempt_no", 0) or 0)
        can_retry = retry_available_after_attempt(
            attempt_no=attempt_no,
            max_retry_count=int(getattr(step_row, "max_retry_count", 0) or 0),
        )
        retryable_timeout_found = retryable_timeout_found or can_retry
        updates.append(
            StaleRunningStepRecovery(
                step_row=step_row,
                status=ModelReviewChainStepStatus.TIMEOUT if can_retry else ModelReviewChainStepStatus.FAILED,
                attempt_no=attempt_no,
                finished_at_utc=current_time,
                retry_after_utc=current_time if can_retry else None,
                error_code="step_running_timeout" if can_retry else "step_running_timeout_retry_exhausted",
                error_message=(
                    "RUNNING step exceeded MODEL_REVIEW_STEP_RUNNING_TIMEOUT_SECONDS; "
                    "it can be resumed by a later worker tick."
                    if can_retry
                    else "RUNNING step exceeded timeout and retry count is exhausted."
                ),
            )
        )
    return StaleRunningRecoveryPlan(
        updates=tuple(updates),
        retryable_timeout_found=retryable_timeout_found,
        timeout_seconds=timeout_value,
    )


def _step_status_value(step_row: Any) -> str:
    value = getattr(step_row, "status", ModelReviewChainStepStatus.PENDING.value)
    return str(value.value if hasattr(value, "value") else value)


__all__ = [
    "DEFAULT_TEMPORARY_RETRY_AFTER_SECONDS",
    "StaleRunningRecoveryPlan",
    "StaleRunningStepRecovery",
    "build_stale_running_recovery_plan",
    "cli_real_model_cost_confirmation_missing",
    "ensure_utc",
    "retry_available_after_attempt",
    "running_step_timed_out",
    "stage19_result_is_temporary_failure",
    "temporary_retry_after_utc",
    "utc_or_none",
]
