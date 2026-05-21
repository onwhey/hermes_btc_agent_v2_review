"""Pure state helpers for stage-20B model review chains.

This file belongs to `app/model_review_chain`. It evaluates chain/step status
transitions without accessing databases, Redis, Hermes, scheduler, provider
clients, Kline data, or trading systems.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.model_review_chain.schema import ModelReviewChainStatus, ModelReviewChainStepStatus


RESUMABLE_STEP_STATUSES = frozenset(
    {
        ModelReviewChainStepStatus.FAILED,
        ModelReviewChainStepStatus.TIMEOUT,
        ModelReviewChainStepStatus.RETRY_WAITING,
    }
)


@dataclass(frozen=True)
class ChainStateSummary:
    """Derived aggregate status for one chain."""

    status: ModelReviewChainStatus
    current_step: int
    success_step_count: int
    failed_step_count: int
    timeout_step_count: int
    skipped_step_count: int
    blocked_step_count: int
    summary_text: str
    error_code: str | None
    error_message: str | None


def calculate_chain_state(steps: Iterable[Any], *, total_steps: int) -> ChainStateSummary:
    """Calculate chain status from persisted step rows.

    Parameters: step rows or DTOs with `status` and `step_no`.
    Return value: aggregate chain counts and status.
    Failure scenarios: unknown step status values are treated as failed by the
    caller before persistence; this function normalizes known strings/enums.
    External effects: none.
    """

    step_list = tuple(steps)
    success_count = _count_status(step_list, ModelReviewChainStepStatus.SUCCESS)
    failed_count = _count_status(step_list, ModelReviewChainStepStatus.FAILED)
    timeout_count = _count_status(step_list, ModelReviewChainStepStatus.TIMEOUT)
    skipped_count = _count_status(step_list, ModelReviewChainStepStatus.SKIPPED)
    blocked_count = _count_status(step_list, ModelReviewChainStepStatus.BLOCKED)
    running_count = _count_status(step_list, ModelReviewChainStepStatus.RUNNING)
    retry_waiting_count = _count_status(step_list, ModelReviewChainStepStatus.RETRY_WAITING)
    started_steps = [
        int(getattr(step, "step_no", 0) or 0)
        for step in step_list
        if step_status(step) != ModelReviewChainStepStatus.PENDING
    ]
    current_step = max(started_steps, default=0)

    if total_steps > 0 and success_count == total_steps:
        return ChainStateSummary(
            status=ModelReviewChainStatus.SUCCESS,
            current_step=total_steps,
            success_step_count=success_count,
            failed_step_count=failed_count,
            timeout_step_count=timeout_count,
            skipped_step_count=skipped_count,
            blocked_step_count=blocked_count,
            summary_text="Mock chain completed successfully. No real model was called.",
            error_code=None,
            error_message=None,
        )

    failure_like_count = failed_count + timeout_count + blocked_count + retry_waiting_count
    if failure_like_count > 0 and success_count > 0:
        return ChainStateSummary(
            status=ModelReviewChainStatus.PARTIAL_SUCCESS,
            current_step=current_step,
            success_step_count=success_count,
            failed_step_count=failed_count,
            timeout_step_count=timeout_count,
            skipped_step_count=skipped_count,
            blocked_step_count=blocked_count,
            summary_text="Mock chain is partially complete; incomplete steps must not be treated as a full review.",
            error_code="partial_success",
            error_message="At least one chain step did not complete successfully.",
        )
    if failure_like_count > 0:
        return ChainStateSummary(
            status=ModelReviewChainStatus.FAILED,
            current_step=current_step,
            success_step_count=success_count,
            failed_step_count=failed_count,
            timeout_step_count=timeout_count,
            skipped_step_count=skipped_count,
            blocked_step_count=blocked_count,
            summary_text="Mock chain failed before producing a complete review.",
            error_code="chain_failed",
            error_message="No complete model-review chain is available.",
        )
    if running_count > 0:
        chain_status = ModelReviewChainStatus.RUNNING
        summary = "Mock chain is running. No real model was called."
    else:
        chain_status = ModelReviewChainStatus.PENDING
        summary = "Mock chain is pending. No real model was called."
    return ChainStateSummary(
        status=chain_status,
        current_step=current_step,
        success_step_count=success_count,
        failed_step_count=failed_count,
        timeout_step_count=timeout_count,
        skipped_step_count=skipped_count,
        blocked_step_count=blocked_count,
        summary_text=summary,
        error_code=None,
        error_message=None,
    )


def step_status(step: Any) -> ModelReviewChainStepStatus:
    """Normalize one step row status into an enum."""

    value = getattr(step, "status", ModelReviewChainStepStatus.PENDING)
    if isinstance(value, ModelReviewChainStepStatus):
        return value
    return ModelReviewChainStepStatus(str(value))


def step_retry_is_available(step: Any) -> bool:
    """Return whether a non-success step may be executed again.

    `max_retry_count` is interpreted as retries after the first attempt.
    Example: `attempt_no=1` and `max_retry_count=1` still allows one resume
    attempt; after that second attempt, retry use is exhausted.
    """

    status = step_status(step)
    if status == ModelReviewChainStepStatus.SUCCESS:
        return False
    if status == ModelReviewChainStepStatus.PENDING:
        return True
    attempt_no = int(getattr(step, "attempt_no", 0) or 0)
    max_retry_count = int(getattr(step, "max_retry_count", 0) or 0)
    retries_used = max(0, attempt_no - 1)
    return retries_used < max_retry_count


def step_is_resumable(step: Any) -> bool:
    """Return whether resume mode should consider this step for execution."""

    return step_status(step) in RESUMABLE_STEP_STATUSES


def _count_status(steps: Iterable[Any], expected_status: ModelReviewChainStepStatus) -> int:
    return sum(1 for step in steps if step_status(step) == expected_status)


__all__ = [
    "ChainStateSummary",
    "RESUMABLE_STEP_STATUSES",
    "calculate_chain_state",
    "step_is_resumable",
    "step_retry_is_available",
    "step_status",
]
