"""Manual CLI entry for stage-14 BTCUSDT 1d Kline integrity review.

Triggered by: a user running `python -m scripts.check_kline_integrity_1d`.
Manual execution: allowed, mainly to produce the 1d `data_quality_check`
precondition required by stage-15 MarketContextSnapshot.
Scheduler execution: not allowed through this script; scheduler jobs call
`app/scheduler/jobs/kline_1d_integrity_check.py::run_kline_1d_integrity_check_job`,
which directly invokes the app service.
Required args: `--check-trigger cli` (compat alias: `--trigger-source cli`).
Optional args: `--symbol`, `--interval`, `--lookback-count` (compat alias:
`--limit`), `--notify-success`, and `--no-notify-success`.
Service called: `app/market_data/kline_integrity/kline_1d_integrity_service.py::run_daily_1d_kline_integrity_check`.
Database/Redis/Hermes impact: delegated to the service. This script does not
request Binance directly, write repositories directly, or send Hermes directly.
Formal Kline impact: never writes, overwrites, deletes, repairs, or backfills
`market_kline_1d` or `market_kline_4h`.
DeepSeek, large model calls, strategy advice, and trading execution: none.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import KLINE_1D_INTERVAL_VALUE, TRIGGER_SOURCE_CLI
from app.market_data.kline_integrity.kline_1d_integrity_service import run_daily_1d_kline_integrity_check
from app.market_data.kline_integrity.kline_1d_integrity_types import (
    EXIT_PARAMETER_ERROR,
    DailyKline1dIntegrityCheckRequest,
    DailyKline1dIntegrityCheckResult,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual 1d integrity-review CLI parser."""

    settings = get_settings()
    parser = argparse.ArgumentParser(description="Manually run the read-only BTCUSDT 1d Kline integrity review.")
    parser.add_argument("--symbol", default=settings.daily_kline_1d_integrity_symbol)
    parser.add_argument(
        "--interval",
        default=settings.daily_kline_1d_integrity_interval,
        choices=[KLINE_1D_INTERVAL_VALUE],
    )
    parser.add_argument(
        "--lookback-count",
        "--limit",
        dest="lookback_count",
        type=int,
        default=settings.daily_kline_1d_integrity_limit,
        help="Recent closed 1d Kline count to review. --limit is kept as a compatibility alias.",
    )
    parser.add_argument(
        "--check-trigger",
        "--trigger-source",
        dest="check_trigger",
        required=True,
        choices=[TRIGGER_SOURCE_CLI],
        help="Manual CLI reviews must pass cli. --trigger-source is a compatibility alias.",
    )
    parser.add_argument("--notify-success", dest="notify_success", action="store_true")
    parser.add_argument("--no-notify-success", dest="notify_success", action="store_false")
    parser.set_defaults(notify_success=settings.daily_kline_1d_integrity_notify_success)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call the 1d integrity service once, print a compact summary, and return an exit code."""

    try:
        args = build_arg_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    settings = get_settings()
    request = DailyKline1dIntegrityCheckRequest(
        symbol=args.symbol.strip().upper(),
        interval_value=args.interval,
        lookback_count=args.lookback_count,
        check_trigger=args.check_trigger,
        notify_success=args.notify_success,
        lock_ttl_seconds=settings.daily_kline_1d_integrity_lock_ttl_seconds,
    )

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=True) as db_session:
        result = run_daily_1d_kline_integrity_check(request, db_session=db_session)

    for line in format_daily_1d_kline_integrity_result_lines(result):
        print(line)
    return result.exit_code


def format_daily_1d_kline_integrity_result_lines(result: DailyKline1dIntegrityCheckResult) -> list[str]:
    """Format the service result without printing payloads, Kline arrays, or internal objects."""

    lines = [
        (
            f"daily_kline_1d_integrity_status={result.status.value}; exit_code={result.exit_code}; "
            f"requested_count={result.requested_count}; checked_count={result.checked_count}; "
            f"issue_count={result.issue_count}; alert_status={result.alert_status or ''}"
        ),
        f"trace_id={result.trace_id}",
        f"quality_check_id={result.quality_check_id or ''}",
        f"checked_time_utc={result.checked_start_time or ''}..{result.checked_end_time or ''}",
        f"latest_open_time_ms={result.latest_open_time_ms or ''}",
        f"expected_latest_open_time_ms={result.expected_latest_open_time_ms or ''}",
        f"message={result.message}",
    ]
    if result.first_issue_type:
        lines.append("first_issue=" f"{result.first_issue_type}; message={result.first_issue_message or ''}")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
