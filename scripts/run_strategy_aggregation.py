"""Manual CLI entry for stage-18 strategy aggregation and material packs.

Triggered by: a user running `python -m scripts.run_strategy_aggregation`.
Manual execution: allowed for dry-run validation and explicit confirmed writes.
Scheduler execution: not allowed through this script; scheduler uses
`app/scheduler/jobs/strategy_aggregation_job.py`.
Required args: `--strategy-signal-run-id` and `--trigger-source cli`.
Real stage-18 writes require `--confirm-write`; otherwise dry-run is the
default.
Calls: `app/strategy/aggregation/service.py::run_strategy_aggregation`.
Business logic: lives in `app/strategy/aggregation`, not in this script.
Database impact: dry-run writes no `strategy_aggregation_run` and no
`analysis_material_pack`; confirm-write delegates persistence to stage 18.
Redis impact: none.
Hermes impact: delegated to stage 18 only during confirmed writes and only
when `.env` enables it.
Formal Kline impact: this script is not allowed to modify `market_kline_4h` or
`market_kline_1d`.
Data repair/trading impact: no automatic repair, no manual field editing, no
stage-16 rerun, no stage-15 snapshot generation, no external market request,
no private trading state reads, and no trading.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.strategy.aggregation.service import run_strategy_aggregation
from app.strategy.aggregation.types import (
    EXIT_PARAMETER_ERROR,
    StrategyAggregationRequest,
    format_strategy_aggregation_result_lines,
)
from app.storage.mysql.session import session_scope


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual stage-18 aggregation CLI parser."""

    parser = argparse.ArgumentParser(
        description="Run deterministic strategy aggregation and build an analysis material pack."
    )
    parser.add_argument("--strategy-signal-run-id", required=True)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only validation. This is the default.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow stage 18 to write aggregation rows.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call only the stage-18 service, print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    dry_run = not bool(args.confirm_write)
    request = StrategyAggregationRequest(
        strategy_signal_run_id=args.strategy_signal_run_id.strip(),
        trigger_source=args.trigger_source,
        dry_run=dry_run,
        confirm_write=bool(args.confirm_write),
        created_by="cli",
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = run_strategy_aggregation(db_session=db_session, request=request)

    for line in format_strategy_aggregation_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
