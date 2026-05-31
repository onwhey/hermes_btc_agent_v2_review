"""Manual CLI for 27B weak model output quality checks.

Triggered by: a user running `python -m scripts.check_weak_model_output_quality`.
Manual execution: allowed for read-only inspection and explicit confirmed writes.
Scheduler execution: not allowed in 27B; there is no scheduler job for this
script.
Required args: either `--weak-model-run-id` for exact check, or symbol/base/
higher/limit for recent persisted 27A runs.
Calls: `app/weak_models/output_quality_service.py::check_weak_model_output_quality`.
Business logic: lives in `app/weak_models`, not in this script.
Database impact: default dry-run writes nothing; `--confirm-write` writes only
`weak_model_quality_check` through the service. It never modifies
`weak_model_run`, `weak_model_result`, `weak_model_aggregation`, formal Kline
tables, strategy tables, material-pack tables, model-review tables, advice
tables, or pipeline scheduling.
Redis impact: none.
Hermes impact: none.
Formal Kline impact: this script is not allowed to modify or repair Kline data.
External/trading impact: no Binance REST request, no large-model call, no private
trading-state read, no order generation, and no automatic trading.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Sequence

from app.core.config import AppSettings, get_settings
from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE
from app.storage.mysql.session import session_scope
from app.weak_models.output_quality_service import (
    WeakModelOutputQualityService,
    create_default_weak_model_output_quality_service,
)
from app.weak_models.output_quality_types import (
    EXIT_PARAMETER_OR_DATABASE_ERROR,
    NON_TRADING_STATEMENT,
    WeakModelQualityCheckReport,
    WeakModelQualityCheckRequest,
    format_weak_model_quality_report_lines,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the 27B weak model output quality CLI parser."""

    parser = argparse.ArgumentParser(description="检查 27B 弱模型输出质量，不重新运行弱模型。")
    parser.add_argument("--weak-model-run-id", default="")
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--base-interval", default=KLINE_4H_INTERVAL_VALUE, choices=[KLINE_4H_INTERVAL_VALUE])
    parser.add_argument("--higher-interval", default=KLINE_1D_INTERVAL_VALUE, choices=[KLINE_1D_INTERVAL_VALUE])
    parser.add_argument("--limit", type=int, default=10)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只读检查，不写库。这是默认行为。")
    mode.add_argument("--confirm-write", action="store_true", help="写入 weak_model_quality_check 审计结果。")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    service: WeakModelOutputQualityService | Any | None = None,
    settings: AppSettings | None = None,
    session_scope_factory: Callable[..., AbstractContextManager[Any]] | None = None,
) -> int:
    """Parse args, call only the 27B service, and print compact Chinese output."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_OR_DATABASE_ERROR

    request = WeakModelQualityCheckRequest(
        weak_model_run_id=_blank_to_none(args.weak_model_run_id),
        symbol=str(args.symbol).strip().upper(),
        base_interval=str(args.base_interval).strip(),
        higher_interval=str(args.higher_interval).strip(),
        limit=int(args.limit),
        dry_run=bool(args.dry_run or not args.confirm_write),
        confirm_write=bool(args.confirm_write),
    )
    parameter_error = _validate_request(request)
    if parameter_error:
        print(f"参数错误：{parameter_error}")
        print(NON_TRADING_STATEMENT)
        return EXIT_PARAMETER_OR_DATABASE_ERROR

    active_settings = settings or get_settings()
    active_service = service or create_default_weak_model_output_quality_service()
    scope_factory = session_scope_factory or session_scope
    try:
        with scope_factory(settings=active_settings, commit_on_success=False) as db_session:
            report = _run_quality_service(db_session=db_session, request=request, service=active_service)
    except Exception as exc:  # noqa: BLE001 - CLI maps database/query failures to exit code 2.
        print(f"数据库查询失败或质量检查失败：{exc}")
        print(NON_TRADING_STATEMENT)
        return EXIT_PARAMETER_OR_DATABASE_ERROR

    for line in format_weak_model_quality_report_lines(report):
        print(line)
    return report.exit_code


def _run_quality_service(
    *,
    db_session: Any,
    request: WeakModelQualityCheckRequest,
    service: WeakModelOutputQualityService | Any,
) -> WeakModelQualityCheckReport:
    return service.check_weak_model_output_quality(db_session, request=request)


def _validate_request(request: WeakModelQualityCheckRequest) -> str | None:
    if not request.weak_model_run_id and not request.symbol:
        return "symbol 不能为空"
    if request.base_interval != KLINE_4H_INTERVAL_VALUE:
        return "27B 第一版只支持 base_interval=4h"
    if request.higher_interval != KLINE_1D_INTERVAL_VALUE:
        return "27B 第一版只支持 higher_interval=1d"
    if request.limit <= 0:
        return "limit 必须大于 0"
    if request.limit > 200:
        return "limit 不能超过 200，避免一次性输出过多结果"
    return None


def _blank_to_none(value: str | None) -> str | None:
    stripped = str(value or "").strip()
    return stripped or None


if __name__ == "__main__":  # pragma: no cover - exercised by CLI use.
    sys.exit(main())
