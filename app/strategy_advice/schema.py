"""Types for stage-21A strategy advice lifecycle management.

This file belongs to `app/strategy_advice`. It defines stable enums, request
and result DTOs, and bounded persistence payloads for stage 21A.

Called by: `app/strategy_advice/service.py`,
`app/strategy_advice/repository.py`, `scripts/run_strategy_advice.py`, and
tests.

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

STRATEGY_ADVICE_EVENT_SOURCE = "app.strategy_advice.service"
STRATEGY_ADVICE_PAYLOAD_SCHEMA_VERSION = "strategy_advice_payload_v1"

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4


class StrategyAdviceServiceStatus(str, Enum):
    """Status values for one stage-21A service attempt."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"


class AdviceStatus(str, Enum):
    """Stable lifecycle status values for `strategy_advice` rows."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    COMPLETED = "completed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class AdviceAction(str, Enum):
    """Stable action keys for a human strategy advice row."""

    WAIT = "wait"
    AVOID_TRADE = "avoid_trade"
    STOP_TRADING = "stop_trading"
    CONDITIONAL_TRADE = "conditional_trade"
    MANAGE_POSITION = "manage_position"


class DirectionalBias(str, Enum):
    """Stable directional-bias keys for strategy advice."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class TradePermission(str, Enum):
    """Stable human trade-permission keys for advice rows and setups."""

    NOT_ALLOWED = "not_allowed"
    CONDITIONALLY_ALLOWED = "conditionally_allowed"
    POSITION_MANAGEMENT_ONLY = "position_management_only"


class LifecycleAction(str, Enum):
    """Stable 4h lifecycle review action keys."""

    CREATE_NEW_ADVICE = "create_new_advice"
    CONTINUE_ACTIVE_ADVICE = "continue_active_advice"
    UPDATE_ACTIVE_ADVICE = "update_active_advice"
    CLOSE_ACTIVE_ADVICE = "close_active_advice"
    COMPLETE_ACTIVE_ADVICE = "complete_active_advice"
    INVALIDATE_ACTIVE_ADVICE = "invalidate_active_advice"
    EXPIRE_ACTIVE_ADVICE = "expire_active_advice"
    WAIT_WITHOUT_ACTIVE_ADVICE = "wait_without_active_advice"
    STOP_TRADING = "stop_trading"


class AdviceEventType(str, Enum):
    """Stable event types for the first lifecycle event stream."""

    CREATED = "created"
    CONTINUED = "continued"
    UPDATED = "updated"
    SUPERSEDED = "superseded"
    ACTIVATED = "activated"
    COMPLETED = "completed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"
    CLOSED = "closed"
    NOTIFICATION_PAYLOAD_CREATED = "notification_payload_created"


@dataclass(frozen=True)
class StrategyAdviceRequest:
    """Input for one stage-21A advice lifecycle attempt.

    Parameters: `review_aggregation_run_id` identifies the stage-20 aggregation
    row; `trigger_source` is CLI-only in 21A; dry-run is the safe default.
    Return value: `StrategyAdviceResult` from the service.
    Failure scenarios: invalid parameters, missing stage-20 row, and database
    persistence failures are converted into structured results by the service.
    External effects: none in this value object.
    """

    review_aggregation_run_id: str
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = True
    confirm_write: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class StrategyAdvicePersistencePayload:
    """Repository payload for one `strategy_advice` row.

    The payload stores only compact summaries and lifecycle state. It never
    stores full model prompts, full provider responses, Kline arrays, private
    trading state, or executable trading instructions.
    """

    advice_id: str
    advice_code: str
    symbol: str
    base_interval: str
    higher_interval: str
    parent_advice_id: str | None
    root_advice_id: str
    previous_advice_id: str | None
    advice_path: str
    version_no: int
    advice_status: AdviceStatus
    advice_action: AdviceAction
    directional_bias: DirectionalBias
    trade_permission: TradePermission
    source_review_aggregation_run_id: str
    source_material_pack_id: str
    source_strategy_signal_run_id: str | None
    source_snapshot_id: str | None
    source_model_chain_id: str | None
    model_review_invoked: bool
    model_review_invocation_mode: str
    model_review_reused: bool
    reused_model_analysis_run_id: str | None
    model_review_basis: str
    model_review_expired: bool
    model_review_chain_status: str
    latest_model_review_at_utc: datetime | None
    model_review_status_summary_json: Mapping[str, Any]
    summary_text: str
    risk_summary_json: Mapping[str, Any]
    strategy_summary_json: Mapping[str, Any]
    model_summary_json: Mapping[str, Any]
    is_trading_signal: bool
    is_executable: bool
    auto_trading_allowed: bool
    closed_at_utc: datetime | None = None


