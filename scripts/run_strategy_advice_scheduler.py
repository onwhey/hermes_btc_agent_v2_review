"""CLI entry for stage-21C strategy advice scheduler orchestration.

Triggered by: user manually from a shell for validation or recovery.
Scheduler use: not allowed through this script; scheduler calls
`app/scheduler/jobs/strategy_advice_scheduler_job.py` directly with
`trigger_source=scheduler`.

Required mode: exactly one of `--dry-run` or `--confirm-write`.
Core service: `app/strategy_advice/scheduler_service.py::run_strategy_advice_scheduler`.
Database: dry-run reads only; confirm-write may write lifecycle review/event,
alert_message via 21B, and 21C scheduler event log. Redis: confirm-write may
use a short 21C lock. Hermes: never sent by a CLI flag; real send remains
controlled by `STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED`. Formal Klines:
never modified. Auto repair/trading: never allowed.
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.storage.mysql import session as mysql_session
from app.strategy_advice.scheduler_schema import (
    StrategyAdviceSchedulerRequest,
    format_strategy_advice_scheduler_result_lines,
)
from app.strategy_advice.scheduler_service import run_strategy_advice_scheduler


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and delegate all business logic to the 21C service."""

    parser = argparse.ArgumentParser(description="Run stage-21C strategy advice scheduler orchestration.")
    parser.add_argument("--review-aggregation-run-id", default="", help="Optional MRAG id to process or recover.")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol for scan mode, default BTCUSDT.")
    parser.add_argument("--base-interval", default="4h", help="Base interval for scan mode, default 4h.")
    parser.add_argument("--higher-interval", default="1d", help="Higher interval for scan mode, default 1d.")
    parser.add_argument("--trigger-source", default=TRIGGER_SOURCE_CLI, choices=[TRIGGER_SOURCE_CLI])
    parser.add_argument("--limit", type=int, default=20, help="Maximum MRAG/review rows to inspect.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview without writes or Hermes sends.")
    mode.add_argument("--confirm-write", action="store_true", help="Persist 21C/21A/21B rows as allowed by env.")
    args = parser.parse_args(argv)

    settings = get_settings()
    request = StrategyAdviceSchedulerRequest(
        review_aggregation_run_id=args.review_aggregation_run_id.strip() or None,
        symbol=args.symbol,
        base_interval=args.base_interval,
        higher_interval=args.higher_interval,
        trigger_source=args.trigger_source,
        dry_run=bool(args.dry_run),
        confirm_write=bool(args.confirm_write),
        created_by="cli_strategy_advice_scheduler",
        limit=args.limit,
    )
    with mysql_session.session_scope(settings=settings, commit_on_success=False) as db_session:
        result = run_strategy_advice_scheduler(db_session=db_session, request=request)
    for line in format_strategy_advice_scheduler_result_lines(result):
        print(line)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover - manual CLI entry.
    sys.exit(main())
