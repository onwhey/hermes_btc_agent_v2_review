"""Result builders for stage-20C model-review chain worker.

This file belongs to `app/model_review_chain`. It formats bounded worker
results from 20A aggregation decisions and 20B chain rows.

Called by `app/model_review_chain/worker.py`.
External services: none. MySQL: none. Redis: none. Hermes: none.
DeepSeek/GPT/Claude calls: none. Trading execution: none.
"""

from __future__ import annotations

from typing import Any

from app.model_review_chain.result_builder import build_step_result
from app.model_review_chain.schema import (
    EXIT_SUCCESS,
    ModelReviewChainStatus,
    ModelReviewChainStepResult,
)
from app.model_review_chain.worker_schema import (
    MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
    MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
    MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED,
    MODEL_REVIEW_CHAIN_WORKER_STATUS_SUCCESS,
    ModelReviewChainWorkerRequest,
    ModelReviewChainWorkerResult,
    build_worker_result,
)


def config_skipped_result(
    *,
    request: ModelReviewChainWorkerRequest,
    error_code: str,
    reason: str,
) -> ModelReviewChainWorkerResult:
    """Return a config-disabled worker result without touching stage 19."""

    return build_worker_result(
        status=MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED,
        trace_id=request.trace_id,
        material_pack_id=request.material_pack_id or None,
        chain_id=request.chain_id,
        model_review_skip_reason=f"本轮未调用大模型；{reason}",
        model_review_block_reason=reason,
        summary_text=f"本轮未调用大模型；{reason}",
        error_code=error_code,
        error_message=reason,
    )


def result_from_reusable_aggregation(
    *,
    request: ModelReviewChainWorkerRequest,
    aggregation_result: Any,
) -> ModelReviewChainWorkerResult:
    """Return a result when 20A found current or reusable stage-19 review."""

    status_text = (
        MODEL_REVIEW_CHAIN_WORKER_STATUS_SUCCESS
        if not getattr(aggregation_result, "model_review_reused", False)
        else MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED
    )
    return build_worker_result(
        status=status_text,
        exit_code=EXIT_SUCCESS,
        trace_id=request.trace_id,
        material_pack_id=aggregation_result.material_pack_id,
        aggregation_run_id=aggregation_result.aggregation_run_id,
        strategy_signal_run_id=aggregation_result.strategy_signal_run_id,
        snapshot_id=aggregation_result.snapshot_id,
        model_review_invoked=False,
        model_review_invocation_mode=aggregation_result.model_review_invocation_mode,
        model_review_reused=bool(aggregation_result.model_review_reused),
        reused_model_analysis_run_id=aggregation_result.reused_model_analysis_run_id,
        model_review_skip_reason=aggregation_result.model_review_skip_reason or "本轮未调用大模型",
        model_review_chain_status="not_started",
        latest_model_review_at_utc=aggregation_result.latest_model_review_at_utc,
        model_review_basis=aggregation_result.model_review_basis,
        model_review_expired=bool(aggregation_result.model_review_expired),
        summary_text=aggregation_result.summary_text,
        details={"aggregation_status": aggregation_result.status.value},
    )


def result_from_blocked_aggregation(
    *,
    request: ModelReviewChainWorkerRequest,
    aggregation_result: Any,
    override_error_code: str | None = None,
    override_block_reason: str | None = None,
) -> ModelReviewChainWorkerResult:
    """Return a blocked result from a 20A no-result or expired decision."""

    error_code = override_error_code or aggregation_result.error_code
    block_reason = override_block_reason or aggregation_result.model_review_block_reason
    return build_worker_result(
        status=MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
        trace_id=request.trace_id,
        material_pack_id=aggregation_result.material_pack_id,
        aggregation_run_id=aggregation_result.aggregation_run_id,
        strategy_signal_run_id=aggregation_result.strategy_signal_run_id,
        snapshot_id=aggregation_result.snapshot_id,
        model_review_invoked=False,
        model_review_invocation_mode="none",
        model_review_reused=False,
        reused_model_analysis_run_id=None,
        model_review_skip_reason=aggregation_result.model_review_skip_reason or "本轮未调用大模型",
        model_review_block_reason=block_reason,
        latest_model_review_at_utc=aggregation_result.latest_model_review_at_utc,
        model_review_basis=aggregation_result.model_review_basis,
        model_review_expired=bool(aggregation_result.model_review_expired),
        summary_text=aggregation_result.summary_text,
        error_code=error_code,
        error_message=aggregation_result.error_message,
        details={"aggregation_status": aggregation_result.status.value},
    )


