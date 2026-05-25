"""CLI simulator for confirming or cancelling a stage-22B manual execution intent.

Triggered by: a user running `python -m scripts.confirm_manual_execution_intent`.
Manual execution: allowed as a safe Hermes/WeChat confirmation simulator.
Scheduler execution: not allowed; only `--trigger-source cli` is accepted.
Required args: `--intent-id`, `--action`, and either `--dry-run` or
`--confirm-write`.
Calls: `app.manual_execution.hermes_entry.intent_service.py::confirm_manual_execution_intent`
or `cancel_manual_execution_intent`.
Business logic: lives in `app/manual_execution/hermes_entry`, not in this script.
Database impact: confirm-write may update the 22B intent row; confirmation may
delegate to 22A service to write manual execution rows.
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
    INBOUND_COMMAND_CANCEL,
    INBOUND_COMMAND_CONFIRM,
    SOURCE_CHANNEL_CLI,
    SOURCE_CHANNEL_HERMES,
    SOURCE_CHANNEL_WECHAT,
)
from app.manual_execution.hermes_entry.intent_schema import (
    IntentActionRequest,
    format_manual_execution_intent_result_lines,
)
from app.manual_execution.hermes_entry.intent_service import (
    cancel_manual_execution_intent,
    confirm_manual_execution_intent,
)
from app.storage.mysql.session import session_scope


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the stage-22B intent confirm/cancel CLI parser."""

    parser = argparse.ArgumentParser(description="Confirm or cancel one stage-22B manual execution intent.")
    parser.add_argument("--intent-id", required=True)
    parser.add_argument("--action", required=True, choices=[INBOUND_COMMAND_CONFIRM, INBOUND_COMMAND_CANCEL])
    parser.add_argument("--source-channel", default=SOURCE_CHANNEL_CLI, choices=[SOURCE_CHANNEL_CLI, SOURCE_CHANNEL_HERMES, SOURCE_CHANNEL_WECHAT])
    parser.add_argument("--source-message-id")
    parser.add_argument("--source-user-id")
    parser.add_argument("--trigger-source", required=True, choices=[MANUAL_TRIGGER_SOURCE_CLI])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only validation.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow the confirm/cancel write.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, call only the 22B service, and print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = IntentActionRequest(
        intent_id=args.intent_id.strip(),
        source_channel=args.source_channel,
        source_message_id=args.source_message_id,
        source_user_id=args.source_user_id,
        dry_run=not bool(args.confirm_write),
        confirm_write=bool(args.confirm_write),
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        if args.action == INBOUND_COMMAND_CONFIRM:
            result = confirm_manual_execution_intent(db_session=db_session, request=request)
        else:
            result = cancel_manual_execution_intent(db_session=db_session, request=request)

    for line in format_manual_execution_intent_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
