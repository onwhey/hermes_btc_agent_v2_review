"""CLI for 26C-A strategy pipeline observation index building.

触发方式：用户手动执行，例如：
`python -m scripts.build_strategy_pipeline_observations --symbol BTCUSDT --base-interval 4h --higher-interval 1d --limit 10 --confirm-write`

是否允许用户手动执行：允许。
是否允许 scheduler 调用：不允许；26C-A 第一版不接入 scheduler，也不允许
`--trigger-source scheduler`。
必须参数：无；默认 `symbol=BTCUSDT`、`base_interval=4h`、`higher_interval=1d`。
可选参数：`--limit`、`--kline-slot-utc`、`--dry-run`、`--confirm-write`、
`--refresh-existing`、`--trigger-source cli`。
调用 service：`app/strategy_pipeline_observation/service.py::build_strategy_pipeline_observations`。
不负责业务逻辑：不做 canonical 选择，不写 SQL，不运行 16/23F/26B/18/20/21，
不做复盘分析。
数据库影响：dry-run 不写库；confirm-write 由 service 写
`strategy_pipeline_observation`，不修改正式 K线表、pipeline 表、策略表、模型表或建议表。
Redis 影响：不读写 Redis。
Hermes 影响：不发送 Hermes，不写 `alert_message`。
正式 K线影响：只读 `market_kline_4h` slot，不修改正式 K线表。
数据修复与交易边界：不自动修复，不人工改数，不请求 Binance REST，不调用真实模型，
不读取账户，不读取仓位，不生成订单，不自动交易。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
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
from app.strategy_pipeline_observation.service import (
    StrategyPipelineObservationService,
    build_strategy_pipeline_observations,
)
from app.strategy_pipeline_observation.types import (
    EXIT_PARAMETER_OR_DATABASE_ERROR,
    NON_TRADING_STATEMENT,
    StrategyPipelineObservationBuildReport,
    StrategyPipelineObservationBuildRequest,
    format_strategy_pipeline_observation_report_lines,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the 26C-A CLI parser."""

    parser = argparse.ArgumentParser(description="构建 26C-A 策略链路观察索引。")
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--base-interval", default=KLINE_4H_INTERVAL_VALUE)
    parser.add_argument("--higher-interval", default=KLINE_1D_INTERVAL_VALUE)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--kline-slot-utc", default="")
    parser.add_argument("--dry-run", action="store_true", help="只计算并输出，不写 observation 表。")
    parser.add_argument("--confirm-write", action="store_true", help="显式确认写入 observation 表。")
    parser.add_argument("--refresh-existing", action="store_true", help="重复执行时刷新已有 observation。")
    parser.add_argument("--trigger-source", default=TRIGGER_SOURCE_CLI)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    service: StrategyPipelineObservationService | Any | None = None,
    settings: AppSettings | None = None,
    session_scope_factory: Callable[..., AbstractContextManager[Any]] | None = None,
) -> int:
    """Parse CLI args, call the app service, and print a Chinese report.

    参数：`argv` 是 CLI 参数；`service`、`settings`、`session_scope_factory`
    供测试注入。
    返回值：0 表示构建或 dry-run 成功；2 表示参数错误或数据库查询/写入失败。
    失败场景：参数非法直接返回 2；数据库异常返回 2。
    外部服务：不访问。
    数据影响：dry-run 只读；confirm-write 只写 observation 索引，不写 Redis、
    不发送 Hermes、不调用模型、不交易。
    """

    parser = build_arg_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_OR_DATABASE_ERROR

    try:
        kline_slot_utc = _parse_optional_utc(str(args.kline_slot_utc or "").strip())
    except ValueError as exc:
        print(f"参数错误：kline-slot-utc 必须是 ISO datetime，例如 2026-05-31T04:00:00Z；{exc}")
        print(NON_TRADING_STATEMENT)
        return EXIT_PARAMETER_OR_DATABASE_ERROR

    request = StrategyPipelineObservationBuildRequest(
        symbol=str(args.symbol).strip().upper(),
        base_interval=str(args.base_interval).strip(),
        higher_interval=str(args.higher_interval).strip(),
        limit=int(args.limit),
        kline_slot_utc=kline_slot_utc,
        dry_run=bool(args.dry_run or not args.confirm_write),
        confirm_write=bool(args.confirm_write),
        refresh_existing=bool(args.refresh_existing),
        trigger_source=str(args.trigger_source).strip(),
    )
    parameter_error = _validate_request(request)
    if parameter_error:
        print(f"参数错误：{parameter_error}")
        print(NON_TRADING_STATEMENT)
        return EXIT_PARAMETER_OR_DATABASE_ERROR

    active_settings = settings or get_settings()
    active_service = service or StrategyPipelineObservationService(settings=active_settings)
    scope_factory = session_scope_factory or session_scope
    try:
        with scope_factory(settings=active_settings, commit_on_success=False) as db_session:
            report = _run_service(db_session=db_session, request=request, service=active_service)
    except Exception as exc:  # noqa: BLE001 - CLI maps DB/query/write failures to exit code 2.
        print(f"数据库查询或 observation 构建失败：{exc}")
        print(NON_TRADING_STATEMENT)
        return EXIT_PARAMETER_OR_DATABASE_ERROR

    _print_report(report)
    return report.exit_code


def _run_service(
    *,
    db_session: Any,
    request: StrategyPipelineObservationBuildRequest,
    service: StrategyPipelineObservationService | Any,
) -> StrategyPipelineObservationBuildReport:
    if hasattr(service, "build_strategy_pipeline_observations"):
        return service.build_strategy_pipeline_observations(db_session, request=request)
    return build_strategy_pipeline_observations(db_session, request=request, service=service)


def _validate_request(request: StrategyPipelineObservationBuildRequest) -> str | None:
    if not request.symbol:
        return "symbol 不能为空"
    if request.base_interval != KLINE_4H_INTERVAL_VALUE:
        return "26C-A 第一版只支持 base_interval=4h"
    if request.higher_interval != KLINE_1D_INTERVAL_VALUE:
        return "26C-A 第一版只支持 higher_interval=1d"
    if request.limit <= 0:
        return "limit 必须大于 0"
    if request.limit > 200:
        return "limit 不能超过 200，避免一次性构建过多 observation"
    if request.trigger_source != TRIGGER_SOURCE_CLI:
        return "26C-A 第一版只允许 --trigger-source cli，不允许 scheduler 调用"
    if request.confirm_write and request.dry_run:
        return "--confirm-write 与 --dry-run 不能同时使用"
    return None


def _parse_optional_utc(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    return ensure_utc_aware(parsed)


def _print_report(report: StrategyPipelineObservationBuildReport) -> None:
    for line in format_strategy_pipeline_observation_report_lines(report):
        print(line)


if __name__ == "__main__":  # pragma: no cover - exercised by CLI use.
    sys.exit(main())
