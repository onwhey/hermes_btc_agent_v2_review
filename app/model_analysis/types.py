"""Types for stage-19 model analysis review gate.

This file belongs to `app/model_analysis`. It defines only constants, enums,
request/result DTOs, and repository payloads for stage-19A.

Called by: `app/model_analysis/service.py`, repository/provider modules,
`scripts/run_model_analysis.py`, and tests.
External services: none. MySQL: none in this file. Redis: none. Hermes: none
in this file. Real model calls: none. Trading execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI

MODEL_REVIEW_PROVIDER_MOCK = "mock"
MODEL_REVIEW_MOCK_MODEL_NAME = "mock-reviewer"
MODEL_REVIEW_MOCK_MODEL_VERSION = "mock_v1"
MODEL_REVIEW_MODE_DEFAULT = "single"
MODEL_REVIEW_MODEL_KEY_DEFAULT = "mock_review"
MODEL_REVIEW_MODEL_ROLE_DEFAULT = "review_gate"
MODEL_ANALYSIS_EVENT_SOURCE = "app.model_analysis.service"

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4


class ModelAnalysisStatus(str, Enum):
    """Status values for one model analysis attempt."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class ReviewDecision(str, Enum):
    """Allowed review conclusions; none of them is a trading instruction."""

    ACCEPT_FOR_FURTHER_REVIEW = "accept_for_further_review"
    REJECT_CANDIDATE = "reject_candidate"
    REQUIRE_MORE_EVIDENCE = "require_more_evidence"
    WAIT = "wait"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    BLOCKED = "blocked"


class ModelAnalysisHermesStatus(str, Enum):
    """Compact Hermes dispatch status stored on model analysis run rows."""

    DISABLED = "disabled"
    NOT_REQUIRED = "not_required"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class ModelAnalysisRequest:
    """Input for one stage-19 model review attempt.

    Parameters: `material_pack_id` identifies an existing stage-18 final
    material pack; `trigger_source` is currently `cli`; dry-run is safe default.
    Return value: `ModelAnalysisServiceResult` from the service.
    Failure scenarios: invalid parameters, missing material pack, input/output
    size limit breaches, schema invalid output, and persistence failures are
    converted into structured results by the service.
    External effects: none in this value object.
    """

    material_pack_id: str
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = True
    confirm_write: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    use_real_model: bool = False


@dataclass(frozen=True)
class PromptBuildResult:
    """Compact review input built from one analysis material pack."""

    prompt_text: str
    input_summary: Mapping[str, Any]
    input_material_hash: str
    input_char_count: int
    input_byte_count: int
    strategy_item_count: int
    truncated_strategy_count: int


@dataclass(frozen=True)
class ModelProviderResult:
    """Structured result returned by a stage-19 provider."""

    output: Mapping[str, Any]
    output_char_count: int
    output_byte_count: int


@dataclass(frozen=True)
class SchemaValidationResult:
    """Validation outcome for provider output."""

    is_valid: bool
    normalized_output: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ModelAnalysisRunPersistencePayload:
    """Repository payload for one `model_analysis_run` attempt row.

    This payload stores only compact input summaries and counts. It never
    stores a full prompt, full provider response, final trading advice, private
    trading state, or Kline arrays.
    """

    model_analysis_run_id: str
    review_version_key: str
    material_pack_id: str
    aggregation_run_id: str
    strategy_signal_run_id: str
    snapshot_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    review_schema_version: str
    prompt_template_version: str
    model_provider: str
    model_name: str
    model_version: str
    review_mode: str
    model_key: str
    model_role: str
    analysis_mode: str
    chain_id: str | None
    chain_step: int | None
    parent_model_analysis_run_id: str | None
    comparison_group_id: str | None
    status: ModelAnalysisStatus
    input_material_hash: str
    input_summary_json: Mapping[str, Any]
    input_char_count: int
    input_byte_count: int
    output_char_count: int
    output_byte_count: int
    is_final_trading_advice: bool
    is_trading_signal: bool
    is_executable: bool
    auto_trading_allowed: bool
    human_review_required: bool
    trigger_source: str
    created_by: str
    trace_id: str
    error_code: str | None
    error_message: str | None
    hermes_enabled: bool
    hermes_status: str | None
    hermes_message: str | None
    hermes_error: str | None
    hermes_sent_at_utc: datetime | None


