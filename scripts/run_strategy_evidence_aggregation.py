"""Manual CLI entry for stage-23F strategy evidence aggregation.

Triggered by: a user running `python -m scripts.run_strategy_evidence_aggregation`.
Manual execution: allowed for dry-run validation and explicit confirmed writes.
Scheduler execution: not allowed in this script for 23F.
Required args: `--strategy-signal-run-id` and `--trigger-source cli`.
Real writes require `--confirm-write`; otherwise dry-run is the default.
Calls: `app/strategy/aggregation/evidence_service.py::run_strategy_evidence_aggregation`.
Business logic: lives in `app/strategy/aggregation`, not in this script.
Database impact: dry-run writes no rows; confirm-write writes or updates only
`strategy_evidence_aggregation_result` through the service.
Redis impact: none.
Hermes impact: none.
Formal Kline impact: this script is not allowed to modify `market_kline_4h` or
`market_kline_1d`.
Data repair/trading impact: no automatic repair, no manual Kline editing, no
strategy rerun, no snapshot generation, no external market request, no private
trading state reads, no final advice, no trade setup, and no trading.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.storage.mysql.session import session_scope
from app.strategy.aggregation.evidence_service import run_strategy_evidence_aggregation
from app.strategy.aggregation.evidence_types import (
    EXIT_PARAMETER_ERROR,
    EvidenceAggregationRequest,
    format_evidence_aggregation_result_lines,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual 23F evidence aggregation CLI parser."""

    parser = argparse.ArgumentParser(description="Run strategy evidence aggregation for one strategy signal run.")
    parser.add_argument("--strategy-signal-run-id", required=True)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only validation. This is the default.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow 23F to write aggregation rows.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call only the 23F service, print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = EvidenceAggregationRequest(
        strategy_signal_run_id=args.strategy_signal_run_id.strip(),
        trigger_source=args.trigger_source,
        dry_run=not bool(args.confirm_write),
        confirm_write=bool(args.confirm_write),
        created_by="cli",
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = run_strategy_evidence_aggregation(db_session=db_session, request=request)

    for line in format_evidence_aggregation_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
