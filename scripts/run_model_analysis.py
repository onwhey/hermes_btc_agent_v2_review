"""Manual CLI entry for stage-19 model analysis review gate.

Triggered by: a user running `python -m scripts.run_model_analysis`.
Manual execution: allowed for dry-run validation and explicit confirmed writes.
Scheduler execution: not allowed in stage 19A.
Required args: `--material-pack-id` and `--trigger-source cli`.
Real writes require `--confirm-write`; otherwise dry-run is the default.
Calls: `app/model_analysis/service.py::run_model_analysis`.
Business logic: lives in `app/model_analysis`, not in this script.
Database impact: dry-run writes no `model_analysis_run` and no
`model_analysis_result`; confirm-write delegates persistence to stage 19A and
is blocked unless `MODEL_REVIEW_ENABLED=true`.
Redis impact: none.
Hermes impact: delegated to stage 19A only during confirmed writes and only
when `.env` enables it.
Formal Kline impact: this script is not allowed to modify `market_kline_4h` or
`market_kline_1d`.
Data repair/model/trading impact: no automatic repair, no final trading
advice, no private trading state reads, and no trading. Real model calls are
allowed only in stage 19B when the user explicitly passes the real-model and
cost-confirmation flags.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_analysis.service import run_model_analysis
from app.model_analysis.types import (
    EXIT_PARAMETER_ERROR,
    ModelAnalysisRequest,
    format_model_analysis_result_lines,
)
from app.storage.mysql.session import session_scope

def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual stage-19 model analysis CLI parser."""

    parser = argparse.ArgumentParser(description="Run stage-19 model analysis review gate.")
    parser.add_argument("--material-pack-id", required=True)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only validation. This is the default.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow stage 19A to write review rows.")
    parser.add_argument(
        "--use-real-model",
        action="store_true",
        help="Enable explicit stage-19B real provider flow.",
    )
    parser.add_argument("--model-key", default="", help="Required with --use-real-model.")
    parser.add_argument(
        "--confirm-real-model-cost",
        action="store_true",
        help="Required before any real provider call.",
    )
    parser.add_argument("--capture-raw-response", action="store_true", help="Store raw response as artifact.")
    parser.add_argument("--capture-raw-request", action="store_true", help="Store raw request as artifact when supported.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call only the stage-19 service, print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = ModelAnalysisRequest(
        material_pack_id=args.material_pack_id.strip(),
        trigger_source=args.trigger_source,
        dry_run=not bool(args.confirm_write),
        confirm_write=bool(args.confirm_write),
        created_by="cli",
        use_real_model=bool(args.use_real_model),
        model_key=args.model_key.strip() or None,
        confirm_real_model_cost=bool(args.confirm_real_model_cost),
        capture_raw_request=bool(args.capture_raw_request),
        capture_raw_response=bool(args.capture_raw_response),
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = run_model_analysis(db_session=db_session, request=request)

    for line in format_model_analysis_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
