"""Manual debug CLI for phase-09 BTCUSDT 4h incremental collection.

Triggered by: a user running `python -m scripts.collect_4h_klines`.
Manual execution: allowed for local debugging and operational checks.
Scheduler execution: not allowed through this script; future scheduler jobs must
call `app/market_data/collector/kline_4h_collector_service.py::run_incremental_4h_collection`
directly with `trigger_source=scheduler`.
Required args: `--trigger-source cli`. Real writes require `--confirm-write`;
otherwise use `--dry-run`.
Database/Redis/Hermes impact: delegated to the service. The script does not
request Binance directly, write repositories directly, or send Hermes directly.
Formal Kline impact: never direct from this script.
Repair/trading impact: no automatic repair, no extra-range backfill, no trading.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.market_data.collector.kline_4h_collector_service import (
    format_incremental_collect_result_lines,
    run_incremental_4h_collection,
)
from app.market_data.collector.types import (
    DEFAULT_COLLECT_LIMIT,
    EXIT_PARAMETER_ERROR,
    IncrementalKlineCollectRequest,
)
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_4H_INTERVAL_VALUE, TRIGGER_SOURCE_CLI


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual incremental collector CLI parser."""

    parser = argparse.ArgumentParser(description="Manually run official Binance REST 4h incremental collection.")
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--interval", default=KLINE_4H_INTERVAL_VALUE, choices=[KLINE_4H_INTERVAL_VALUE])
    parser.add_argument("--limit", type=int, default=DEFAULT_COLLECT_LIMIT)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--notify-success", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, call the collector service, print result, return exit code."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = IncrementalKlineCollectRequest(
        symbol=args.symbol.strip().upper(),
        interval_value=args.interval,
        trigger_source=args.trigger_source,
        limit=args.limit,
        dry_run=args.dry_run,
        confirm_write=args.confirm_write,
        notify_success=args.notify_success,
    )

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=False) as db_session:
        result = run_incremental_4h_collection(request, db_session=db_session)

    for line in format_incremental_collect_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

