"""Manual CLI entry for stage-20B model review chain state machine.

Triggered by: a user running `python -m scripts.run_model_review_chain`.
Manual execution: allowed for dry-run validation and explicit confirmed writes.
Scheduler execution: not allowed in stage 20B.
Required args: create mode needs `--material-pack-id`, `--chain-key`, and
`--trigger-source cli`; resume mode needs `--chain-id`, `--resume`, and
`--trigger-source cli`.
Real writes require `--confirm-write`; otherwise dry-run is the default.
Calls: `app/model_review_chain/service.py::run_model_review_chain`.
Business logic: lives in `app/model_review_chain`, not in this script.
Database impact: dry-run writes no rows; confirm-write delegates compact
stage-20B chain/step rows and mock `model_analysis_run` attempts to the
service.
Redis impact: none.
Hermes impact: none in stage 20B.
Formal Kline impact: this script is not allowed to modify `market_kline_4h` or
`market_kline_1d`.
Data repair/model/trading impact: no automatic repair, no real model provider
call, no final trading advice, no private trading state reads, and no trading.
Scheduler note: stage 20B must not be called by scheduler and this script only
accepts `--trigger-source cli`.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_review_chain.schema import (
    DEFAULT_CHAIN_KEY,
    DEFAULT_MAX_RETRY_COUNT,
    EXIT_PARAMETER_ERROR,
    ModelReviewChainRequest,
    format_model_review_chain_result_lines,
)
from app.model_review_chain.service import run_model_review_chain
from app.storage.mysql.session import session_scope


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual stage-20B chain CLI parser."""

    parser = argparse.ArgumentParser(description="Run or resume a stage-20B mock model review chain.")
    parser.add_argument("--material-pack-id", default="")
    parser.add_argument("--chain-id", default="")
    parser.add_argument("--chain-key", default=DEFAULT_CHAIN_KEY)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--simulate-step-failure", type=int, default=None)
    parser.add_argument("--max-retry-count", type=int, default=DEFAULT_MAX_RETRY_COUNT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only validation. This is the default.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow stage 20B to write chain state rows.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call only the stage-20B service, print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = ModelReviewChainRequest(
        material_pack_id=args.material_pack_id.strip(),
        chain_id=args.chain_id.strip() or None,
        chain_key=args.chain_key.strip(),
        trigger_source=args.trigger_source,
        resume=bool(args.resume),
        dry_run=not bool(args.confirm_write),
        confirm_write=bool(args.confirm_write),
        created_by="cli",
        simulate_step_failure=args.simulate_step_failure,
        max_retry_count=int(args.max_retry_count),
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = run_model_review_chain(db_session=db_session, request=request)

    for line in format_model_review_chain_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
