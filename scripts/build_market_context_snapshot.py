"""Manual CLI entry for stage-15 BTCUSDT 4h + 1d MarketContextSnapshot.

Triggered by: a user running `python -m scripts.build_market_context_snapshot`.
Manual execution: allowed for dry-run validation and confirmed snapshot creation.
Scheduler execution: not allowed through this script; if a later phase needs
scheduler support, the scheduler must call `app/market_context/snapshot_service.py::build_market_context_snapshot`
directly, not this script.
Required args: `--trigger-source cli`. Real writes require `--confirm-write`;
otherwise use `--dry-run`.
Calls: `app/market_context/snapshot_service.py::build_market_context_snapshot`.
Business logic: lives in `app/market_context`, not in this script.
Database impact: delegated to the service; may write only `market_context_snapshot`
when confirm-write is supplied.
Redis impact: none.
Hermes impact: delegated to the service, and only when `--notify-on-blocked` or
`--notify-on-failed` is explicitly supplied.
Formal Kline impact: this script is not allowed to modify `market_kline_4h` or
`market_kline_1d`.
Data repair/trading impact: no automatic repair, no manual field editing, no
account or private state reading, and no trading.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_context.snapshot_service import build_market_context_snapshot
from app.market_context.snapshot_types import (
    EXIT_PARAMETER_ERROR,
    MarketContextSnapshotRequest,
    format_market_context_snapshot_result_lines,
)
from app.market_data.kline_constants import (
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual MarketContextSnapshot CLI parser."""

    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Build a BTCUSDT 4h + 1d market fact snapshot from existing formal Kline tables."
    )
    parser.add_argument("--symbol", default=settings.market_context_symbol)
    parser.add_argument(
        "--base-interval",
        default=settings.market_context_base_interval,
        choices=[KLINE_4H_INTERVAL_VALUE],
    )
    parser.add_argument(
        "--higher-interval",
        default=settings.market_context_higher_interval,
        choices=[KLINE_1D_INTERVAL_VALUE],
    )
    parser.add_argument("--lookback-4h", type=int, default=settings.market_context_4h_lookback_count)
    parser.add_argument("--lookback-1d", type=int, default=settings.market_context_1d_lookback_count)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--notify-on-blocked", action="store_true")
    parser.add_argument("--notify-on-failed", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, call the app service, print a compact summary, and return exit code."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = MarketContextSnapshotRequest(
        symbol=args.symbol.strip().upper(),
        base_interval_value=args.base_interval,
        higher_interval_value=args.higher_interval,
        trigger_source=args.trigger_source,
        lookback_4h_count=args.lookback_4h,
        lookback_1d_count=args.lookback_1d,
        dry_run=args.dry_run,
        confirm_write=args.confirm_write,
        notify_on_blocked=args.notify_on_blocked,
        notify_on_failed=args.notify_on_failed,
        created_by="cli",
    )

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=False) as db_session:
        result = build_market_context_snapshot(db_session=db_session, request=request)

    for line in format_market_context_snapshot_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
