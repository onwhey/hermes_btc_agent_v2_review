"""Manual CLI entry for stage-16 strategy signal runs.

Triggered by: a user running `python -m scripts.run_strategy_signals`.
Manual execution: allowed for dry-run validation and confirmed strategy signal
result persistence.
Scheduler execution: not allowed in stage 16; no scheduler job should call this
script in this phase.
Required args: `--trigger-source cli` plus exactly one of `--snapshot-id` or
`--ensure-latest-snapshot`. Real strategy result writes require `--confirm-write`;
otherwise use `--dry-run`.
Calls: `app/strategy/signal_service.py::run_strategy_signals`.
Business logic: lives in `app/strategy`, not in this script.
Database impact: delegated to the service. It may write only strategy signal
tables on confirm-write. When ensure-latest-snapshot has no reusable snapshot,
the delegated resolver may call the stage-15 snapshot service to create a
MarketContextSnapshot prerequisite.
Redis impact: none.
Hermes impact: none in stage 16.
Formal Kline impact: this script is not allowed to modify `market_kline_4h` or
`market_kline_1d`.
Data repair/trading impact: no automatic repair, no manual field editing, no
account or private state reading, and no trading.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE, TRIGGER_SOURCE_CLI
from app.strategy.signal_service import run_strategy_signals
from app.strategy.types import (
    EXIT_PARAMETER_ERROR,
    StrategySignalRunRequest,
    format_strategy_signal_run_result_lines,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual strategy signal CLI parser."""

    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Run independent strategy signals from an existing or ensured MarketContextSnapshot."
    )
    parser.add_argument("--snapshot-id")
    parser.add_argument("--ensure-latest-snapshot", action="store_true")
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
    parser.add_argument(
        "--lookback-base",
        "--lookback-4h",
        dest="lookback_base",
        type=int,
        default=settings.market_context_4h_lookback_count,
    )
    parser.add_argument(
        "--lookback-higher",
        "--lookback-1d",
        dest="lookback_higher",
        type=int,
        default=settings.market_context_1d_lookback_count,
    )
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, call the app service, print compact output, and return exit code."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = StrategySignalRunRequest(
        snapshot_id=_normalize_optional_snapshot_id(args.snapshot_id),
        ensure_latest_snapshot=bool(args.ensure_latest_snapshot),
        symbol=args.symbol.strip().upper(),
        base_interval_value=args.base_interval,
        higher_interval_value=args.higher_interval,
        lookback_base_count=args.lookback_base,
        lookback_higher_count=args.lookback_higher,
        trigger_source=args.trigger_source,
        dry_run=args.dry_run,
        confirm_write=args.confirm_write,
        created_by="cli",
    )

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=False) as db_session:
        result = run_strategy_signals(db_session=db_session, request=request)

    for line in format_strategy_signal_run_result_lines(result):
        print(line)
    return result.exit_code


def _normalize_optional_snapshot_id(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


if __name__ == "__main__":
    raise SystemExit(main())
