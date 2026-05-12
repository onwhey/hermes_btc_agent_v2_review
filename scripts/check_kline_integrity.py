"""Manual CLI entry for phase-11 4h Kline integrity review.

Triggered by: a user running `python -m scripts.check_kline_integrity`.
Manual execution: allowed for local debugging and operational checks.
Scheduler execution: not allowed through this script; scheduler jobs call
`app/scheduler/jobs/daily_kline_integrity_check.py::run_daily_kline_integrity_check_job`,
which directly invokes the app service.
Required args: `--check-trigger cli` (compat alias: `--trigger-source cli`).
Optional args: `--symbol`, `--interval`, `--lookback-count` (compat alias:
`--limit`), `--notify-success`, and `--no-notify-success`.
Service called: `app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check`.
Database/Redis/Hermes impact: delegated to the service. This script does not
request Binance directly, write repositories directly, or send Hermes directly.
Formal Kline impact: never writes, overwrites, deletes, repairs, or backfills
`market_kline_4h`. This stage supports only recent-N review; `--start-time`
and `--end-time` are rejected until range review is implemented.
Automatic repair, DeepSeek, strategy advice, and trading execution: none.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import KLINE_4H_INTERVAL_VALUE
from app.market_data.kline_integrity.kline_integrity_service import (
    format_daily_kline_integrity_result_lines,
    run_daily_kline_integrity_check,
)
from app.market_data.kline_integrity.types import (
    CHECK_MODE_MANUAL_INTEGRITY_CHECK,
    EXIT_PARAMETER_ERROR,
    DailyKlineIntegrityCheckRequest,
)
from app.market_data.kline_quality.types import CHECK_TRIGGER_SOURCE_CLI


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual integrity-review CLI parser."""

    settings = get_settings()
    parser = argparse.ArgumentParser(description="Manually run the official 4h Kline integrity review.")
    parser.add_argument("--symbol", default=settings.daily_kline_integrity_symbol)
    parser.add_argument("--interval", default=settings.daily_kline_integrity_interval, choices=[KLINE_4H_INTERVAL_VALUE])
    parser.add_argument(
        "--lookback-count",
        "--limit",
        dest="lookback_count",
        type=int,
        default=settings.daily_kline_integrity_limit,
        help="Recent closed 4h Kline count to review. --limit is kept as a compatibility alias.",
    )
    parser.add_argument(
        "--check-trigger",
        "--trigger-source",
        dest="check_trigger",
        required=True,
        choices=[CHECK_TRIGGER_SOURCE_CLI],
        help="Manual CLI reviews must pass cli. --trigger-source is a compatibility alias.",
    )
    parser.add_argument("--start-time", default=None, help="Reserved for a future range-review stage.")
    parser.add_argument("--end-time", default=None, help="Reserved for a future range-review stage.")
    parser.add_argument("--notify-success", dest="notify_success", action="store_true")
    parser.add_argument("--no-notify-success", dest="notify_success", action="store_false")
    parser.set_defaults(notify_success=settings.daily_kline_integrity_notify_success)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call the service once, print a summary, and return an exit code."""

    try:
        args = build_arg_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    if args.start_time or args.end_time:
        print("start/end range review is not implemented in phase 11; use --lookback-count instead.")
        return EXIT_PARAMETER_ERROR

    request = DailyKlineIntegrityCheckRequest(
        symbol=args.symbol.strip().upper(),
        interval_value=args.interval,
        lookback_count=args.lookback_count,
        check_trigger=args.check_trigger,
        check_mode=CHECK_MODE_MANUAL_INTEGRITY_CHECK,
        notify_success=args.notify_success,
        lock_ttl_seconds=get_settings().daily_kline_integrity_lock_ttl_seconds,
    )

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=True) as db_session:
        result = run_daily_kline_integrity_check(request, db_session=db_session)

    for line in format_daily_kline_integrity_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
