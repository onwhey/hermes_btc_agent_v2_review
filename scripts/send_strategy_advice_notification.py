"""Manual CLI entry for stage-21B strategy advice Hermes notification.

Triggered by: a user running `python -m scripts.send_strategy_advice_notification`.
Manual execution: allowed for dry-run preview, confirmed alert preparation, and
explicit real Hermes submission.
Scheduler execution: not allowed in stage 21B.
Required args: `--review-id` and `--trigger-source cli`.
Real database writes require `--confirm-write`; real Hermes submission also
requires `--send-real-alert`.
Calls: `app/strategy_advice/notification_sender.py::send_strategy_advice_notification`.
Business logic: lives in `app/strategy_advice`, not in this script.
Database impact: dry-run writes no rows; confirm-write can write alert_message
and strategy_advice_event rows through the service.
Redis impact: none.
Hermes impact: only `--confirm-write --send-real-alert` may call Hermes through
the existing alerting client and configuration.
Formal Kline impact: this script is not allowed to modify Kline tables.
Data repair/model/trading impact: no automatic repair, no stage-19 call, no
model provider call, no scheduler hook, no private trading state reads, and no
trading execution.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.storage.mysql.session import session_scope
from app.strategy_advice.notification_schema import (
    EXIT_PARAMETER_ERROR,
    StrategyAdviceNotificationRequest,
    format_strategy_advice_notification_result_lines,
)
from app.strategy_advice.notification_sender import send_strategy_advice_notification


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the manual stage-21B notification CLI parser."""

    parser = argparse.ArgumentParser(description="Send or preview a stage-21B strategy advice notification.")
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--trigger-source", required=True, choices=[TRIGGER_SOURCE_CLI])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Render preview only. This is the default.")
    mode.add_argument("--confirm-write", action="store_true", help="Write alert/event rows.")
    parser.add_argument(
        "--send-real-alert",
        action="store_true",
        help="With --confirm-write, explicitly allow real Hermes submission.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call only the stage-21B service, print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    if args.send_real_alert and not args.confirm_write:
        print("--send-real-alert requires --confirm-write")
        return EXIT_PARAMETER_ERROR

    request = StrategyAdviceNotificationRequest(
        review_id=args.review_id.strip(),
        trigger_source=args.trigger_source,
        dry_run=not bool(args.confirm_write),
        confirm_write=bool(args.confirm_write),
        send_real_alert=bool(args.send_real_alert),
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = send_strategy_advice_notification(db_session=db_session, request=request)

    for line in format_strategy_advice_notification_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
