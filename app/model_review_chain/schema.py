"""Types for stage-20B model review chain state machine.

This file belongs to `app/model_review_chain`. It defines constants, enums,
request/result DTOs, and repository payloads for the deterministic stage-20B
mock chain framework.

Called by: `app/model_review_chain/service.py`,
`app/model_review_chain/repository.py`, `scripts/run_model_review_chain.py`,
and tests.

External services: none. MySQL: none in this file. Redis: none. Hermes: none.
DeepSeek/GPT/Claude calls: none. Trading execution: none.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI

MODEL_REVIEW_CHAIN_EVENT_SOURCE = "app.model_review_chain.service"
DEFAULT_CHAIN_KEY = "mock_deepseek_then_gpt_risk_review"
DEFAULT_SCHEDULER_CHAIN_KEY = "scheduler_deepseek_pro_review"
SCHEDULER_RELAY_CHAIN_KEY = "scheduler_deepseek_pro_then_flash_review"
MOCK_CHAIN_PROFILE_VERSION = "mock_chain_profile_v1"
SCHEDULER_CHAIN_PROFILE_VERSION = "scheduler_chain_profile_v1"
DEFAULT_MAX_RETRY_COUNT = 1

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4


class ModelReviewChainStatus(str, Enum):
    """Status values for one stage-20B chain run."""

    PENDING = "pending"
    RUNNING = "running"
    PARTIAL_SUCCESS = "partial_success"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"


class ModelReviewChainStepStatus(str, Enum):
    """Status values for one stage-20B chain step."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    RETRY_WAITING = "retry_waiting"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ModelReviewChainRequest:
    """Input for one stage-20B chain operation.

    Parameters: create mode uses `material_pack_id` and `chain_key`; resume
    mode uses `chain_id`. `trigger_source` is currently CLI-only, and dry-run
    is the safe default.
    Return value: `ModelReviewChainResult` from the service.
    Failure scenarios: invalid parameters, missing material/chain rows,
    unknown chain profiles, and persistence failures are converted into
    structured results by the service.
    External effects: none in this value object.
    """

    material_pack_id: str = ""
    chain_id: str | None = None
    chain_key: str = DEFAULT_CHAIN_KEY
    trigger_source: str = TRIGGER_SOURCE_CLI
    resume: bool = False
    dry_run: bool = True
    confirm_write: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    simulate_step_failure: int | None = None
    max_retry_count: int = DEFAULT_MAX_RETRY_COUNT


@dataclass(frozen=True)
class ChainStepDefinition:
    """Static definition for one mock chain step."""

    step_no: int
    model_key: str
    model_role: str


@dataclass(frozen=True)
class ChainProfile:
    """Static chain profile resolved from a chain key."""

    chain_key: str
    chain_profile_version: str
    steps: tuple[ChainStepDefinition, ...]


@dataclass(frozen=True)
class ModelReviewChainRunPersistencePayload:
    """Repository payload for one `model_review_chain_run` row.

    This payload stores only compact chain state, counts, and source IDs. It
    never stores full prompts, full provider responses, final trading advice,
    private trading state, or Kline arrays.
    """

    chain_id: str
    material_pack_id: str
    aggregation_run_id: str | None
    strategy_signal_run_id: str | None
    snapshot_id: str | None
    symbol: str | None
    base_interval: str | None
    higher_interval: str | None
    chain_key: str
    chain_profile_version: str
    status: ModelReviewChainStatus
    trigger_source: str
    trace_id: str
    current_step: int
    total_steps: int
    success_step_count: int
    failed_step_count: int
    timeout_step_count: int
    skipped_step_count: int
    blocked_step_count: int
    max_retry_count: int
    summary_text: str
    error_code: str | None
    error_message: str | None
    is_final_trading_advice: bool
    is_trading_signal: bool
    is_executable: bool
    auto_trading_allowed: bool


@dataclass(frozen=True)
class ModelReviewChainStepPersistencePayload:
    """Repository payload for one `model_review_chain_step` row."""

    chain_step_id: str
    chain_id: str
    step_no: int
    model_key: str
    model_role: str
    parent_step_id: str | None
    parent_model_analysis_run_id: str | None
    model_analysis_run_id: str | None
    status: ModelReviewChainStepStatus
    attempt_no: int
    max_retry_count: int
    started_at_utc: datetime | None
    finished_at_utc: datetime | None
    error_code: str | None
    error_message: str | None
    retry_after_utc: datetime | None
    step_input_hash: str | None
    step_output_hash: str | None


