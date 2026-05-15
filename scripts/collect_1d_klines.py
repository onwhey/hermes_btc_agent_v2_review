"""Manual CLI entry for stage-14-3 BTCUSDT 1d incremental collection.

Triggered by: a user running `python -m scripts.collect_1d_klines`.
Manual execution: allowed for verification and operations checks.
Scheduler execution: not allowed through this script; future scheduler jobs must
call `app/market_data/collector/kline_1d_incremental_collector.py::run_incremental_1d_collection`
directly with `trigger_source=scheduler`.
Required args: `--trigger-source cli`. Real writes require `--confirm-write`;
otherwise use `--dry-run`.
Calls: `app/market_data/collector/kline_1d_incremental_collector.py::run_incremental_1d_collection`.
Business logic: lives in `app/market_data/collector`, not in this script.
Database impact: delegated to the service; may write collector/data-quality/formal
1d Kline rows only after quality checks pass.
Redis impact: delegated to the service; only the Kline write task lock is used.
Hermes impact: delegated to the service; real-write blocked/failed results send
fixed alerts, while success alerts require `--notify-success`.
Formal Kline impact: never direct from this script and only targets `market_kline_1d`.
Repair/trading impact: no automatic repair, no manual field editing, no trading.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.market_data.collector.kline_1d_incremental_collector import run_incremental_1d_collection
from app.market_data.collector.kline_1d_incremental_types import (
    DEFAULT_1D_INCREMENTAL_MAX_CLOSED_COUNT,
    EXIT_PARAMETER_ERROR,
    IncrementalKline1dCollectRequest,
    format_incremental_1d_collect_result_lines,
)
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_1D_INTERVAL_VALUE, TRIGGER_SOURCE_CLI


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual 1d incremental collector CLI parser."""

    parser = argparse.ArgumentParser(
        description="Manually run official Binance REST 1d incremental collection."
    )
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--interval", default=KLINE_1D_INTERVAL_VALUE, choices=[KLINE_1D_INTERVAL_VALUE])
    parser.add_argument("--max-closed-count", type=int, default=DEFAULT_1D_INCREMENTAL_MAX_CLOSED_COUNT)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--notify-success", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, call the service, print its result, and return exit code."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = IncrementalKline1dCollectRequest(
        symbol=args.symbol.strip().upper(),
        interval_value=args.interval,
        trigger_source=args.trigger_source,
        dry_run=args.dry_run,
        confirm_write=args.confirm_write,
        notify_success=args.notify_success,
        max_closed_count=args.max_closed_count,
    )

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=False) as db_session:
        result = run_incremental_1d_collection(request, db_session=db_session)

    for line in format_incremental_1d_collect_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