def dry_run_would_create_or_resume_result(
    *,
    request: ModelReviewChainWorkerRequest,
    aggregation_result: Any,
    chain_id: str | None,
) -> ModelReviewChainWorkerResult:
    """Return the dry-run result for material-driven worker creation."""

    return build_worker_result(
        status=MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED,
        trace_id=request.trace_id,
        material_pack_id=request.material_pack_id,
        chain_id=chain_id,
        aggregation_run_id=aggregation_result.aggregation_run_id,
        strategy_signal_run_id=aggregation_result.strategy_signal_run_id,
        snapshot_id=aggregation_result.snapshot_id,
        model_review_skip_reason="本轮未调用大模型；dry-run 只判断需要创建或恢复 chain，不写库也不调用模型。",
        model_review_block_reason="dry_run",
        model_review_basis=aggregation_result.model_review_basis,
        model_review_expired=bool(aggregation_result.model_review_expired),
        summary_text="本轮未调用大模型；dry-run 只判断需要创建或恢复 chain，不写库也不调用模型。",
        details={"would_create_or_resume_chain": True, "aggregation_error_code": aggregation_result.error_code},
    )


def dry_run_would_resume_result(
    *,
    request: ModelReviewChainWorkerRequest,
    chain_row: Any,
    step_rows: tuple[Any, ...],
) -> ModelReviewChainWorkerResult:
    """Return the dry-run result for an existing chain resume."""

    return build_worker_result(
        status=MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED,
        trace_id=request.trace_id,
        material_pack_id=str(getattr(chain_row, "material_pack_id", "") or "") or None,
        chain_id=str(getattr(chain_row, "chain_id", "") or "") or None,
        aggregation_run_id=str(getattr(chain_row, "aggregation_run_id", "") or "") or None,
        strategy_signal_run_id=str(getattr(chain_row, "strategy_signal_run_id", "") or "") or None,
        snapshot_id=str(getattr(chain_row, "snapshot_id", "") or "") or None,
        model_review_skip_reason="本轮未调用大模型；dry-run 只判断可恢复 step，不写库也不调用模型。",
        model_review_block_reason="dry_run",
        model_review_chain_status=str(getattr(chain_row, "status", "") or "pending"),
        model_review_basis="existing_chain_dry_run",
        summary_text="本轮未调用大模型；dry-run 只判断可恢复 step，不写库也不调用模型。",
        steps=tuple(build_step_result(step) for step in step_rows),
    )


def chain_result_without_step(
    *,
    request: ModelReviewChainWorkerRequest,
    chain_row: Any,
    error_code: str,
) -> ModelReviewChainWorkerResult:
    """Return a blocked result for a chain that cannot be resumed."""

    return build_worker_result(
        status=MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
        trace_id=request.trace_id,
        material_pack_id=str(getattr(chain_row, "material_pack_id", "") or "") or None,
        chain_id=str(getattr(chain_row, "chain_id", "") or "") or None,
        aggregation_run_id=str(getattr(chain_row, "aggregation_run_id", "") or "") or None,
        strategy_signal_run_id=str(getattr(chain_row, "strategy_signal_run_id", "") or "") or None,
        snapshot_id=str(getattr(chain_row, "snapshot_id", "") or "") or None,
        model_review_skip_reason="本轮未调用大模型；model_review_chain 不能恢复。",
        model_review_block_reason=error_code,
        model_review_chain_status=str(getattr(chain_row, "status", "") or "blocked"),
        model_review_basis="chain_not_runnable",
        summary_text="本轮未调用大模型；model_review_chain 不能恢复。",
        error_code=error_code,
    )


def result_from_completed_or_incomplete_chain(
    *,
    request: ModelReviewChainWorkerRequest,
    chain_row: Any,
    state_status: str,
    steps: tuple[ModelReviewChainStepResult, ...],
    invoked_keys: tuple[str, ...],
    invoked_roles: tuple[str, ...],
) -> ModelReviewChainWorkerResult:
    """Return worker result from final chain state after a tick."""

    invoked = bool(invoked_keys)
    complete = state_status == ModelReviewChainStatus.SUCCESS.value
    status = MODEL_REVIEW_CHAIN_WORKER_STATUS_SUCCESS if complete else MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED
    summary = (
        "20C worker 已调用大模型并完成 chain；仍不是最终交易建议。"
        if complete and invoked
        else "本轮未形成完整模型接力审查；partial_success/failed/blocked 不能伪装成完整成功。"
    )
    return build_worker_result(
        status=status,
        trace_id=request.trace_id,
        material_pack_id=str(getattr(chain_row, "material_pack_id", "") or "") or None,
        chain_id=str(getattr(chain_row, "chain_id", "") or "") or None,
        aggregation_run_id=str(getattr(chain_row, "aggregation_run_id", "") or "") or None,
        strategy_signal_run_id=str(getattr(chain_row, "strategy_signal_run_id", "") or "") or None,
        snapshot_id=str(getattr(chain_row, "snapshot_id", "") or "") or None,
        model_review_invoked=invoked,
        model_review_invocation_mode="worker_real_model" if invoked else "none",
        model_review_skip_reason="" if invoked else "本轮未调用大模型；没有可执行 step。",
        model_review_block_reason=None if complete else "chain_not_complete",
        invoked_model_keys_json=invoked_keys,
        invoked_model_roles_json=invoked_roles,
        model_review_chain_status=state_status,
        model_review_basis="automatic_chain",
        summary_text=summary,
        error_code=None if complete else "chain_not_complete",
        steps=steps,
    )


