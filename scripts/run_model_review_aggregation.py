"""Manual CLI entry for stage-20A model review aggregation.

Triggered by: a user running `python -m scripts.run_model_review_aggregation`.
Manual execution: allowed for dry-run validation and explicit confirmed writes.
Scheduler execution: not allowed in stage 20A.
Required args: `--material-pack-id` and `--trigger-source cli`.
Real writes require `--confirm-write`; otherwise dry-run is the default.
Calls: `app/model_review_aggregation/service.py::run_model_review_aggregation`.
Business logic: lives in `app/model_review_aggregation`, not in this script.
Database impact: dry-run writes no `model_review_aggregation_run`; confirm-write
delegates one compact stage-20A row to the service.
Redis impact: none.
Hermes impact: none in stage 20A.
Formal Kline impact: this script is not allowed to modify `market_kline_4h` or
`market_kline_1d`.
Data repair/model/trading impact: no automatic repair, no model provider call,
no final trading advice, no private trading state reads, and no trading.
Scheduler note: stage 20A must not be called by scheduler; scheduler also must
not use this script to trigger stage 19.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_review_aggregation.schema import (
    EXIT_PARAMETER_ERROR,
    ModelReviewAggregationRequest,
    format_model_review_aggregation_result_lines,
)
from app.model_review_aggregation.service import run_model_review_aggregation
from app.storage.mysql.session import session_scope


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual stage-20A aggregation CLI parser."""

    parser = argparse.ArgumentParser(description="Run stage-20A model review aggregation.")
    parser.add_argument("--material-pack-id", required=True)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only validation. This is the default.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow stage 20A to write one aggregation row.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call only the stage-20A service, print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = ModelReviewAggregationRequest(
        material_pack_id=args.material_pack_id.strip(),
        trigger_source=args.trigger_source,
        dry_run=not bool(args.confirm_write),
        confirm_write=bool(args.confirm_write),
        created_by="cli",
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = run_model_review_aggregation(db_session=db_session, request=request)

    for line in format_model_review_aggregation_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
