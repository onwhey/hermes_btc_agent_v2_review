"""Read-only CLI for 26A strategy pipeline observability.

触发方式：用户手动执行
`python -m scripts.check_strategy_pipeline_status --symbol BTCUSDT --base-interval 4h --higher-interval 1d --limit 5`。
是否允许用户手动执行：允许。
是否允许 scheduler 调用：不允许；本脚本没有 scheduler job，也不得由 scheduler 配置引用。
必须参数：无，默认 `symbol=BTCUSDT`、`base_interval=4h`、`higher_interval=1d`、`limit=5`。
调用 service：`app/strategy_observability/service.py::check_strategy_pipeline_status`。
不负责业务逻辑：不查询细节 SQL、不调用 25 pipeline、不修改 25 调度、不修改策略算法。
数据库影响：只读 MySQL，不写 `strategy_pipeline_event_log` 或其他业务表。
Redis 影响：不读写 Redis。
Hermes 影响：不发送 Hermes，不写 `alert_message`。
正式 K线影响：只读 `market_kline_4h` slot，不修改正式 K线表。
数据修复与交易边界：不自动修复，不人工改数，不调用真实模型，不读取账户，
不生成订单，不自动交易。
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
from app.strategy_observability.service import (
    StrategyPipelineObservabilityService,
    check_strategy_pipeline_status,
)
from app.strategy_observability.types import (
    EXIT_PARAMETER_OR_DATABASE_ERROR,
    NON_TRADING_STATEMENT,
    StrategyPipelineStatusReport,
    StrategyPipelineStatusRequest,
    format_strategy_pipeline_status_report_lines,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the 26A read-only CLI parser."""

    parser = argparse.ArgumentParser(description="只读检查最近 N 根 4h K线对应的策略 pipeline 状态。")
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--base-interval", default=KLINE_4H_INTERVAL_VALUE)
    parser.add_argument("--higher-interval", default=KLINE_1D_INTERVAL_VALUE)
    parser.add_argument("--limit", type=int, default=5)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    service: StrategyPipelineObservabilityService | Any | None = None,
    settings: AppSettings | None = None,
    session_scope_factory: Callable[..., AbstractContextManager[Any]] | None = None,
) -> int:
    """Parse CLI arguments, call the app service, and print a Chinese report.

    参数：`argv` 是 CLI 参数；`service`、`settings` 和 `session_scope_factory`
    供测试注入。
    返回值：0 表示全部 healthy/expected_blocked；1 表示存在异常观测状态；
    2 表示参数错误或数据库查询失败。
    失败场景：参数非法直接返回 2；数据库异常返回 2。
    外部服务：不访问。
    数据影响：只读 MySQL；不写 Redis、不发送 Hermes、不调用模型、不交易。
    """

    parser = build_arg_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_OR_DATABASE_ERROR

    request = StrategyPipelineStatusRequest(
        symbol=str(args.symbol).strip().upper(),
        base_interval=str(args.base_interval).strip(),
        higher_interval=str(args.higher_interval).strip(),
        limit=int(args.limit),
    )
    parameter_error = _validate_request(request)
    if parameter_error:
        print(f"参数错误：{parameter_error}")
        print(NON_TRADING_STATEMENT)
        return EXIT_PARAMETER_OR_DATABASE_ERROR

    active_settings = settings or get_settings()
    scope_factory = session_scope_factory or session_scope
    try:
        with scope_factory(settings=active_settings, commit_on_success=False) as db_session:
            report = _run_service(db_session=db_session, request=request, service=service)
    except Exception as exc:  # noqa: BLE001 - CLI must map DB/query failures to exit code 2.
        print(f"数据库查询失败或观测失败：{exc}")
        print(NON_TRADING_STATEMENT)
        return EXIT_PARAMETER_OR_DATABASE_ERROR

    _print_report(report)
    return report.exit_code


def _run_service(
    *,
    db_session: Any,
    request: StrategyPipelineStatusRequest,
    service: StrategyPipelineObservabilityService | Any | None,
) -> StrategyPipelineStatusReport:
    if service is not None and hasattr(service, "check_strategy_pipeline_status"):
        return service.check_strategy_pipeline_status(db_session, request=request)
    return check_strategy_pipeline_status(db_session=db_session, request=request, service=service)


def _validate_request(request: StrategyPipelineStatusRequest) -> str | None:
    if not request.symbol:
        return "symbol 不能为空"
    if request.base_interval != KLINE_4H_INTERVAL_VALUE:
        return "26A 第一版只支持 base_interval=4h"
    if not request.higher_interval:
        return "higher_interval 不能为空"
    if request.limit <= 0:
        return "limit 必须大于 0"
    if request.limit > 100:
        return "limit 不能超过 100，避免一次性输出过多观测结果"
    return None


def _print_report(report: StrategyPipelineStatusReport) -> None:
    for line in format_strategy_pipeline_status_report_lines(report):
        print(line)


if __name__ == "__main__":  # pragma: no cover - exercised by CLI use.
    sys.exit(main())