def augment_result_with_chain(
    result: ModelReviewChainWorkerResult,
    *,
    chain_row: Any,
    state_status: str,
    steps: tuple[ModelReviewChainStepResult, ...],
    invoked_keys: tuple[str, ...],
    invoked_roles: tuple[str, ...],
) -> ModelReviewChainWorkerResult:
    """Return a result enriched with latest chain status and step list."""

    return build_worker_result(
        status=result.status,
        exit_code=result.exit_code,
        trace_id=result.trace_id,
        material_pack_id=result.material_pack_id or str(getattr(chain_row, "material_pack_id", "") or "") or None,
        chain_id=result.chain_id or str(getattr(chain_row, "chain_id", "") or "") or None,
        aggregation_run_id=result.aggregation_run_id or str(getattr(chain_row, "aggregation_run_id", "") or "") or None,
        strategy_signal_run_id=(
            result.strategy_signal_run_id or str(getattr(chain_row, "strategy_signal_run_id", "") or "") or None
        ),
        snapshot_id=result.snapshot_id or str(getattr(chain_row, "snapshot_id", "") or "") or None,
        model_review_invoked=result.model_review_invoked or bool(invoked_keys),
        model_review_invocation_mode="worker_real_model" if invoked_keys else result.model_review_invocation_mode,
        model_review_reused=result.model_review_reused,
        reused_model_analysis_run_id=result.reused_model_analysis_run_id,
        model_review_skip_reason=result.model_review_skip_reason,
        model_review_block_reason=result.model_review_block_reason,
        invoked_model_keys_json=invoked_keys or result.invoked_model_keys_json,
        invoked_model_roles_json=invoked_roles or result.invoked_model_roles_json,
        model_review_chain_status=state_status,
        latest_model_review_at_utc=result.latest_model_review_at_utc,
        model_review_basis=result.model_review_basis,
        model_review_expired=result.model_review_expired,
        summary_text=result.summary_text,
        error_code=result.error_code,
        error_message=result.error_message,
        steps=steps,
        details=result.details,
    )


def chain_request_adapter(request: ModelReviewChainWorkerRequest) -> Any:
    """Return a minimal object accepted by existing 20B payload builders."""

    return _RequestAdapter(
        material_pack_id=request.material_pack_id,
        chain_id=request.chain_id,
        chain_key=request.chain_key,
        trigger_source=request.trigger_source,
        resume=False,
        dry_run=request.dry_run,
        confirm_write=request.confirm_write,
        created_by=request.created_by,
        trace_id=request.trace_id,
        simulate_step_failure=None,
        max_retry_count=request.max_retry_count,
    )


def worker_failed_result(
    *,
    request: ModelReviewChainWorkerRequest,
    error_code: str,
    message: str,
    chain_row: Any | None = None,
) -> ModelReviewChainWorkerResult:
    """Return a failed worker result for database or orchestration failures."""

    return build_worker_result(
        status=MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
        trace_id=request.trace_id,
        material_pack_id=(
            str(getattr(chain_row, "material_pack_id", "") or "") or request.material_pack_id or None
        ),
        chain_id=str(getattr(chain_row, "chain_id", "") or "") or request.chain_id,
        aggregation_run_id=str(getattr(chain_row, "aggregation_run_id", "") or "") or None,
        strategy_signal_run_id=str(getattr(chain_row, "strategy_signal_run_id", "") or "") or None,
        snapshot_id=str(getattr(chain_row, "snapshot_id", "") or "") or None,
        model_review_skip_reason=f"本轮未调用大模型；{message}",
        model_review_block_reason=error_code,
        summary_text=f"本轮未调用大模型；{message}",
        error_code=error_code,
        error_message=message,
    )


class _RequestAdapter:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


__all__ = [
    "augment_result_with_chain",
    "chain_request_adapter",
    "chain_result_without_step",
    "config_skipped_result",
    "dry_run_would_create_or_resume_result",
    "dry_run_would_resume_result",
    "result_from_blocked_aggregation",
    "result_from_completed_or_incomplete_chain",
    "result_from_reusable_aggregation",
    "worker_failed_result",
]
