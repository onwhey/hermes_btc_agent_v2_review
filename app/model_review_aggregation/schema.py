"""Types for stage-20A model review aggregation and reuse checks.

This file belongs to `app/model_review_aggregation`. It defines constants,
enums, request/result DTOs, and repository payloads for the deterministic
stage-20A controller.

Called by: `app/model_review_aggregation/service.py`,
`app/model_review_aggregation/repository.py`, `scripts/run_model_review_aggregation.py`,
and tests.

External services: none. MySQL: none in this file. Redis: none. Hermes: none.
Large-model calls: none. Trading execution: none.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI

MODEL_REVIEW_AGGREGATION_EVENT_SOURCE = "app.model_review_aggregation.service"
REVIEW_INPUT_FINGERPRINT_VERSION = "review_input_fingerprint_v1"
AGGREGATION_MODE_SINGLE_OR_REUSE = "single_or_reuse"

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4


class ModelReviewAggregationStatus(str, Enum):
    """Status values for one stage-20A aggregation run."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ModelReviewAggregationRequest:
    """Input for one stage-20A aggregation attempt.

    Parameters: `material_pack_id` identifies the stage-18 material pack;
    `trigger_source` is currently CLI-only; dry-run is the safe default.
    Return value: `ModelReviewAggregationResult` from the service.
    Failure scenarios: invalid parameters, missing material pack, missing or
    expired model review results, and persistence failures are converted into
    structured results by the service.
    External effects: none in this value object.
    """

    material_pack_id: str
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = True
    confirm_write: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class ModelReviewAggregationPersistencePayload:
    """Repository payload for one `model_review_aggregation_run` row.

    This payload stores only compact model-review summaries, reuse status, and
    small JSON arrays. It never stores full prompts, full provider responses,
    final trading advice, private trading state, or Kline arrays.
    """

    review_aggregation_run_id: str
    material_pack_id: str
    aggregation_run_id: str
    strategy_signal_run_id: str
    snapshot_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    status: ModelReviewAggregationStatus
    trigger_source: str
    created_by: str
    trace_id: str
    input_model_run_count: int
    input_model_result_count: int
    accepted_model_result_count: int
    failed_model_result_count: int
    blocked_model_result_count: int
    skipped_model_result_count: int
    aggregation_mode: str
    model_review_invoked: bool
    model_review_invocation_mode: str
    model_review_reused: bool
    reused_model_analysis_run_id: str | None
    reused_model_review_created_at_utc: datetime | None
    model_review_skip_reason: str
    model_review_block_reason: str | None
    invoked_model_keys_json: list[str]
    invoked_model_roles_json: list[str]
    model_review_chain_status: str
    model_review_partial_failure_reason: str | None
    latest_model_review_at_utc: datetime | None
    model_review_basis: str
    model_review_reuse_status: str
    model_review_reuse_base_bars: int | None
    model_review_reuse_max_base_bars: int
    model_review_expired: bool
    review_input_fingerprint: str
    review_input_fingerprint_version: str
    review_decision_summary: str
    evidence_quality_summary: str
    risk_acceptability_summary: str
    strategy_conflict_summary: str
    model_consensus_level: str
    allowed_advice_mode: str
    directional_trade_allowed: bool
    model_results_summary_json: Mapping[str, Any]
    model_disagreement_json: Mapping[str, Any]
    risk_warnings_json: list[Any]
    missing_evidence_json: list[Any]
    human_review_questions_json: list[Any]
    summary_text: str
    is_final_trading_advice: bool
    is_trading_signal: bool
    is_executable: bool
    auto_trading_allowed: bool
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class ModelReviewAggregationResult:
    """Compact service result returned to CLI and tests."""

    status: ModelReviewAggregationStatus
    exit_code: int
    review_aggregation_run_id: str
    material_pack_id: str
    aggregation_run_id: str | None
    strategy_signal_run_id: str | None
    snapshot_id: str | None
    trace_id: str
    accepted_model_result_count: int = 0
    failed_model_result_count: int = 0
    blocked_model_result_count: int = 0
    skipped_model_result_count: int = 0
    model_review_invoked: bool = False
    model_review_invocation_mode: str = "none"
    model_review_reused: bool = False
    reused_model_analysis_run_id: str | None = None
    model_review_skip_reason: str = ""
    model_review_block_reason: str | None = None
    model_review_basis: str = "material_only"
    latest_model_review_at_utc: datetime | None = None
    model_review_reuse_status: str = "not_reused"
    model_review_reuse_base_bars: int | None = None
    model_review_reuse_max_base_bars: int = 3
    model_review_expired: bool = False
    review_decision_summary: str = "no_model_review_result"
    evidence_quality_summary: str = "no_model_review_result"
    risk_acceptability_summary: str = "no_model_review_result"
    strategy_conflict_summary: str = "no_model_review_result"
    summary_text: str = "本轮未调用大模型。"
    is_final_trading_advice: bool = False
    is_trading_signal: bool = False
    is_executable: bool = False
    auto_trading_allowed: bool = False
    error_code: str | None = None
    error_message: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


