"""Manual debug CLI for phase-11 daily 4h Kline integrity review.

Triggered by: a user running `python -m scripts.run_daily_kline_integrity_check`.
Manual execution: allowed for local debugging and operational checks.
Scheduler execution: not allowed through this script; scheduler jobs must call
`app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check`
directly with `check_trigger_source=scheduler`.
Required args: `--trigger-source cli`.
Optional args: `--symbol`, `--interval`, `--limit`, `--notify-success`,
and `--no-notify-success`.
Database/Redis/Hermes impact: delegated to the service. The script does not
request Binance directly, write repositories directly, or send Hermes directly.
Formal Kline impact: never writes, overwrites, deletes, repairs, or backfills
`market_kline_4h`.
Automatic repair and trading impact: none.
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
    EXIT_PARAMETER_ERROR,
    DailyKlineIntegrityCheckRequest,
)
from app.market_data.kline_quality.types import CHECK_TRIGGER_SOURCE_CLI


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual daily integrity CLI parser."""

    settings = get_settings()
    parser = argparse.ArgumentParser(description="Manually run the daily official 4h Kline integrity review.")
    parser.add_argument("--symbol", default=settings.daily_kline_integrity_symbol)
    parser.add_argument("--interval", default=settings.daily_kline_integrity_interval, choices=[KLINE_4H_INTERVAL_VALUE])
    parser.add_argument("--limit", type=int, default=settings.daily_kline_integrity_limit)
    parser.add_argument("--trigger-source", required=True, choices=[CHECK_TRIGGER_SOURCE_CLI])
    parser.add_argument("--notify-success", dest="notify_success", action="store_true")
    parser.add_argument("--no-notify-success", dest="notify_success", action="store_false")
    parser.set_defaults(notify_success=settings.daily_kline_integrity_notify_success)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, call the service, print the result, and return an exit code."""

    try:
        args = build_arg_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = DailyKlineIntegrityCheckRequest(
        symbol=args.symbol.strip().upper(),
        interval_value=args.interval,
        limit=args.limit,
        check_trigger_source=args.trigger_source,
        notify_success=args.notify_success,
    )

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=True) as db_session:
        result = run_daily_kline_integrity_check(request, db_session=db_session)

    for line in format_daily_kline_integrity_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
