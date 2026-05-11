"""Manual CLI entry for phase-08 BTCUSDT 4h Kline backfill.

Triggered by: a user running `python -m scripts.backfill_4h_klines`.
Manual execution: allowed.
Scheduler execution: not allowed in phase 08.
Required args: `--trigger-source cli` and either open-time millisecond bounds or
UTC bounds. Real writes require `--confirm-write`; otherwise use `--dry-run`.
Calls: `app/market_data/backfill/kline_4h_backfill_service.py::run_manual_4h_backfill`.
Business logic: lives in `app/market_data/backfill`, not in this script.
Database impact: delegated to the service; may write collector/data-quality/formal
Kline rows only after quality checks pass.
Redis impact: delegated to the service; only the Kline write task lock is used.
Hermes impact: delegated to the service; failures must alert and cannot be
disabled by CLI parameters. Success alerts require `--notify-success`.
Formal Kline impact: never direct from this script.
Repair/trading impact: no automatic repair, no manual field editing, no trading.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Sequence

from app.core.time_utils import UTC, utc_datetime_to_timestamp_ms
from app.market_data.backfill.kline_4h_backfill_service import (
    format_manual_backfill_result_lines,
    run_manual_4h_backfill,
)
from app.market_data.backfill.types import (
    DEFAULT_BACKFILL_LIMIT_PER_REQUEST,
    EXIT_PARAMETER_ERROR,
    ManualKlineBackfillRequest,
)
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_4H_INTERVAL_VALUE, TRIGGER_SOURCE_CLI


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual backfill CLI parser."""

    parser = argparse.ArgumentParser(description="Manually backfill official Binance REST 4h Klines.")
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--interval", default=KLINE_4H_INTERVAL_VALUE, choices=[KLINE_4H_INTERVAL_VALUE])
    parser.add_argument("--start-open-time-ms", "--start-time", dest="start_open_time_ms", type=int)
    parser.add_argument("--end-open-time-ms", "--end-time", dest="end_open_time_ms", type=int)
    parser.add_argument("--start-utc")
    parser.add_argument("--end-utc")
    parser.add_argument("--limit-per-request", type=int, default=DEFAULT_BACKFILL_LIMIT_PER_REQUEST)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--notify-success", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, call the service, print its result, and return its exit code."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
        start_open_time_ms, end_open_time_ms = _resolve_time_bounds(args, parser)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = ManualKlineBackfillRequest(
        symbol=args.symbol.strip().upper(),
        interval_value=args.interval,
        start_open_time_ms=start_open_time_ms,
        end_open_time_ms=end_open_time_ms,
        trigger_source=args.trigger_source,
        dry_run=args.dry_run,
        confirm_write=args.confirm_write,
        notify_success=args.notify_success,
        limit_per_request=args.limit_per_request,
    )

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=False) as db_session:
        result = run_manual_4h_backfill(request, db_session=db_session)

    for line in format_manual_backfill_result_lines(result):
        print(line)
    return result.exit_code


def _resolve_time_bounds(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[int, int]:
    """Resolve mutually exclusive UTC or millisecond open-time bounds."""

    has_ms_bounds = args.start_open_time_ms is not None or args.end_open_time_ms is not None
    has_utc_bounds = bool(args.start_utc or args.end_utc)
    if has_ms_bounds and has_utc_bounds:
        parser.error("Use either millisecond open-time bounds or UTC bounds, not both.")
    if has_ms_bounds:
        if args.start_open_time_ms is None or args.end_open_time_ms is None:
            parser.error("Both --start-open-time-ms and --end-open-time-ms are required.")
        return int(args.start_open_time_ms), int(args.end_open_time_ms)
    if has_utc_bounds:
        if not args.start_utc or not args.end_utc:
            parser.error("Both --start-utc and --end-utc are required.")
        try:
            return _parse_utc_to_ms(args.start_utc), _parse_utc_to_ms(args.end_utc)
        except ValueError as exc:
            parser.error(str(exc))
    parser.error("A bounded backfill range is required.")
    raise AssertionError("argparse.error should have exited")


def _parse_utc_to_ms(value: str) -> int:
    """Parse an explicit UTC datetime string into milliseconds."""

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid UTC datetime: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError("UTC time must include Z or an explicit offset")
    return utc_datetime_to_timestamp_ms(parsed.astimezone(UTC))


if __name__ == "__main__":
    raise SystemExit(main())
