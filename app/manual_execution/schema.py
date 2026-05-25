"""Stage-22A manual execution request and result schemas.

This file belongs to `app/manual_execution`. It defines structured DTOs for
manual execution CLI requests, service results, execution persistence payloads,
and open-position query output.

Called by scripts, service, repository tests, and formatters. External services:
none. MySQL: none. Redis: none. Hermes: none. DeepSeek: none. Trading execution:
none. These schemas describe user feedback only and do not represent exchange
positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping
from uuid import uuid4

from app.manual_execution.constants import EXIT_BLOCKED, EXIT_FAILED, EXIT_SUCCESS


class ManualExecutionServiceStatus(str, Enum):
    """Service outcome for one manual execution feedback request."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    DRY_RUN = "dry_run"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class ManualExecutionRequest:
    """Structured request for recording one user-reported manual execution.

    Parameters: values mirror CLI flags; Decimal-compatible fields may be
    strings or Decimal objects but never floats.
    Return value: immutable request DTO.
    Failure scenarios: service validation blocks missing advice, invalid
    action, invalid Decimal fields, missing confirm-write, or mismatched manual
    position state.
    External services: this DTO does not access external services.
    Data impact: no MySQL, Redis, or Hermes access.
    This DTO does not execute trades or modify strategy advice lifecycle state.
    """

    action: str
    advice_id: str
    symbol: str
    side: str
    price: Decimal | str | int | None
    notional_usdt: Decimal | str | int | None = None
    margin_usdt: Decimal | str | int | None = None
    manual_position_id: str | None = None
    reason: str = ""
    note: str = ""
    trigger_source: str = "cli"
    dry_run: bool = True
    confirm_write: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class AdviceResolution:
    """Resolved advice metadata for one execution record."""

    advice_id: str
    review_id: str | None
    setup_id: str | None
    advice_resolution_method: str
    setup_resolution_method: str


@dataclass(frozen=True)
class ManualPositionPersistencePayload:
    """Persistence payload for creating one manual position summary row."""

    manual_position_id: str
    symbol: str
    side: str
    status: str
    opened_at_utc: datetime
    closed_at_utc: datetime | None
    opened_by_advice_id: str
    latest_related_advice_id: str
    closed_by_advice_id: str | None
    initial_entry_price: Decimal
    avg_entry_price: Decimal
    close_price: Decimal | None
    current_quantity_base_asset: Decimal
    current_cost_basis_usdt: Decimal
    margin_basis_usdt: Decimal
    effective_leverage: Decimal
    total_open_notional_usdt: Decimal
    total_close_notional_usdt: Decimal
    total_fee_usdt: Decimal
    gross_realized_pnl_usdt: Decimal
    net_realized_pnl_usdt: Decimal
    net_pnl_ratio_on_margin: Decimal
    open_reason: str | None
    open_decision_context: str | None
    review_status: str
    trigger_source: str
    created_by: str
    trace_id: str
    created_at_utc: datetime
    updated_at_utc: datetime


@dataclass(frozen=True)
class ExecutionRecordPersistencePayload:
    """Persistence payload for one manual execution record row."""

    execution_id: str
    manual_position_id: str
    execution_action: str
    symbol: str
    side: str
    price: Decimal
    notional_usdt: Decimal
    quantity_base_asset: Decimal
    margin_usdt: Decimal | None
    fee_rate: Decimal
    fee_usdt: Decimal
    gross_pnl_usdt: Decimal
    net_pnl_usdt: Decimal
    advice_id: str
    review_id: str | None
    setup_id: str | None
    advice_resolution_method: str
    setup_resolution_method: str
    manual_position_resolution_method: str
    reason: str | None
    note: str | None
    executed_at_utc: datetime
    trigger_source: str
    created_by: str
    trace_id: str
    created_at_utc: datetime


@dataclass(frozen=True)
class ManualExecutionResult:
    """Result returned after recording or previewing one manual execution."""

    status: ManualExecutionServiceStatus
    exit_code: int
    action: str
    trace_id: str
    dry_run: bool
    manual_position_id: str | None = None
    execution_id: str | None = None
    database_written: bool = False
    receipt_required: bool = False
    receipt_status: str | None = None
    receipt_failed: bool = False
    error_code: str | None = None
    error_message: str | None = None
    warnings: tuple[str, ...] = ()
    position_snapshot: Mapping[str, str] = field(default_factory=dict)
    execution_snapshot: Mapping[str, str] = field(default_factory=dict)
    receipt_message: str = ""


