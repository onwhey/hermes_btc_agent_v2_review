"""Manual CLI entry for stage-14-2 BTCUSDT 1d Kline backfill.

Triggered by: a user running `python -m scripts.backfill_1d_klines`.
Manual execution: allowed.
Scheduler execution: not allowed in stage 14-2.
Required args: `--trigger-source cli` and explicit UTC open-time bounds.
Real writes require `--confirm-write`; otherwise use `--dry-run`.
Calls: `app/market_data/backfill/kline_1d_backfill_service.py::run_manual_1d_backfill`.
Business logic: lives in `app/market_data/backfill`, not in this script.
Database impact: delegated to the service; may write collector/data-quality/formal
1d Kline rows only after quality checks pass.
Redis impact: delegated to the service; only the Kline write task lock is used.
Hermes impact: delegated to the service; real-write blocked/failed results send
fixed alerts, while dry-run never submits real Hermes unless `--notify-success`
is explicitly used for a success summary.
Formal Kline impact: never direct from this script and only targets `market_kline_1d`.
Repair/trading impact: no automatic repair, no manual field editing, no trading.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Sequence

from app.core.time_utils import UTC, utc_datetime_to_timestamp_ms
from app.market_data.backfill.kline_1d_backfill_service import run_manual_1d_backfill
from app.market_data.backfill.kline_1d_types import (
    DEFAULT_1D_BACKFILL_LIMIT_PER_REQUEST,
    EXIT_PARAMETER_ERROR,
    ManualKline1dBackfillRequest,
    format_manual_1d_backfill_result_lines,
)
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_1D_INTERVAL_VALUE, TRIGGER_SOURCE_CLI


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual 1d backfill CLI parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Manually backfill official Binance REST 1d Klines. "
            "--start-utc and --end-utc are inclusive 1d open-time UTC boundaries."
        )
    )
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--interval", default=KLINE_1D_INTERVAL_VALUE, choices=[KLINE_1D_INTERVAL_VALUE])
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)
    parser.add_argument("--limit-per-request", type=int, default=DEFAULT_1D_BACKFILL_LIMIT_PER_REQUEST)
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
        start_open_time_ms, end_open_time_ms = _resolve_utc_bounds(args, parser)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = ManualKline1dBackfillRequest(
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
        result = run_manual_1d_backfill(request, db_session=db_session)

    for line in format_manual_1d_backfill_result_lines(result):
        print(line)
    return result.exit_code


def _resolve_utc_bounds(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[int, int]:
    """Resolve and validate inclusive UTC 1d open-time bounds."""

    try:
        start_open_time_ms = _parse_utc_midnight_to_ms(args.start_utc, field_name="start-utc")
        end_open_time_ms = _parse_utc_midnight_to_ms(args.end_utc, field_name="end-utc")
    except ValueError as exc:
        parser.error(str(exc))
    if start_open_time_ms > end_open_time_ms:
        parser.error("end-utc must be greater than or equal to start-utc")
    return start_open_time_ms, end_open_time_ms


def _parse_utc_midnight_to_ms(value: str, *, field_name: str) -> int:
    """Parse a UTC datetime that must align to 00:00:00 into milliseconds."""

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include Z or an explicit UTC offset")
    utc_value = parsed.astimezone(UTC)
    if (
        utc_value.hour != 0
        or utc_value.minute != 0
        or utc_value.second != 0
        or utc_value.microsecond != 0
    ):
        raise ValueError(f"{field_name} must align to UTC 00:00:00")
    return utc_datetime_to_timestamp_ms(utc_value)


if __name__ == "__main__":
    raise SystemExit(main())
