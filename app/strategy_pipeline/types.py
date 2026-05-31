"""DTOs and constants for stage-25 strategy pipeline orchestration.

This file belongs to `app/strategy_pipeline`. It defines only bounded request,
result, and persistence payload objects for the unified pipeline.

Called by `app/strategy_pipeline/service.py`, `scripts/run_strategy_pipeline.py`,
the scheduler 25B wrapper, and tests. External services: none. MySQL: none in
this file. Redis: none. Hermes: none. Large models: none. Trading execution:
none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4

PIPELINE_STEP_PREFLIGHT = "preflight"
PIPELINE_STEP_STAGE17_16 = "17_16_strategy_signals"
PIPELINE_STEP_STAGE23F = "24a_23f_evidence_aggregation"
PIPELINE_STEP_STAGE26B = "26b_strategy_evidence_quality_gate"
PIPELINE_STEP_STAGE18 = "18_material_pack"
PIPELINE_STEP_STAGE20 = "20c_19_20a_model_review"
PIPELINE_STEP_STAGE21 = "21a_21b_advice_notification"


class StrategyPipelineStatus(str, Enum):
    """Stable status values for one 25 pipeline run."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass(frozen=True)
class StrategyPipelineRequest:
    """Input for one strategy pipeline attempt.

    Parameters: symbol/intervals identify the market scope; `kline_slot_utc`
    optionally pins the base Kline open time. If omitted, the service may infer
    the latest closed base Kline from the formal Kline table only when unique.
    Return value: `StrategyPipelineResult`.
    Failure scenarios: invalid write mode, disabled config, unresolved Kline
    slot, Redis lock conflict, or downstream service failures become structured
    results. External effects: none in this value object.
    """

    symbol: str = DEFAULT_KLINE_SYMBOL
    base_interval: str = KLINE_4H_INTERVAL_VALUE
    higher_interval: str = KLINE_1D_INTERVAL_VALUE
    kline_slot_utc: datetime | None = None
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = True
    confirm_write: bool = False
    use_real_model: bool = False
    confirm_real_model_cost: bool = False
    send_real_hermes: bool = False
    retry_failed_stage17: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class StrategyPipelineEventPayload:
    """Repository payload for one `strategy_pipeline_event_log` row."""

    pipeline_run_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None
    kline_slot_source: str | None
    trigger_source: str
    status: str
    current_step: str | None
    strategy_signal_run_id: str | None
    strategy_evidence_aggregation_id: str | None
    material_pack_id: str | None
    model_analysis_run_id: str | None
    review_aggregation_run_id: str | None
    advice_id: str | None
    review_id: str | None
    notification_status: str | None
    model_review_invoked: bool
    model_review_reused: bool
    real_model_called: bool
    hermes_real_sent: bool
    error_code: str | None
    error_message: str | None
    trace_id: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyPipelineResult:
    """Compact result returned by the 25A pipeline service and CLI."""

    status: StrategyPipelineStatus
    exit_code: int
    pipeline_run_id: str
    trace_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None = None
    kline_slot_source: str | None = None
    strategy_signal_run_id: str | None = None
    strategy_evidence_aggregation_id: str | None = None
    material_pack_id: str | None = None
    model_analysis_run_id: str | None = None
    review_aggregation_run_id: str | None = None
    advice_id: str | None = None
    review_id: str | None = None
    notification_status: str | None = None
    model_review_invoked: bool = False
    model_review_reused: bool = False
    real_model_called: bool = False
    hermes_real_sent: bool = False
    is_final_trading_advice: bool = False
    is_trading_signal: bool = False
    is_executable: bool = False
    auto_trading_allowed: bool = False
    current_step: str | None = None
    lock_key: str | None = None
    message: str = ""
    error_code: str | None = None
    error_message: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


def build_pipeline_run_id(*, symbol: str, base_interval: str, higher_interval: str, trace_id: str) -> str:
    """Build a short auditable pipeline business id without querying storage."""

    return f"SP-{symbol}-{base_interval.upper()}-{higher_interval.upper()}-{trace_id[:16]}"


