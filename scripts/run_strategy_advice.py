"""Manual CLI entry for stage-21A strategy advice lifecycle.

Triggered by: a user running `python -m scripts.run_strategy_advice`.
Manual execution: allowed for dry-run validation and explicit confirmed writes.
Scheduler execution: not allowed in stage 21A.
Required args: `--review-aggregation-run-id` and `--trigger-source cli`.
Real writes require `--confirm-write`; otherwise dry-run is the default.
Calls: `app/strategy_advice/service.py::run_strategy_advice`.
Business logic: lives in `app/strategy_advice`, not in this script.
Database impact: dry-run writes no rows; confirm-write delegates compact
stage-21A advice, lifecycle review, event, and setup rows to the service.
Redis impact: none.
Hermes impact: none in stage 21A; this script only prints generated
notification fields.
Formal Kline impact: this script is not allowed to modify `market_kline_4h` or
`market_kline_1d`.
Data repair/model/trading impact: no automatic repair, no model provider call,
no stage-19 call, no scheduler hook, no private trading state reads, and no
trading execution.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.storage.mysql.session import session_scope
from app.strategy_advice.schema import (
    EXIT_PARAMETER_ERROR,
    StrategyAdviceRequest,
    format_strategy_advice_result_lines,
)
from app.strategy_advice.service import run_strategy_advice


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual stage-21A advice lifecycle CLI parser."""

    parser = argparse.ArgumentParser(description="Run stage-21A strategy advice lifecycle management.")
    parser.add_argument("--review-aggregation-run-id", required=True)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only validation. This is the default.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow stage 21A to write advice lifecycle rows.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call only the stage-21A service, print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = StrategyAdviceRequest(
        review_aggregation_run_id=args.review_aggregation_run_id.strip(),
        trigger_source=args.trigger_source,
        dry_run=not bool(args.confirm_write),
        confirm_write=bool(args.confirm_write),
        created_by="cli",
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = run_strategy_advice(db_session=db_session, request=request)

    for line in format_strategy_advice_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
