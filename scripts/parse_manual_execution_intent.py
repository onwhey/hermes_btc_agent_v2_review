"""CLI simulator for stage-22B manual execution intent creation.

Triggered by: a user running `python -m scripts.parse_manual_execution_intent`.
Manual execution: allowed as a safe Hermes/WeChat entry simulator.
Scheduler execution: not allowed; only `--trigger-source cli` is accepted.
Required args: `--text` plus either `--dry-run` or `--confirm-write`.
Calls: `app.manual_execution.hermes_entry.intent_service.py::create_manual_execution_intent`.
Business logic: lives in `app/manual_execution/hermes_entry`, not in this script.
Database impact: dry-run writes no rows; confirm-write writes only the 22B intent
table and never writes 22A execution rows.
Redis impact: none.
Hermes impact: only the service may call unified alerting; real submission is
controlled by config.
Formal Kline impact: this script is not allowed to modify formal Kline tables.
Repair/model/trading impact: no correction/update/delete, no model call, no
exchange account read, and no automatic trading.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.manual_execution.constants import MANUAL_TRIGGER_SOURCE_CLI
from app.manual_execution.hermes_entry.constants import (
    EXIT_PARAMETER_ERROR,
    SOURCE_CHANNEL_CLI,
    SOURCE_CHANNEL_HERMES,
    SOURCE_CHANNEL_WECHAT,
)
from app.manual_execution.hermes_entry.intent_schema import (
    InboundManualExecutionMessage,
    format_manual_execution_intent_result_lines,
)
from app.manual_execution.hermes_entry.intent_service import create_manual_execution_intent
from app.storage.mysql.session import session_scope


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the stage-22B intent creation CLI parser."""

    parser = argparse.ArgumentParser(description="Create one stage-22B manual execution confirmation intent.")
    parser.add_argument("--text", required=True)
    parser.add_argument("--source-channel", default=SOURCE_CHANNEL_CLI, choices=[SOURCE_CHANNEL_CLI, SOURCE_CHANNEL_HERMES, SOURCE_CHANNEL_WECHAT])
    parser.add_argument("--source-message-id")
    parser.add_argument("--source-user-id")
    parser.add_argument("--trigger-source", required=True, choices=[MANUAL_TRIGGER_SOURCE_CLI])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only parsing and validation.")
    mode.add_argument("--confirm-write", action="store_true", help="Write the 22B intent row only.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, call only the 22B service, and print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    message = InboundManualExecutionMessage(
        text=args.text,
        source_channel=args.source_channel,
        source_message_id=args.source_message_id,
        source_user_id=args.source_user_id,
        trigger_source=args.trigger_source,
        dry_run=not bool(args.confirm_write),
        confirm_write=bool(args.confirm_write),
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = create_manual_execution_intent(db_session=db_session, message=message)

    for line in format_manual_execution_intent_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