@dataclass(frozen=True)
class ModelAnalysisResultPersistencePayload:
    """Repository payload for one `model_analysis_result` final row."""

    model_analysis_result_id: str
    model_analysis_run_id: str
    review_version_key: str
    material_pack_id: str
    aggregation_run_id: str
    strategy_signal_run_id: str
    review_decision: str
    human_review_required: bool
    evidence_quality: str
    logic_consistency: str
    risk_acceptability: str
    strategy_conflict_level: str
    missing_evidence_json: list[Any]
    rejection_reasons_json: list[Any]
    risk_warnings_json: list[Any]
    conditions_to_reconsider_json: list[Any]
    validation_focus_json: list[Any]
    human_review_questions_json: list[Any]
    summary_text: str
    not_trading_advice_text: str


@dataclass(frozen=True)
class ModelAnalysisServiceResult:
    """Compact service result returned to CLI and tests."""

    status: ModelAnalysisStatus
    exit_code: int
    model_analysis_run_id: str
    model_analysis_result_id: str | None
    review_version_key: str | None
    material_pack_id: str
    aggregation_run_id: str | None
    strategy_signal_run_id: str | None
    trace_id: str
    review_decision: str | None = None
    model_key: str | None = None
    model_role: str | None = None
    analysis_mode: str | None = None
    evidence_quality: str | None = None
    risk_acceptability: str | None = None
    strategy_conflict_level: str | None = None
    human_review_required: bool = False
    is_final_trading_advice: bool = False
    is_trading_signal: bool = False
    is_executable: bool = False
    auto_trading_allowed: bool = False
    input_char_count: int = 0
    input_byte_count: int = 0
    output_char_count: int = 0
    output_byte_count: int = 0
    message: str = ""
    error_code: str | None = None
    error_message: str | None = None
    hermes_status: ModelAnalysisHermesStatus | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


def format_model_analysis_result_lines(result: ModelAnalysisServiceResult) -> list[str]:
    """Format compact CLI output without prompt or provider response dumps."""

    return [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"model_analysis_run_id={result.model_analysis_run_id}",
        f"model_analysis_result_id={result.model_analysis_result_id or ''}",
        f"material_pack_id={result.material_pack_id}",
        f"aggregation_run_id={result.aggregation_run_id or ''}",
        f"strategy_signal_run_id={result.strategy_signal_run_id or ''}",
        f"review_version_key={result.review_version_key or ''}",
        f"model_key={result.model_key or ''}",
        f"model_role={result.model_role or ''}",
        f"analysis_mode={result.analysis_mode or ''}",
        f"review_decision={result.review_decision or ''}",
        f"evidence_quality={result.evidence_quality or ''}",
        f"risk_acceptability={result.risk_acceptability or ''}",
        f"strategy_conflict_level={result.strategy_conflict_level or ''}",
        f"human_review_required={str(result.human_review_required).lower()}",
        f"is_final_trading_advice={str(result.is_final_trading_advice).lower()}",
        f"is_trading_signal={str(result.is_trading_signal).lower()}",
        f"is_executable={str(result.is_executable).lower()}",
        f"auto_trading_allowed={str(result.auto_trading_allowed).lower()}",
        f"input_char_count={result.input_char_count}",
        f"input_byte_count={result.input_byte_count}",
        f"output_char_count={result.output_char_count}",
        f"output_byte_count={result.output_byte_count}",
        f"message={result.message}",
        f"error_message={result.error_message or ''}",
    ]


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "MODEL_ANALYSIS_EVENT_SOURCE",
    "MODEL_REVIEW_MOCK_MODEL_NAME",
    "MODEL_REVIEW_MOCK_MODEL_VERSION",
    "MODEL_REVIEW_MODEL_KEY_DEFAULT",
    "MODEL_REVIEW_MODEL_ROLE_DEFAULT",
    "MODEL_REVIEW_MODE_DEFAULT",
    "MODEL_REVIEW_PROVIDER_MOCK",
    "ModelAnalysisHermesStatus",
    "ModelAnalysisRequest",
    "ModelAnalysisResultPersistencePayload",
    "ModelAnalysisRunPersistencePayload",
    "ModelAnalysisServiceResult",
    "ModelAnalysisStatus",
    "ModelProviderResult",
    "PromptBuildResult",
    "ReviewDecision",
    "SchemaValidationResult",
    "format_model_analysis_result_lines",
]
