"""Manual CLI entry for the stage-25A unified strategy pipeline.

Triggered by: a human operator from the command line.
Scheduler use: not allowed in 25A; pass `--trigger-source cli`.
Required parameters: symbol, base interval, higher interval, and exactly one of
`--dry-run` or `--confirm-write`. `--kline-slot-utc` is optional; if omitted,
the app service may infer the latest formal 4h Kline slot.

This script only parses CLI arguments, loads config, opens a DB session, and
calls `app/strategy_pipeline/service.py::run_strategy_pipeline`.
It does not contain strategy logic, request Binance, write Redis directly, send
Hermes directly, modify Kline tables, call large models directly, read accounts,
or perform trading.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from app.core.config import get_settings
from app.core.time_utils import ensure_utc_aware
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE
from app.storage.mysql.session import session_scope
from app.strategy_pipeline.service import run_strategy_pipeline
from app.strategy_pipeline.types import (
    StrategyPipelineRequest,
    format_strategy_pipeline_result_lines,
)


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run the 25A app-layer pipeline service."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    request = StrategyPipelineRequest(
        symbol=str(args.symbol).strip().upper(),
        base_interval=str(args.base_interval).strip(),
        higher_interval=str(args.higher_interval).strip(),
        kline_slot_utc=_parse_optional_datetime(args.kline_slot_utc),
        trigger_source=args.trigger_source,
        dry_run=bool(args.dry_run),
        confirm_write=bool(args.confirm_write),
        use_real_model=bool(args.use_real_model),
        confirm_real_model_cost=bool(args.confirm_real_model_cost),
        send_real_hermes=bool(args.send_real_hermes),
        created_by="cli",
    )
    with session_scope(settings=settings, commit_on_success=False) as db_session:
        result = run_strategy_pipeline(db_session=db_session, request=request)
    for line in format_strategy_pipeline_result_lines(result):
        print(line)
    return result.exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the manual 25A strategy pipeline.")
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--base-interval", default=KLINE_4H_INTERVAL_VALUE)
    parser.add_argument("--higher-interval", default=KLINE_1D_INTERVAL_VALUE)
    parser.add_argument(
        "--kline-slot-utc",
        default=None,
        help="Optional base Kline open time, for example 2026-05-30T04:00:00Z.",
    )
    parser.add_argument("--trigger-source", choices=("cli",), default="cli")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--use-real-model", action="store_true")
    parser.add_argument("--confirm-real-model-cost", action="store_true")
    parser.add_argument("--send-real-hermes", action="store_true")
    return parser


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    return ensure_utc_aware(parsed)


if __name__ == "__main__":  # pragma: no cover - exercised through CLI use.
    sys.exit(main())