@dataclass(frozen=True)
class StrategyAdviceLifecycleReviewPersistencePayload:
    """Repository payload for one `strategy_advice_lifecycle_review` row."""

    review_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    reviewed_advice_id: str | None
    result_advice_id: str | None
    previous_advice_id: str | None
    lifecycle_action: LifecycleAction
    lifecycle_reason: str
    source_review_aggregation_run_id: str
    source_material_pack_id: str
    source_strategy_signal_run_id: str | None
    source_snapshot_id: str | None
    model_review_invoked: bool
    model_review_invocation_mode: str
    model_review_reused: bool
    reused_model_analysis_run_id: str | None
    model_review_basis: str
    model_review_expired: bool
    model_review_chain_status: str
    notification_required: bool
    notification_level: str
    notification_reason: str
    notification_payload_json: Mapping[str, Any]


@dataclass(frozen=True)
class StrategyAdviceEventPersistencePayload:
    """Repository payload for one lifecycle event row."""

    event_id: str
    advice_id: str | None
    related_review_id: str
    event_type: AdviceEventType
    event_reason: str
    event_payload_json: Mapping[str, Any]


@dataclass(frozen=True)
class StrategyAdviceTradeSetupPersistencePayload:
    """Repository payload for one conditional setup row.

    A setup is a human-observation structure, not an order, not an automatic
    trading signal, and not an executable instruction.
    """

    setup_id: str
    advice_id: str
    setup_rank: int
    setup_type: str
    side: str
    entry_zone_json: Mapping[str, Any]
    trigger_condition_json: Mapping[str, Any]
    invalid_condition_json: Mapping[str, Any]
    stop_loss_json: Mapping[str, Any]
    target_zones_json: list[Any]
    expiry_base_bars: int | None
    permission: TradePermission
    source_strategy_names_json: list[str]
    source_model_keys_json: list[str]
    status: str


@dataclass(frozen=True)
class StrategyAdviceResult:
    """Compact stage-21A service result returned to CLI and tests."""

    status: StrategyAdviceServiceStatus
    exit_code: int
    review_id: str
    review_aggregation_run_id: str
    trace_id: str
    lifecycle_action: LifecycleAction | None = None
    lifecycle_reason: str = ""
    advice_id: str | None = None
    result_advice_id: str | None = None
    reviewed_advice_id: str | None = None
    previous_advice_id: str | None = None
    advice_code: str | None = None
    advice_path: str | None = None
    advice_status: AdviceStatus | None = None
    advice_action: AdviceAction | None = None
    directional_bias: DirectionalBias | None = None
    trade_permission: TradePermission | None = None
    material_pack_id: str | None = None
    strategy_signal_run_id: str | None = None
    snapshot_id: str | None = None
    model_review_invoked: bool = False
    model_review_invocation_mode: str = "none"
    model_review_reused: bool = False
    reused_model_analysis_run_id: str | None = None
    model_review_skip_reason: str = ""
    model_review_block_reason: str | None = None
    invoked_model_keys_json: tuple[str, ...] = field(default_factory=tuple)
    invoked_model_roles_json: tuple[str, ...] = field(default_factory=tuple)
    model_review_basis: str = "none"
    model_review_expired: bool = False
    model_review_chain_status: str = "not_started"
    latest_model_review_at_utc: datetime | None = None
    notification_required: bool = True
    notification_level: str = "brief"
    notification_reason: str = ""
    notification_payload_json: Mapping[str, Any] = field(default_factory=dict)
    created_advice_count: int = 0
    updated_advice_count: int = 0
    lifecycle_review_count: int = 0
    event_count: int = 0
    trade_setup_count: int = 0
    dry_run: bool = True
    is_trading_signal: bool = False
    is_executable: bool = False
    auto_trading_allowed: bool = False
    summary_text: str = "Stage 21A created a bounded advice lifecycle result without calling any model."
    error_code: str | None = None
    error_message: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


