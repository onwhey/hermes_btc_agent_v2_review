"""Stage-21C strategy advice scheduler DTOs and constants.

This file belongs to `app/strategy_advice`. It defines bounded request/result
objects for automatically linking stage 20 MRAG rows to existing 21A advice
lifecycle generation and existing 21B notification delivery.

Called by `app/strategy_advice/scheduler_service.py`,
`app/scheduler/jobs/strategy_advice_scheduler_job.py`, the CLI, and tests.
External services: none. MySQL: none. Redis: none. Hermes: none here. Model
providers: none. Trading execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI

STRATEGY_ADVICE_SCHEDULER_JOB_NAME = "strategy_advice_scheduler_21c"
STRATEGY_ADVICE_21C_LOCK_TTL_SECONDS = 600
STRATEGY_ADVICE_NOTIFICATION_RETRY_DELAY_SECONDS = 300
STRATEGY_ADVICE_NOTIFICATION_MAX_RETRY_COUNT = 3

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4


class StrategyAdviceSchedulerStatus(str, Enum):
    """Stable status values for one stage-21C scheduler orchestration."""

    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"
    LOCK_SKIPPED = "lock_skipped"
    DISABLED = "disabled"


@dataclass(frozen=True)
class StrategyAdviceSchedulerRequest:
    """Input for one stage-21C scheduler/manual orchestration attempt.

    Parameters: explicit `review_aggregation_run_id` processes one MRAG; symbol
    and intervals scan a bounded batch of unprocessed MRAG rows. Dry-run reads
    only. Confirm-write may create stale audit reviews, call 21A, and call 21B.
    External effects: none in this value object.
    """

    review_aggregation_run_id: str | None = None
    symbol: str = "BTCUSDT"
    base_interval: str = "4h"
    higher_interval: str = "1d"
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = True
    confirm_write: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    limit: int = 20


@dataclass(frozen=True)
class StrategyAdviceSchedulerResult:
    """Compact 21C result returned to scheduler runner, CLI, and tests."""

    status: StrategyAdviceSchedulerStatus
    exit_code: int
    trace_id: str
    trigger_source: str
    review_aggregation_run_id: str | None = None
    symbol: str | None = None
    base_interval: str | None = None
    higher_interval: str | None = None
    scheduler_enabled: bool = False
    notification_send_enabled: bool = False
    processed_mrag_count: int = 0
    stale_skipped_count: int = 0
    lifecycle_review_id: str | None = None
    advice_result_status: str | None = None
    notification_attempted: bool = False
    notification_status: str | None = None
    send_real_alert: bool = False
    lock_key: str | None = None
    dry_run: bool = True
    error_code: str | None = None
    error_message: str | None = None
    summary_text: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


def format_strategy_advice_scheduler_result_lines(result: StrategyAdviceSchedulerResult) -> list[str]:
    """Format compact CLI output without dumping notification bodies."""

    return [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"trace_id={result.trace_id}",
        f"trigger_source={result.trigger_source}",
        f"review_aggregation_run_id={result.review_aggregation_run_id or ''}",
        f"symbol={result.symbol or ''}",
        f"base_interval={result.base_interval or ''}",
        f"higher_interval={result.higher_interval or ''}",
        f"scheduler_enabled={str(result.scheduler_enabled).lower()}",
        f"notification_send_enabled={str(result.notification_send_enabled).lower()}",
        f"processed_mrag_count={result.processed_mrag_count}",
        f"stale_skipped_count={result.stale_skipped_count}",
        f"lifecycle_review_id={result.lifecycle_review_id or ''}",
        f"advice_result_status={result.advice_result_status or ''}",
        f"notification_attempted={str(result.notification_attempted).lower()}",
        f"notification_status={result.notification_status or ''}",
        f"send_real_alert={str(result.send_real_alert).lower()}",
        f"lock_key={result.lock_key or ''}",
        f"dry_run={str(result.dry_run).lower()}",
        f"error_code={result.error_code or ''}",
        f"error_message={result.error_message or ''}",
        f"summary_text={result.summary_text}",
    ]


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "STRATEGY_ADVICE_21C_LOCK_TTL_SECONDS",
    "STRATEGY_ADVICE_NOTIFICATION_MAX_RETRY_COUNT",
    "STRATEGY_ADVICE_NOTIFICATION_RETRY_DELAY_SECONDS",
    "STRATEGY_ADVICE_SCHEDULER_JOB_NAME",
    "StrategyAdviceSchedulerRequest",
    "StrategyAdviceSchedulerResult",
    "StrategyAdviceSchedulerStatus",
    "format_strategy_advice_scheduler_result_lines",
]
