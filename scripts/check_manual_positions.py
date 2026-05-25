"""Manual CLI entry for checking stage-22A open manual positions.

Triggered by: a user running `python -m scripts.check_manual_positions`.
Manual execution: allowed.
Scheduler execution: not allowed in stage 22A; only `--trigger-source cli` is accepted.
Required args: `--trigger-source cli`; `--symbol` and `--status` are optional filters.
Calls: `app.manual_execution.service.py::list_manual_positions`.
Business logic: lives in `app/manual_execution`, not in this script.
Database impact: read-only query of stage-22A manual position summaries.
Redis impact: none.
Hermes impact: none.
Formal Kline impact: this script is not allowed to modify formal Kline tables.
Repair/model/trading impact: no correction/update/delete, no model call, no
exchange account read, and no automatic trading.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from app.core.config import get_settings
from app.manual_execution.constants import EXIT_PARAMETER_ERROR, MANUAL_TRIGGER_SOURCE_CLI
from app.manual_execution.schema import ManualPositionListRequest, format_manual_position_list_lines
from app.manual_execution.service import list_manual_positions
from app.storage.mysql.session import session_scope


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the stage-22A manual position query CLI parser."""

    parser = argparse.ArgumentParser(description="List stage-22A manual positions.")
    parser.add_argument("--symbol")
    parser.add_argument("--status", default="open", choices=["open", "closed"])
    parser.add_argument("--trigger-source", required=True, choices=[MANUAL_TRIGGER_SOURCE_CLI])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, call only the stage-22A query service, and print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    request = ManualPositionListRequest(
        symbol=args.symbol.strip() if args.symbol else None,
        status=args.status,
        trigger_source=args.trigger_source,
    )
    settings = get_settings()
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = list_manual_positions(db_session=db_session, request=request)

    for line in format_manual_position_list_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
