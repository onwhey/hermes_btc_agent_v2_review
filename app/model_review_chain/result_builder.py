"""Result builders for stage-20B model review chains.

This file belongs to `app/model_review_chain`. It validates service requests
and formats compact service results from chain/step rows. It does not query or
write databases, call provider clients, touch Redis, send Hermes, connect
scheduler, modify formal Kline data, or perform trading.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.model_review_chain.payload_builder import row_optional
from app.model_review_chain.schema import (
    DEFAULT_CHAIN_KEY,
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    ModelReviewChainRequest,
    ModelReviewChainResult,
    ModelReviewChainStatus,
    ModelReviewChainStepResult,
    ModelReviewChainStepStatus,
)
from app.model_review_chain.state_machine import step_status


def validate_chain_request(
    request: ModelReviewChainRequest,
    *,
    allowed_trigger_sources: frozenset[str],
) -> ModelReviewChainResult | None:
    """Validate one chain request without touching the database."""

    chain_id = request.chain_id or ""
    if request.trigger_source not in allowed_trigger_sources:
        return build_failed_result(
            request=request,
            chain_id=chain_id,
            error_code="trigger_source_not_allowed",
            error_message="Stage-20B chain is CLI-only and cannot be triggered by scheduler.",
            exit_code=EXIT_PARAMETER_ERROR,
        )
    if request.dry_run and request.confirm_write:
        return build_failed_result(
            request=request,
            chain_id=chain_id,
            error_code="invalid_write_mode",
            error_message="Choose either dry-run or confirm-write.",
            exit_code=EXIT_PARAMETER_ERROR,
        )
    if not request.dry_run and not request.confirm_write:
        return build_failed_result(
            request=request,
            chain_id=chain_id,
            error_code="missing_write_mode",
            error_message="Write mode must be explicit.",
            exit_code=EXIT_PARAMETER_ERROR,
        )
    if request.max_retry_count < 0:
        return build_failed_result(
            request=request,
            chain_id=chain_id,
            error_code="invalid_max_retry_count",
            error_message="max_retry_count must be zero or greater.",
            exit_code=EXIT_PARAMETER_ERROR,
        )
    if request.simulate_step_failure is not None and request.simulate_step_failure <= 0:
        return build_failed_result(
            request=request,
            chain_id=chain_id,
            error_code="invalid_simulate_step_failure",
            error_message="simulate_step_failure must be a positive step number.",
            exit_code=EXIT_PARAMETER_ERROR,
        )
    if request.resume and not (request.chain_id or "").strip():
        return build_failed_result(
            request=request,
            chain_id=chain_id,
            error_code="missing_chain_id",
            error_message="resume mode requires chain_id.",
            exit_code=EXIT_PARAMETER_ERROR,
        )
    if not request.resume and not request.material_pack_id.strip():
        return build_failed_result(
            request=request,
            chain_id=chain_id,
            error_code="missing_material_pack_id",
            error_message="create mode requires material_pack_id.",
            exit_code=EXIT_PARAMETER_ERROR,
        )
    return None


def build_result_from_chain_rows(
    *,
    request: ModelReviewChainRequest,
    chain_row: Any,
    profile: Any,
    state: Any,
    steps: tuple[ModelReviewChainStepResult, ...],
    mock_execution_count: int,
) -> ModelReviewChainResult:
    """Build the compact service result from persisted or transient rows."""

    return ModelReviewChainResult(
        status=state.status,
        exit_code=EXIT_SUCCESS if state.status == ModelReviewChainStatus.SUCCESS else EXIT_FAILED,
        chain_id=str(getattr(chain_row, "chain_id", request.chain_id or "")),
        material_pack_id=row_optional(chain_row, "material_pack_id"),
        aggregation_run_id=row_optional(chain_row, "aggregation_run_id"),
        strategy_signal_run_id=row_optional(chain_row, "strategy_signal_run_id"),
        snapshot_id=row_optional(chain_row, "snapshot_id"),
        trace_id=str(getattr(chain_row, "trace_id", request.trace_id)),
        chain_key=profile.chain_key,
        chain_profile_version=profile.chain_profile_version,
        current_step=state.current_step,
        total_steps=int(getattr(chain_row, "total_steps", len(steps)) or len(steps)),
        success_step_count=state.success_step_count,
        failed_step_count=state.failed_step_count,
        timeout_step_count=state.timeout_step_count,
        skipped_step_count=state.skipped_step_count,
        blocked_step_count=state.blocked_step_count,
        model_review_invoked=False,
        real_model_invoked=False,
        mock_step_execution_count=mock_execution_count,
        resumed=request.resume,
        dry_run=request.dry_run,
        summary_text=state.summary_text,
        error_code=state.error_code,
        error_message=state.error_message,
        steps=steps,
        details={
            "real_model_invoked": False,
            "scheduler_connected": False,
            "mock_only": True,
        },
    )


def build_step_result(
    step_row: Any,
    *,
    skipped_due_to_success_resume: bool = False,
    retry_blocked: bool = False,
) -> ModelReviewChainStepResult:
    """Build one compact step result from a row."""

    return ModelReviewChainStepResult(
        chain_step_id=str(getattr(step_row, "chain_step_id", "")),
        step_no=int(getattr(step_row, "step_no", 0) or 0),
        model_key=str(getattr(step_row, "model_key", "")),
        model_role=str(getattr(step_row, "model_role", "")),
        status=step_status(step_row),
        attempt_no=int(getattr(step_row, "attempt_no", 0) or 0),
        max_retry_count=int(getattr(step_row, "max_retry_count", 0) or 0),
        model_analysis_run_id=row_optional(step_row, "model_analysis_run_id"),
        parent_model_analysis_run_id=row_optional(step_row, "parent_model_analysis_run_id"),
        skipped_due_to_success_resume=skipped_due_to_success_resume,
        retry_blocked=retry_blocked,
        error_code=row_optional(step_row, "error_code"),
        error_message=row_optional(step_row, "error_message"),
    )


def merge_step_results(
    step_rows: Iterable[Any],
    result_steps: list[ModelReviewChainStepResult],
) -> tuple[ModelReviewChainStepResult, ...]:
    """Return exactly one result per step in step order."""

    by_id = {step.chain_step_id: step for step in result_steps}
    merged = []
    for step_row in step_rows:
        chain_step_id = str(getattr(step_row, "chain_step_id", ""))
        merged.append(by_id.get(chain_step_id) or build_step_result(step_row))
    return tuple(merged)


def latest_successful_parent(step_rows: Iterable[Any]) -> str | None:
    """Return the latest successful model_analysis_run_id in a chain."""

    latest: tuple[int, str] | None = None
    for step_row in step_rows:
        if step_status(step_row) != ModelReviewChainStepStatus.SUCCESS:
            continue
        model_run_id = row_optional(step_row, "model_analysis_run_id")
        if not model_run_id:
            continue
        candidate = (int(getattr(step_row, "step_no", 0) or 0), model_run_id)
        if latest is None or candidate[0] > latest[0]:
            latest = candidate
    return latest[1] if latest else None


def build_blocked_result(
    *,
    request: ModelReviewChainRequest,
    chain_id: str,
    error_code: str,
    error_message: str,
    chain_key: str | None = None,
    chain_profile_version: str | None = None,
) -> ModelReviewChainResult:
    """Build a blocked service result that explicitly states no real model ran."""

    return ModelReviewChainResult(
        status=ModelReviewChainStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        chain_id=chain_id,
        material_pack_id=request.material_pack_id or None,
        aggregation_run_id=None,
        strategy_signal_run_id=None,
        snapshot_id=None,
        trace_id=request.trace_id,
        chain_key=chain_key or request.chain_key or DEFAULT_CHAIN_KEY,
        chain_profile_version=chain_profile_version or "",
        resumed=request.resume,
        dry_run=request.dry_run,
        summary_text="Stage-20B chain blocked. No real model was called.",
        error_code=error_code,
        error_message=error_message,
        details={"real_model_invoked": False, "scheduler_connected": False, "mock_only": True},
    )


def build_failed_result(
    *,
    request: ModelReviewChainRequest,
    chain_id: str,
    error_code: str,
    error_message: str,
    chain_key: str | None = None,
    chain_profile_version: str | None = None,
    exit_code: int = EXIT_FAILED,
) -> ModelReviewChainResult:
    """Build a failed service result that explicitly states no real model ran."""

    return ModelReviewChainResult(
        status=ModelReviewChainStatus.FAILED,
        exit_code=exit_code,
        chain_id=chain_id,
        material_pack_id=request.material_pack_id or None,
        aggregation_run_id=None,
        strategy_signal_run_id=None,
        snapshot_id=None,
        trace_id=request.trace_id,
        chain_key=chain_key or request.chain_key or DEFAULT_CHAIN_KEY,
        chain_profile_version=chain_profile_version or "",
        resumed=request.resume,
        dry_run=request.dry_run,
        summary_text="Stage-20B chain failed. No real model was called.",
        error_code=error_code,
        error_message=error_message,
        details={"real_model_invoked": False, "scheduler_connected": False, "mock_only": True},
    )


__all__ = [
    "build_blocked_result",
    "build_failed_result",
    "build_result_from_chain_rows",
    "build_step_result",
    "latest_successful_parent",
    "merge_step_results",
    "validate_chain_request",
]