def format_strategy_advice_result_lines(result: StrategyAdviceResult) -> list[str]:
    """Format compact CLI output without raw model responses or setup dumps."""

    latest = result.latest_model_review_at_utc.isoformat() if result.latest_model_review_at_utc else ""
    return [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"review_id={result.review_id}",
        f"review_aggregation_run_id={result.review_aggregation_run_id}",
        f"trace_id={result.trace_id}",
        f"lifecycle_action={result.lifecycle_action.value if result.lifecycle_action else ''}",
        f"advice_id={result.advice_id or ''}",
        f"result_advice_id={result.result_advice_id or ''}",
        f"reviewed_advice_id={result.reviewed_advice_id or ''}",
        f"previous_advice_id={result.previous_advice_id or ''}",
        f"advice_code={result.advice_code or ''}",
        f"advice_path={result.advice_path or ''}",
        f"advice_status={result.advice_status.value if result.advice_status else ''}",
        f"advice_action={result.advice_action.value if result.advice_action else ''}",
        f"directional_bias={result.directional_bias.value if result.directional_bias else ''}",
        f"trade_permission={result.trade_permission.value if result.trade_permission else ''}",
        f"material_pack_id={result.material_pack_id or ''}",
        f"strategy_signal_run_id={result.strategy_signal_run_id or ''}",
        f"snapshot_id={result.snapshot_id or ''}",
        f"model_review_invoked={str(result.model_review_invoked).lower()}",
        f"model_review_invocation_mode={result.model_review_invocation_mode}",
        f"model_review_reused={str(result.model_review_reused).lower()}",
        f"reused_model_analysis_run_id={result.reused_model_analysis_run_id or ''}",
        f"model_review_skip_reason={result.model_review_skip_reason}",
        f"model_review_block_reason={result.model_review_block_reason or ''}",
        f"invoked_model_keys_json={json_text(list(result.invoked_model_keys_json))}",
        f"invoked_model_roles_json={json_text(list(result.invoked_model_roles_json))}",
        f"model_review_basis={result.model_review_basis}",
        f"model_review_expired={str(result.model_review_expired).lower()}",
        f"model_review_chain_status={result.model_review_chain_status}",
        f"latest_model_review_at_utc={latest}",
        f"notification_required={str(result.notification_required).lower()}",
        f"notification_level={result.notification_level}",
        f"notification_reason={result.notification_reason}",
        f"created_advice_count={result.created_advice_count}",
        f"updated_advice_count={result.updated_advice_count}",
        f"lifecycle_review_count={result.lifecycle_review_count}",
        f"event_count={result.event_count}",
        f"trade_setup_count={result.trade_setup_count}",
        f"dry_run={str(result.dry_run).lower()}",
        f"is_trading_signal={str(result.is_trading_signal).lower()}",
        f"is_executable={str(result.is_executable).lower()}",
        f"auto_trading_allowed={str(result.auto_trading_allowed).lower()}",
        f"summary_text={result.summary_text}",
        f"error_code={result.error_code or ''}",
        f"error_message={result.error_message or ''}",
    ]


def json_text(value: Any) -> str:
    """Return deterministic JSON text for compact persistence and CLI output."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def load_json_text(value: Any, default: Any) -> Any:
    """Parse JSON text or return a safe default for malformed compact fields."""

    if value is None:
        return default
    if isinstance(value, (dict, list, tuple)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "STRATEGY_ADVICE_EVENT_SOURCE",
    "STRATEGY_ADVICE_PAYLOAD_SCHEMA_VERSION",
    "AdviceAction",
    "AdviceEventType",
    "AdviceStatus",
    "DirectionalBias",
    "LifecycleAction",
    "StrategyAdviceEventPersistencePayload",
    "StrategyAdviceLifecycleReviewPersistencePayload",
    "StrategyAdvicePersistencePayload",
    "StrategyAdviceRequest",
    "StrategyAdviceResult",
    "StrategyAdviceServiceStatus",
    "StrategyAdviceTradeSetupPersistencePayload",
    "TradePermission",
    "format_strategy_advice_result_lines",
    "json_text",
    "load_json_text",
]