def format_model_review_aggregation_result_lines(result: ModelReviewAggregationResult) -> list[str]:
    """Format compact CLI output without raw material or model-response dumps."""

    latest = result.latest_model_review_at_utc.isoformat() if result.latest_model_review_at_utc else ""
    return [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"review_aggregation_run_id={result.review_aggregation_run_id}",
        f"material_pack_id={result.material_pack_id}",
        f"aggregation_run_id={result.aggregation_run_id or ''}",
        f"strategy_signal_run_id={result.strategy_signal_run_id or ''}",
        f"snapshot_id={result.snapshot_id or ''}",
        f"accepted_model_result_count={result.accepted_model_result_count}",
        f"failed_model_result_count={result.failed_model_result_count}",
        f"blocked_model_result_count={result.blocked_model_result_count}",
        f"skipped_model_result_count={result.skipped_model_result_count}",
        f"model_review_invoked={str(result.model_review_invoked).lower()}",
        f"model_review_invocation_mode={result.model_review_invocation_mode}",
        f"model_review_reused={str(result.model_review_reused).lower()}",
        f"reused_model_analysis_run_id={result.reused_model_analysis_run_id or ''}",
        f"model_review_skip_reason={result.model_review_skip_reason}",
        f"model_review_block_reason={result.model_review_block_reason or ''}",
        f"model_review_basis={result.model_review_basis}",
        f"latest_model_review_at_utc={latest}",
        f"model_review_reuse_status={result.model_review_reuse_status}",
        f"model_review_reuse_base_bars={'' if result.model_review_reuse_base_bars is None else result.model_review_reuse_base_bars}",
        f"model_review_reuse_max_base_bars={result.model_review_reuse_max_base_bars}",
        f"model_review_expired={str(result.model_review_expired).lower()}",
        f"review_decision_summary={result.review_decision_summary}",
        f"evidence_quality_summary={result.evidence_quality_summary}",
        f"risk_acceptability_summary={result.risk_acceptability_summary}",
        f"strategy_conflict_summary={result.strategy_conflict_summary}",
        f"is_final_trading_advice={str(result.is_final_trading_advice).lower()}",
        f"is_trading_signal={str(result.is_trading_signal).lower()}",
        f"is_executable={str(result.is_executable).lower()}",
        f"auto_trading_allowed={str(result.auto_trading_allowed).lower()}",
        f"summary_text={result.summary_text}",
        f"error_code={result.error_code or ''}",
        f"error_message={result.error_message or ''}",
    ]


def json_text(value: Any) -> str:
    """Return deterministic JSON text for compact persistence."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


__all__ = [
    "AGGREGATION_MODE_SINGLE_OR_REUSE",
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "MODEL_REVIEW_AGGREGATION_EVENT_SOURCE",
    "REVIEW_INPUT_FINGERPRINT_VERSION",
    "ModelReviewAggregationPersistencePayload",
    "ModelReviewAggregationRequest",
    "ModelReviewAggregationResult",
    "ModelReviewAggregationStatus",
    "format_model_review_aggregation_result_lines",
    "json_text",
]