@dataclass(frozen=True)
class ManualPositionListRequest:
    """Request for listing manual positions from CLI."""

    symbol: str | None = None
    status: str = "open"
    trigger_source: str = "cli"


@dataclass(frozen=True)
class ManualPositionSummary:
    """User-facing summary row for `check_manual_positions`."""

    manual_position_id: str
    symbol: str
    side: str
    status: str
    avg_entry_price: str
    current_quantity_base_asset: str
    current_cost_basis_usdt: str
    margin_basis_usdt: str
    effective_leverage: str
    opened_at_utc: str
    opened_by_advice_id: str


@dataclass(frozen=True)
class ManualPositionListResult:
    """Result returned by the open manual position query service."""

    status: ManualExecutionServiceStatus
    exit_code: int
    trace_id: str
    positions: tuple[ManualPositionSummary, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


def format_manual_execution_result_lines(result: ManualExecutionResult) -> list[str]:
    """Format a compact CLI response for one manual execution operation."""

    lines = [
        f"status={result.status.value}",
        f"dry_run={str(result.dry_run).lower()}",
        f"database_written={str(result.database_written).lower()}",
        f"action={result.action}",
        f"trace_id={result.trace_id}",
    ]
    if result.manual_position_id:
        lines.append(f"manual_position_id={result.manual_position_id}")
    if result.execution_id:
        lines.append(f"execution_id={result.execution_id}")
    if result.error_code:
        lines.append(f"error_code={result.error_code}")
    if result.error_message:
        lines.append(f"error_message={result.error_message}")
    if result.receipt_status:
        lines.append(f"hermes_receipt_status={result.receipt_status}")
    if result.receipt_failed:
        lines.append("warning=数据库已写入，但 Hermes 回执失败")
    for warning in result.warnings:
        lines.append(f"warning={warning}")
    if result.position_snapshot:
        lines.append("position_snapshot:")
        lines.extend(f"  {key}={value}" for key, value in result.position_snapshot.items())
    if result.execution_snapshot:
        lines.append("execution_snapshot:")
        lines.extend(f"  {key}={value}" for key, value in result.execution_snapshot.items())
    return lines


def format_manual_position_list_lines(result: ManualPositionListResult) -> list[str]:
    """Format a compact CLI response for open manual positions."""

    lines = [
        f"status={result.status.value}",
        f"trace_id={result.trace_id}",
        f"count={len(result.positions)}",
    ]
    if result.error_code:
        lines.append(f"error_code={result.error_code}")
    if result.error_message:
        lines.append(f"error_message={result.error_message}")
    for position in result.positions:
        lines.append(
            " | ".join(
                [
                    f"manual_position_id={position.manual_position_id}",
                    f"symbol={position.symbol}",
                    f"side={position.side}",
                    f"status={position.status}",
                    f"avg_entry_price={position.avg_entry_price}",
                    f"current_quantity_base_asset={position.current_quantity_base_asset}",
                    f"current_cost_basis_usdt={position.current_cost_basis_usdt}",
                    f"margin_basis_usdt={position.margin_basis_usdt}",
                    f"effective_leverage={position.effective_leverage}",
                    f"opened_at_utc={position.opened_at_utc}",
                    f"opened_by_advice_id={position.opened_by_advice_id}",
                ]
            )
        )
    return lines


def blocked_result(*, request: ManualExecutionRequest, error_code: str, error_message: str) -> ManualExecutionResult:
    """Create a standard blocked result that never writes execution rows."""

    return ManualExecutionResult(
        status=ManualExecutionServiceStatus.BLOCKED,
        exit_code=EXIT_BLOCKED,
        action=request.action,
        trace_id=request.trace_id,
        dry_run=request.dry_run,
        manual_position_id=request.manual_position_id,
        error_code=error_code,
        error_message=error_message,
    )


def failed_result(*, request: ManualExecutionRequest, error_code: str, error_message: str) -> ManualExecutionResult:
    """Create a standard failed result for unexpected service errors."""

    return ManualExecutionResult(
        status=ManualExecutionServiceStatus.FAILED,
        exit_code=EXIT_FAILED,
        action=request.action,
        trace_id=request.trace_id,
        dry_run=request.dry_run,
        manual_position_id=request.manual_position_id,
        error_code=error_code,
        error_message=error_message,
    )


def success_exit_code(*, dry_run: bool) -> int:
    """Return the CLI exit code for successful preview or write."""

    del dry_run
    return EXIT_SUCCESS