@dataclass(frozen=True)
class ModelReviewChainStepResult:
    """Compact step state returned to CLI and tests."""

    chain_step_id: str
    step_no: int
    model_key: str
    model_role: str
    status: ModelReviewChainStepStatus
    attempt_no: int
    max_retry_count: int
    model_analysis_run_id: str | None = None
    parent_model_analysis_run_id: str | None = None
    skipped_due_to_success_resume: bool = False
    retry_blocked: bool = False
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ModelReviewChainResult:
    """Compact service result returned to CLI and tests."""

    status: ModelReviewChainStatus
    exit_code: int
    chain_id: str
    material_pack_id: str | None
    aggregation_run_id: str | None
    strategy_signal_run_id: str | None
    snapshot_id: str | None
    trace_id: str
    chain_key: str
    chain_profile_version: str
    current_step: int = 0
    total_steps: int = 0
    success_step_count: int = 0
    failed_step_count: int = 0
    timeout_step_count: int = 0
    skipped_step_count: int = 0
    blocked_step_count: int = 0
    model_review_invoked: bool = False
    real_model_invoked: bool = False
    mock_step_execution_count: int = 0
    resumed: bool = False
    dry_run: bool = True
    is_final_trading_advice: bool = False
    is_trading_signal: bool = False
    is_executable: bool = False
    auto_trading_allowed: bool = False
    summary_text: str = "No real model was called in this stage-20B chain run."
    error_code: str | None = None
    error_message: str | None = None
    steps: tuple[ModelReviewChainStepResult, ...] = field(default_factory=tuple)
    details: Mapping[str, Any] = field(default_factory=dict)


def format_model_review_chain_result_lines(result: ModelReviewChainResult) -> list[str]:
    """Format compact CLI output without raw material or model-response dumps."""

    return [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"chain_id={result.chain_id}",
        f"material_pack_id={result.material_pack_id or ''}",
        f"aggregation_run_id={result.aggregation_run_id or ''}",
        f"strategy_signal_run_id={result.strategy_signal_run_id or ''}",
        f"snapshot_id={result.snapshot_id or ''}",
        f"chain_key={result.chain_key}",
        f"chain_profile_version={result.chain_profile_version}",
        f"current_step={result.current_step}",
        f"total_steps={result.total_steps}",
        f"success_step_count={result.success_step_count}",
        f"failed_step_count={result.failed_step_count}",
        f"timeout_step_count={result.timeout_step_count}",
        f"skipped_step_count={result.skipped_step_count}",
        f"blocked_step_count={result.blocked_step_count}",
        f"model_review_invoked={str(result.model_review_invoked).lower()}",
        f"real_model_invoked={str(result.real_model_invoked).lower()}",
        f"mock_step_execution_count={result.mock_step_execution_count}",
        f"resumed={str(result.resumed).lower()}",
        f"dry_run={str(result.dry_run).lower()}",
        f"is_final_trading_advice={str(result.is_final_trading_advice).lower()}",
        f"is_trading_signal={str(result.is_trading_signal).lower()}",
        f"is_executable={str(result.is_executable).lower()}",
        f"auto_trading_allowed={str(result.auto_trading_allowed).lower()}",
        f"summary_text={result.summary_text}",
        f"error_code={result.error_code or ''}",
        f"error_message={result.error_message or ''}",
        "steps=" + json_text([_step_to_dict(step) for step in result.steps]),
    ]


def json_text(value: Any) -> str:
    """Return deterministic JSON text for compact persistence and CLI output."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _step_to_dict(step: ModelReviewChainStepResult) -> dict[str, Any]:
    return {
        "attempt_no": step.attempt_no,
        "chain_step_id": step.chain_step_id,
        "error_code": step.error_code,
        "error_message": step.error_message,
        "max_retry_count": step.max_retry_count,
        "model_analysis_run_id": step.model_analysis_run_id,
        "model_key": step.model_key,
        "model_role": step.model_role,
        "parent_model_analysis_run_id": step.parent_model_analysis_run_id,
        "retry_blocked": step.retry_blocked,
        "skipped_due_to_success_resume": step.skipped_due_to_success_resume,
        "status": step.status.value,
        "step_no": step.step_no,
    }


__all__ = [
    "DEFAULT_CHAIN_KEY",
    "DEFAULT_MAX_RETRY_COUNT",
    "DEFAULT_SCHEDULER_CHAIN_KEY",
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "MOCK_CHAIN_PROFILE_VERSION",
    "SCHEDULER_CHAIN_PROFILE_VERSION",
    "SCHEDULER_RELAY_CHAIN_KEY",
    "MODEL_REVIEW_CHAIN_EVENT_SOURCE",
    "ChainProfile",
    "ChainStepDefinition",
    "ModelReviewChainRequest",
    "ModelReviewChainResult",
    "ModelReviewChainRunPersistencePayload",
    "ModelReviewChainStatus",
    "ModelReviewChainStepPersistencePayload",
    "ModelReviewChainStepResult",
    "ModelReviewChainStepStatus",
    "format_model_review_chain_result_lines",
    "json_text",
]
