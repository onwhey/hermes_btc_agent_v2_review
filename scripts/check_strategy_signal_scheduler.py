"""Manual check entry for stage-17 strategy signal scheduler orchestration.

Triggered by: a user running `python -m scripts.check_strategy_signal_scheduler`.
Manual execution: allowed for dry-run validation and explicit confirmed writes.
Scheduler execution: not allowed; scheduler uses `app/scheduler/runner.py`, not
this check script.
Required args: `--upstream-slot-time-utc`. Optional args include `--symbol`,
`--base-interval`, `--higher-interval`, `--upstream-job-name`, `--dry-run`, and
`--confirm-write`.
Calls: dry-run calls `StrategySignalSchedulerService.preview_after_collector_success`;
confirmed writes call `StrategySignalSchedulerService.run_after_collector_success`.
Business logic: lives in `app/scheduler`, not in this script.
Database impact: dry-run is read-only and does not write
`strategy_signal_scheduler_event_log`; confirmed writes delegate to stage 17.
Redis impact: none.
Hermes impact: delegated to stage 17 only during confirmed writes.
Formal Kline impact: this script is not allowed to modify `market_kline_4h` or
`market_kline_1d`.
Data repair/trading impact: no automatic repair, no manual field editing, no
private trading state reads, and no trading.
This script is only for validating scheduler orchestration and does not replace
`scripts/run_strategy_signals.py`.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime
from typing import Any, Sequence

from app.core.config import AppSettings, get_settings
from app.core.time_utils import UTC, now_utc
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE
from app.scheduler.config import (
    SchedulerRuntimeConfig,
    build_scheduler_runtime_config,
    validate_scheduler_runtime_config,
)
from app.scheduler.slot_state import KLINE_1D_INCREMENTAL_JOB_NAME, KLINE_4H_INCREMENTAL_JOB_NAME
from app.scheduler.strategy_signal_scheduler_service import StrategySignalSchedulerService
from app.scheduler.strategy_signal_scheduler_types import (
    StrategySignalSchedulerRequest,
    StrategySignalSchedulerResult,
    StrategySignalSchedulerStatus,
)
from app.storage.mysql.session import session_scope

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4

CHECK_STATUS_DISABLED = "disabled"
CHECK_STATUS_WOULD_TRIGGER = "would_trigger"
CHECK_STATUS_WOULD_WAITING_UPSTREAM = "would_waiting_upstream"
CHECK_STATUS_SKIPPED = "skipped"


def build_arg_parser(settings: AppSettings | None = None) -> argparse.ArgumentParser:
    """Build the manual stage-17 scheduler validation parser."""

    active_settings = settings or get_settings()
    parser = argparse.ArgumentParser(description="Dry-run or confirm stage-17 strategy signal scheduler orchestration.")
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument(
        "--base-interval",
        default=active_settings.strategy_signal_base_interval or KLINE_4H_INTERVAL_VALUE,
        choices=[KLINE_4H_INTERVAL_VALUE],
    )
    parser.add_argument(
        "--higher-interval",
        default=active_settings.strategy_signal_higher_interval or KLINE_1D_INTERVAL_VALUE,
        choices=[KLINE_1D_INTERVAL_VALUE],
    )
    parser.add_argument(
        "--upstream-job-name",
        default=KLINE_4H_INCREMENTAL_JOB_NAME,
        choices=[KLINE_4H_INCREMENTAL_JOB_NAME, KLINE_1D_INCREMENTAL_JOB_NAME],
    )
    parser.add_argument(
        "--upstream-slot-time-utc",
        required=True,
        help="UTC scheduler slot time, for example 2026-05-18T12:05:00Z.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only preview. This is also the default mode.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow stage 17 to write and call stage 16.")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    service: StrategySignalSchedulerService | Any | None = None,
    settings: AppSettings | None = None,
    config: SchedulerRuntimeConfig | None = None,
    session_scope_factory: Callable[..., AbstractContextManager[Any]] | None = None,
    current_time_utc: datetime | None = None,
) -> int:
    """Parse args, call only stage-17 service methods, print key-value output."""

    active_settings = settings or get_settings()
    parser = build_arg_parser(active_settings)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_SUCCESS if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    try:
        upstream_slot_time_utc = _parse_utc_datetime(args.upstream_slot_time_utc)
    except ValueError as exc:
        _print_check_lines(
            _empty_check_result(
                status="failed",
                exit_code=EXIT_PARAMETER_ERROR,
                message="invalid upstream slot time",
                error_message=str(exc),
            )
        )
        return EXIT_PARAMETER_ERROR

    active_config = _build_check_config(
        args=args,
        settings=active_settings,
        explicit_config=config,
    )
    active_service = service or StrategySignalSchedulerService(config=active_config, settings=active_settings)
    active_time_utc = _ensure_utc(current_time_utc or now_utc())
    request = StrategySignalSchedulerRequest(
        upstream_job_name=args.upstream_job_name,
        current_time_utc=active_time_utc,
        upstream_slot_time_utc=upstream_slot_time_utc,
        symbol=active_config.strategy_signal_symbol,
        base_interval_value=active_config.strategy_signal_base_interval,
        higher_interval_value=active_config.strategy_signal_higher_interval,
    )

    scope_factory = session_scope_factory or session_scope
    with scope_factory(settings=active_settings, commit_on_success=False) as db_session:
        preview_result = active_service.preview_after_collector_success(db_session, request=request)
        preview_status = str(preview_result.details.get("check_status") or preview_result.status.value)
        if args.confirm_write and preview_status != CHECK_STATUS_DISABLED:
            final_result = active_service.run_after_collector_success(db_session, request=request)
            check_result = _format_confirm_write_result(final_result, preview_result)
        else:
            check_result = _format_dry_run_result(preview_result)

    _print_check_lines(check_result)
    return int(check_result["exit_code"])


def _build_check_config(
    *,
    args: argparse.Namespace,
    settings: AppSettings,
    explicit_config: SchedulerRuntimeConfig | None,
) -> SchedulerRuntimeConfig:
    base_config = explicit_config or build_scheduler_runtime_config(settings)
    config = replace(
        base_config,
        strategy_signal_symbol=args.symbol.strip().upper(),
        strategy_signal_base_interval=args.base_interval,
        strategy_signal_higher_interval=args.higher_interval,
    )
    validate_scheduler_runtime_config(config)
    return config


def _format_dry_run_result(result: StrategySignalSchedulerResult) -> dict[str, Any]:
    check_status = str(result.details.get("check_status") or result.status.value)
    return _build_output_result(
        status=check_status,
        exit_code=EXIT_SUCCESS,
        result=result,
        target_details=result.details,
        message=result.message,
        error_message=result.error_message or "",
    )


def _format_confirm_write_result(
    final_result: StrategySignalSchedulerResult,
    preview_result: StrategySignalSchedulerResult,
) -> dict[str, Any]:
    return _build_output_result(
        status=final_result.status.value,
        exit_code=_exit_code_for_scheduler_result(final_result),
        result=final_result,
        target_details=preview_result.details,
        message=final_result.message,
        error_message=final_result.error_message or "",
    )


def _build_output_result(
    *,
    status: str,
    exit_code: int,
    result: StrategySignalSchedulerResult,
    target_details: dict[str, Any],
    message: str,
    error_message: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "exit_code": exit_code,
        "event_id": result.event_id or "",
        "run_id": result.run_id or "",
        "snapshot_id": result.snapshot_id or "",
        "target_base_open_time_utc": _format_optional_utc(target_details.get("target_base_open_time_utc")),
        "target_base_close_time_utc": _format_optional_utc(target_details.get("target_base_close_time_utc")),
        "target_higher_open_time_utc": _format_optional_utc(target_details.get("target_higher_open_time_utc")),
        "strategy_count": result.strategy_count,
        "success_count": result.success_count,
        "failed_count": result.failed_count,
        "invalid_count": result.invalid_count,
        "not_implemented_count": result.not_implemented_count,
        "message": message,
        "error_message": error_message,
    }


def _empty_check_result(
    *,
    status: str,
    exit_code: int,
    message: str,
    error_message: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "exit_code": exit_code,
        "event_id": "",
        "run_id": "",
        "snapshot_id": "",
        "target_base_open_time_utc": "",
        "target_base_close_time_utc": "",
        "target_higher_open_time_utc": "",
        "strategy_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "invalid_count": 0,
        "not_implemented_count": 0,
        "message": message,
        "error_message": error_message,
    }


def _print_check_lines(result: dict[str, Any]) -> None:
    for key in (
        "status",
        "exit_code",
        "event_id",
        "run_id",
        "snapshot_id",
        "target_base_open_time_utc",
        "target_base_close_time_utc",
        "target_higher_open_time_utc",
        "strategy_count",
        "success_count",
        "failed_count",
        "invalid_count",
        "not_implemented_count",
        "message",
        "error_message",
    ):
        print(f"{key}={result[key]}")


def _exit_code_for_scheduler_result(result: StrategySignalSchedulerResult) -> int:
    if result.status in {
        StrategySignalSchedulerStatus.SUCCESS,
        StrategySignalSchedulerStatus.PARTIAL_SUCCESS,
        StrategySignalSchedulerStatus.SKIPPED,
        StrategySignalSchedulerStatus.WAITING_UPSTREAM,
    }:
        return EXIT_SUCCESS
    if result.status == StrategySignalSchedulerStatus.BLOCKED:
        return EXIT_BLOCKED
    return EXIT_FAILED


def _parse_utc_datetime(raw_value: str) -> datetime:
    normalized = raw_value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    value = datetime.fromisoformat(normalized)
    if value.tzinfo is None:
        raise ValueError("--upstream-slot-time-utc must include UTC timezone, for example 2026-05-18T12:05:00Z")
    return _ensure_utc(value)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("strategy signal scheduler check time must be timezone-aware UTC")
    return value.astimezone(UTC)


def _format_optional_utc(value: Any) -> str:
    if value is None:
        return ""
    return _ensure_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
