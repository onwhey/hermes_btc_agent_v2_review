"""Manual CLI entry for 27A weak model / factor layer.

Triggered by: a user running `python -m scripts.run_weak_models`.
Manual execution: allowed for dry-run validation and explicit confirmed writes.
Scheduler execution: not allowed in 27A; this script accepts only
`--trigger-source cli`.
Required args: `--strategy-signal-run-id`. Optional args include
`--pipeline-run-id`, symbol/interval guards, `--kline-slot-utc`, `--config-dir`,
and exactly one of `--dry-run` or `--confirm-write`.
Calls: `app/weak_models/service.py::run_weak_models_for_strategy_signal`.
Business logic: lives in `app/weak_models`, not in this script.
Database impact: dry-run writes nothing; confirm-write writes or updates only
`weak_model_run`, `weak_model_result`, and `weak_model_aggregation` through the
service. It does not modify formal Kline tables, strategy tables, material-pack
tables, model-review tables, advice tables, or pipeline scheduling.
Redis impact: none.
Hermes impact: none.
Formal Kline impact: this script is not allowed to modify or repair Kline data.
External/trading impact: no Binance REST request, no large-model call, no private
trading-state read, no order generation, and no automatic trading.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from app.core.config import AppSettings, get_settings
from app.core.time_utils import ensure_utc_aware
from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)
from app.storage.mysql.session import session_scope
from app.weak_models.registry import WeakModelRegistry
from app.weak_models.service import WeakModelService, create_default_weak_model_service
from app.weak_models.types import (
    EXIT_PARAMETER_ERROR,
    NON_TRADING_STATEMENT,
    WeakModelRunRequest,
    format_weak_model_run_result_lines,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the 27A weak model CLI parser."""

    parser = argparse.ArgumentParser(description="Run 27A weak models from one stage-16 strategy signal run.")
    parser.add_argument("--strategy-signal-run-id", required=True)
    parser.add_argument("--pipeline-run-id", default="")
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--base-interval", default=KLINE_4H_INTERVAL_VALUE, choices=[KLINE_4H_INTERVAL_VALUE])
    parser.add_argument("--higher-interval", default=KLINE_1D_INTERVAL_VALUE, choices=[KLINE_1D_INTERVAL_VALUE])
    parser.add_argument("--kline-slot-utc", default="")
    parser.add_argument("--config-dir", default="")
    parser.add_argument("--trigger-source", default=TRIGGER_SOURCE_CLI, choices=[TRIGGER_SOURCE_CLI])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only validation and calculation. This is the default.")
    mode.add_argument("--confirm-write", action="store_true", help="Allow 27A to write weak model audit tables.")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    service: WeakModelService | Any | None = None,
    settings: AppSettings | None = None,
    session_scope_factory: Callable[..., AbstractContextManager[Any]] | None = None,
) -> int:
    """Parse args, call only the 27A service, print compact output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    try:
        kline_slot_utc = _parse_optional_utc(args.kline_slot_utc)
    except ValueError as exc:
        print(f"参数错误：--kline-slot-utc 必须是 ISO datetime，例如 2026-05-31T04:00:00Z；{exc}")
        print(NON_TRADING_STATEMENT)
        return EXIT_PARAMETER_ERROR

    strategy_signal_run_id = str(args.strategy_signal_run_id).strip()
    if not strategy_signal_run_id:
        print("参数错误：--strategy-signal-run-id 不能为空")
        print(NON_TRADING_STATEMENT)
        return EXIT_PARAMETER_ERROR

    active_settings = settings or get_settings()
    active_service = service or _create_service_from_args(args)
    request = WeakModelRunRequest(
        strategy_signal_run_id=strategy_signal_run_id,
        pipeline_run_id=_blank_to_none(args.pipeline_run_id),
        symbol=str(args.symbol).strip().upper(),
        base_interval=str(args.base_interval).strip(),
        higher_interval=str(args.higher_interval).strip(),
        kline_slot_utc=kline_slot_utc,
        trigger_source=str(args.trigger_source).strip(),
        dry_run=bool(args.dry_run or not args.confirm_write),
        confirm_write=bool(args.confirm_write),
        created_by="cli",
    )

    scope_factory = session_scope_factory or session_scope
    with scope_factory(settings=active_settings, commit_on_success=False) as db_session:
        result = active_service.run_weak_models_for_strategy_signal(db_session, request)

    for line in format_weak_model_run_result_lines(result):
        print(line)
    return result.exit_code


def _create_service_from_args(args: Any) -> WeakModelService:
    config_dir = Path(args.config_dir).resolve() if str(args.config_dir or "").strip() else None
    if config_dir is None:
        return create_default_weak_model_service()
    return WeakModelService(registry=WeakModelRegistry(config_dir=config_dir))


def _parse_optional_utc(value: str | None) -> datetime | None:
    stripped = str(value or "").strip()
    if not stripped:
        return None
    normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
    return ensure_utc_aware(datetime.fromisoformat(normalized))


def _blank_to_none(value: str | None) -> str | None:
    stripped = str(value or "").strip()
    return stripped or None


if __name__ == "__main__":
    raise SystemExit(main())
