"""DTOs for stage-20C automatic model-review chain worker.

This file belongs to `app/model_review_chain`. It defines only bounded request
and result objects for the 20C worker/watchdog layer.

Called by `app/model_review_chain/worker.py`,
`app/scheduler/jobs/model_review_chain_worker_job.py`,
`scripts/run_model_review_chain_worker.py`, and tests.

External services: none in this file. MySQL: none. Redis: none. Hermes: none.
DeepSeek/GPT/Claude calls: none. Trading execution: none.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_review_chain.schema import (
    DEFAULT_MAX_RETRY_COUNT,
    DEFAULT_SCHEDULER_CHAIN_KEY,
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_PARAMETER_ERROR,
    EXIT_SUCCESS,
    ModelReviewChainStepResult,
)

MODEL_REVIEW_CHAIN_WORKER_STATUS_SUCCESS = "success"
MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED = "skipped"
MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED = "blocked"
MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ModelReviewChainWorkerRequest:
    """Input for one 20C worker tick or one explicit chain resume.

    Parameters: `material_pack_id` starts or resumes work for one stage-18
    material pack; `chain_id` resumes one existing chain. `trigger_source`
    identifies the caller as CLI, scheduler, or worker; this worker calls stage 19
    with the internal `worker` trigger source only after policy checks pass.
    CLI-triggered confirmed ticks must also set `confirm_real_model_cost` before
    any real model cost can be incurred.
    Return value: `ModelReviewChainWorkerResult`.
    Failure scenarios: invalid write mode, disabled config, missing rows,
    budget/whitelist/frequency gates, and database or lock failures.
    External effects: none in this value object.
    """

    material_pack_id: str = ""
    chain_id: str | None = None
    chain_key: str = DEFAULT_SCHEDULER_CHAIN_KEY
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = True
    confirm_write: bool = False
    confirm_real_model_cost: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    limit: int = 20
    max_retry_count: int = DEFAULT_MAX_RETRY_COUNT


@dataclass(frozen=True)
class ModelReviewChainWorkerResult:
    """Compact 20C worker output for CLI, scheduler details, and tests."""

    status: str
    exit_code: int
    trace_id: str
    material_pack_id: str | None = None
    chain_id: str | None = None
    aggregation_run_id: str | None = None
    strategy_signal_run_id: str | None = None
    snapshot_id: str | None = None
    model_review_invoked: bool = False
    model_review_invocation_mode: str = "none"
    model_review_reused: bool = False
    reused_model_analysis_run_id: str | None = None
    model_review_skip_reason: str = "本轮未调用大模型"
    model_review_block_reason: str | None = None
    invoked_model_keys_json: tuple[str, ...] = field(default_factory=tuple)
    invoked_model_roles_json: tuple[str, ...] = field(default_factory=tuple)
    model_review_chain_status: str = "not_started"
    latest_model_review_at_utc: datetime | None = None
    model_review_basis: str = "none"
    model_review_expired: bool = False
    is_final_trading_advice: bool = False
    is_trading_signal: bool = False
    is_executable: bool = False
    auto_trading_allowed: bool = False
    summary_text: str = "本轮未调用大模型"
    error_code: str | None = None
    error_message: str | None = None
    steps: tuple[ModelReviewChainStepResult, ...] = field(default_factory=tuple)
    details: Mapping[str, Any] = field(default_factory=dict)


def build_worker_result(
    *,
    status: str,
    trace_id: str,
    exit_code: int | None = None,
    material_pack_id: str | None = None,
    chain_id: str | None = None,
    aggregation_run_id: str | None = None,
    strategy_signal_run_id: str | None = None,
    snapshot_id: str | None = None,
    model_review_invoked: bool = False,
    model_review_invocation_mode: str = "none",
    model_review_reused: bool = False,
    reused_model_analysis_run_id: str | None = None,
    model_review_skip_reason: str = "本轮未调用大模型",
    model_review_block_reason: str | None = None,
    invoked_model_keys_json: tuple[str, ...] = (),
    invoked_model_roles_json: tuple[str, ...] = (),
    model_review_chain_status: str = "not_started",
    latest_model_review_at_utc: datetime | None = None,
    model_review_basis: str = "none",
    model_review_expired: bool = False,
    summary_text: str = "本轮未调用大模型",
    error_code: str | None = None,
    error_message: str | None = None,
    steps: tuple[ModelReviewChainStepResult, ...] = (),
    details: Mapping[str, Any] | None = None,
) -> ModelReviewChainWorkerResult:
    """Build a result with all trading boundary fields fixed to false."""

    resolved_exit_code = exit_code
    if resolved_exit_code is None:
        if status == MODEL_REVIEW_CHAIN_WORKER_STATUS_SUCCESS:
            resolved_exit_code = EXIT_SUCCESS
        elif status == MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED:
            resolved_exit_code = EXIT_BLOCKED
        elif status == MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED:
            resolved_exit_code = EXIT_SUCCESS
        else:
            resolved_exit_code = EXIT_FAILED
    return ModelReviewChainWorkerResult(
        status=status,
        exit_code=resolved_exit_code,
        trace_id=trace_id,
        material_pack_id=material_pack_id,
        chain_id=chain_id,
        aggregation_run_id=aggregation_run_id,
        strategy_signal_run_id=strategy_signal_run_id,
        snapshot_id=snapshot_id,
        model_review_invoked=model_review_invoked,
        model_review_invocation_mode=model_review_invocation_mode,
        model_review_reused=model_review_reused,
        reused_model_analysis_run_id=reused_model_analysis_run_id,
        model_review_skip_reason=model_review_skip_reason,
        model_review_block_reason=model_review_block_reason,
        invoked_model_keys_json=tuple(invoked_model_keys_json),
        invoked_model_roles_json=tuple(invoked_model_roles_json),
        model_review_chain_status=model_review_chain_status,
        latest_model_review_at_utc=latest_model_review_at_utc,
        model_review_basis=model_review_basis,
        model_review_expired=model_review_expired,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        summary_text=summary_text,
        error_code=error_code,
        error_message=error_message,
        steps=tuple(steps),
        details=dict(details or {}),
    )


def format_model_review_chain_worker_result_lines(result: ModelReviewChainWorkerResult) -> list[str]:
    """Format compact CLI output without raw model prompts or responses."""

    latest = result.latest_model_review_at_utc.isoformat() if result.latest_model_review_at_utc else ""
    return [
        f"status={result.status}",
        f"exit_code={result.exit_code}",
        f"trace_id={result.trace_id}",
        f"material_pack_id={result.material_pack_id or ''}",
        f"chain_id={result.chain_id or ''}",
        f"aggregation_run_id={result.aggregation_run_id or ''}",
        f"strategy_signal_run_id={result.strategy_signal_run_id or ''}",
        f"snapshot_id={result.snapshot_id or ''}",
        f"model_review_invoked={str(result.model_review_invoked).lower()}",
        f"model_review_invocation_mode={result.model_review_invocation_mode}",
        f"model_review_reused={str(result.model_review_reused).lower()}",
        f"reused_model_analysis_run_id={result.reused_model_analysis_run_id or ''}",
        f"model_review_skip_reason={result.model_review_skip_reason}",
        f"model_review_block_reason={result.model_review_block_reason or ''}",
        f"invoked_model_keys_json={_json_text(list(result.invoked_model_keys_json))}",
        f"invoked_model_roles_json={_json_text(list(result.invoked_model_roles_json))}",
        f"model_review_chain_status={result.model_review_chain_status}",
        f"latest_model_review_at_utc={latest}",
        f"model_review_basis={result.model_review_basis}",
        f"model_review_expired={str(result.model_review_expired).lower()}",
        f"is_final_trading_advice={str(result.is_final_trading_advice).lower()}",
        f"is_trading_signal={str(result.is_trading_signal).lower()}",
        f"is_executable={str(result.is_executable).lower()}",
        f"auto_trading_allowed={str(result.auto_trading_allowed).lower()}",
        f"summary_text={result.summary_text}",
        f"error_code={result.error_code or ''}",
        f"error_message={result.error_message or ''}",
    ]


def invalid_worker_request_result(
    *,
    request: ModelReviewChainWorkerRequest,
    message: str,
) -> ModelReviewChainWorkerResult:
    """Return a parameter-error result without touching database or Redis."""

    return build_worker_result(
        status=MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
        exit_code=EXIT_PARAMETER_ERROR,
        trace_id=request.trace_id,
        material_pack_id=request.material_pack_id or None,
        chain_id=request.chain_id,
        model_review_skip_reason="本轮未调用大模型；20C worker 请求参数无效。",
        model_review_block_reason=message,
        summary_text="本轮未调用大模型；20C worker 请求参数无效。",
        error_code="invalid_worker_request",
        error_message=message,
    )


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


__all__ = [
    "MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED",
    "MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED",
    "MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED",
    "MODEL_REVIEW_CHAIN_WORKER_STATUS_SUCCESS",
    "ModelReviewChainWorkerRequest",
    "ModelReviewChainWorkerResult",
    "build_worker_result",
    "format_model_review_chain_worker_result_lines",
    "invalid_worker_request_result",
]