def status_value(value: Any) -> str:
    """Return an enum/string value safely for compact status comparisons."""

    if hasattr(value, "value"):
        return str(value.value)
    return str(value or "")


def exit_code_for_status(status: StrategyPipelineStatus) -> int:
    """Map pipeline status to CLI exit code."""

    if status in (StrategyPipelineStatus.SUCCESS, StrategyPipelineStatus.PARTIAL_SUCCESS, StrategyPipelineStatus.DRY_RUN):
        return EXIT_SUCCESS
    if status in (StrategyPipelineStatus.BLOCKED, StrategyPipelineStatus.SKIPPED):
        return EXIT_BLOCKED
    return EXIT_FAILED


def format_strategy_pipeline_result_lines(result: StrategyPipelineResult) -> list[str]:
    """Format CLI output without dumping strategy/model/advice JSON bodies."""

    return [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"pipeline_run_id={result.pipeline_run_id}",
        f"trace_id={result.trace_id}",
        f"symbol={result.symbol}",
        f"base_interval={result.base_interval}",
        f"higher_interval={result.higher_interval}",
        f"kline_slot_utc={result.kline_slot_utc.isoformat() if result.kline_slot_utc else ''}",
        f"kline_slot_source={result.kline_slot_source or ''}",
        f"strategy_signal_run_id={result.strategy_signal_run_id or ''}",
        f"strategy_evidence_aggregation_id={result.strategy_evidence_aggregation_id or ''}",
        f"material_pack_id={result.material_pack_id or ''}",
        f"model_analysis_run_id={result.model_analysis_run_id or ''}",
        f"review_aggregation_run_id={result.review_aggregation_run_id or ''}",
        f"advice_id={result.advice_id or ''}",
        f"review_id={result.review_id or ''}",
        f"notification_status={result.notification_status or ''}",
        f"model_review_invoked={str(result.model_review_invoked).lower()}",
        f"model_review_reused={str(result.model_review_reused).lower()}",
        f"real_model_called={str(result.real_model_called).lower()}",
        f"hermes_real_sent={str(result.hermes_real_sent).lower()}",
        f"retry_failed_stage17={str(bool(_detail_value(result.details, 'retry_failed_stage17'))).lower()}",
        f"previous_stage17_event_id={_detail_value(result.details, 'previous_stage17_event_id') or ''}",
        f"previous_stage17_status={_detail_value(result.details, 'previous_stage17_status') or ''}",
        f"previous_stage17_run_id={_detail_value(result.details, 'previous_stage17_run_id') or ''}",
        f"previous_strategy_signal_run_status={_detail_value(result.details, 'previous_strategy_signal_run_status') or ''}",
        f"new_strategy_signal_run_id={_detail_value(result.details, 'new_strategy_signal_run_id') or ''}",
        f"is_final_trading_advice={str(result.is_final_trading_advice).lower()}",
        f"is_trading_signal={str(result.is_trading_signal).lower()}",
        f"is_executable={str(result.is_executable).lower()}",
        f"auto_trading_allowed={str(result.auto_trading_allowed).lower()}",
        f"current_step={result.current_step or ''}",
        f"lock_key={result.lock_key or ''}",
        f"message={result.message}",
        f"error_code={result.error_code or ''}",
        f"error_message={result.error_message or ''}",
    ]


def _detail_value(details: Mapping[str, Any], key: str) -> Any:
    return details.get(key) if isinstance(details, Mapping) else None


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "PIPELINE_STEP_PREFLIGHT",
    "PIPELINE_STEP_STAGE17_16",
    "PIPELINE_STEP_STAGE18",
    "PIPELINE_STEP_STAGE20",
    "PIPELINE_STEP_STAGE21",
    "PIPELINE_STEP_STAGE26B",
    "PIPELINE_STEP_STAGE23F",
    "StrategyPipelineEventPayload",
    "StrategyPipelineRequest",
    "StrategyPipelineResult",
    "StrategyPipelineStatus",
    "build_pipeline_run_id",
    "exit_code_for_status",
    "format_strategy_pipeline_result_lines",
    "status_value",
]
