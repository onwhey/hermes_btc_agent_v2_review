"""Manual CLI entry for stage-22A manual execution feedback.

Triggered by: a user running `python -m scripts.record_manual_execution`.
Manual execution: allowed for dry-run validation and explicit confirmed writes.
Scheduler execution: not allowed in stage 22A; only `--trigger-source cli` is accepted.
Required args depend on `--action`: open/add/reduce/close/take_profit/stop_loss
must follow `docs/plans/22_manual_execution_feedback_plan.md`.
Calls: `app.manual_execution.service.py::record_manual_execution`.
Business logic: lives in `app/manual_execution`, not in this script.
Database impact: dry-run writes no rows; confirm-write delegates the two stage-22A
manual feedback tables and optional alert_message rows to the service.
Redis impact: none.
Hermes impact: only the service may call unified alerting; real submission is
controlled by config.
Formal Kline impact: this script is not allowed to modify formal Kline tables.
Repair/model/trading impact: no correction/update/delete, no natural-language
write path, no model call, no exchange account read, and no automatic trading.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.manual_execution.constants import (
    ACTION_ADD_POSITION,
    ACTION_CLOSE_POSITION,
    ACTION_OPEN_POSITION,
    ACTION_REDUCE_POSITION,
    ACTION_STOP_LOSS,
    ACTION_TAKE_PROFIT,
    MANUAL_TRIGGER_SOURCE_CLI,
    EXIT_PARAMETER_ERROR,
)
from app.manual_execution.schema import ManualExecutionRequest, format_manual_execution_result_lines
from app.manual_execution.service import record_manual_execution
from app.storage.mysql.session import session_scope


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the stage-22A manual execution feedback CLI parser."""

    parser = argparse.ArgumentParser(description="Record one user-reported manual execution action.")
    parser.add_argument(
        "--action",
        required=True,
        choices=[
            ACTION_OPEN_POSITION,
            ACTION_ADD_POSITION,
            ACTION_REDUCE_POSITION,
            ACTION_CLOSE_POSITION,
            ACTION_TAKE_PROFIT,
            ACTION_STOP_LOSS,
        ],
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", required=True, choices=["long", "short"])
    parser.add_argument("--price", required=True)
    parser.add_argument("--notional-usdt")
    parser.add_argument("--margin-usdt")
    parser.add_argument("--manual-position-id")
    parser.add_argument("--advice-id", required=True)
    parser.add_argument("--reason", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--trigger-source", required=True, choices=[MANUAL_TRIGGER_SOURCE_CLI])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only validation. This is the default.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow stage 22A to write manual feedback rows.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, call only the stage-22A service, and print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = ManualExecutionRequest(
        action=args.action,
        advice_id=args.advice_id.strip(),
        symbol=args.symbol.strip(),
        side=args.side,
        price=args.price,
        notional_usdt=args.notional_usdt,
        margin_usdt=args.margin_usdt,
        manual_position_id=args.manual_position_id.strip() if args.manual_position_id else None,
        reason=args.reason.strip(),
        note=args.note.strip(),
        trigger_source=args.trigger_source,
        dry_run=not bool(args.confirm_write),
        confirm_write=bool(args.confirm_write),
        created_by="cli",
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = record_manual_execution(db_session=db_session, request=request)

    for line in format_manual_execution_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
