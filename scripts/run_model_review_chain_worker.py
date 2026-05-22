"""Manual CLI entry for stage-20C model-review chain worker.

Triggered by: a user running `python -m scripts.run_model_review_chain_worker`.
Manual execution: allowed for dry-run validation and explicit confirmed writes.
Scheduler execution: not through this script; scheduler calls the thin job in
`app/scheduler/jobs/model_review_chain_worker_job.py`.
Required args: `--trigger-source cli`; optional `--material-pack-id` or
`--chain-id` targets a specific object, otherwise the worker scans one pending
chain.
Real writes require `--confirm-write`; otherwise dry-run is the default. If a
manual CLI tick may reach a real model call, `--confirm-real-model-cost` is
also required; scheduler jobs do not use this script and remain controlled by
config, budget, whitelist, locks, and state machine gates.
Calls: `app/model_review_chain/worker.py::run_model_review_chain_worker`.
Business logic: lives in `app/model_review_chain`, not in this script.
Database impact: dry-run writes no rows; confirm-write delegates compact 20B
chain/step state and stage-19 attempt rows to services.
Redis impact: confirm-write may use Redis locks for worker concurrency.
Hermes impact: none in this script.
Formal Kline impact: this script is not allowed to modify formal Kline tables.
Data repair/model/trading impact: no automatic repair, no final trading advice,
no trading signal, no private trading state reads, and no trading. Any real
model call from this manual CLI entry also requires the explicit cost
confirmation flag.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_review_chain.schema import DEFAULT_MAX_RETRY_COUNT, DEFAULT_SCHEDULER_CHAIN_KEY, EXIT_PARAMETER_ERROR
from app.model_review_chain.worker import run_model_review_chain_worker
from app.model_review_chain.worker_schema import (
    ModelReviewChainWorkerRequest,
    format_model_review_chain_worker_result_lines,
)
from app.storage.mysql.session import session_scope


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual stage-20C worker CLI parser."""

    parser = argparse.ArgumentParser(description="Run one stage-20C model-review worker tick.")
    parser.add_argument("--material-pack-id", default="")
    parser.add_argument("--chain-id", default="")
    parser.add_argument("--chain-key", default=DEFAULT_SCHEDULER_CHAIN_KEY)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-retry-count", type=int, default=DEFAULT_MAX_RETRY_COUNT)
    parser.add_argument(
        "--confirm-real-model-cost",
        action="store_true",
        help="Required for CLI-triggered worker ticks that may call a real model.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only validation. This is the default.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow 20C to write and advance eligible steps.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call only the stage-20C worker service, print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = ModelReviewChainWorkerRequest(
        material_pack_id=args.material_pack_id.strip(),
        chain_id=args.chain_id.strip() or None,
        chain_key=args.chain_key.strip(),
        trigger_source=args.trigger_source,
        dry_run=not bool(args.confirm_write),
        confirm_write=bool(args.confirm_write),
        confirm_real_model_cost=bool(args.confirm_real_model_cost),
        created_by="cli",
        limit=int(args.limit),
        max_retry_count=int(args.max_retry_count),
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = run_model_review_chain_worker(db_session=db_session, request=request)

    for line in format_model_review_chain_worker_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
