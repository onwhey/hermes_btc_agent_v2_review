"""Read-only CLI for 26B strategy evidence quality gate results.

触发方式：用户手动执行，例如：
`python -m scripts.check_strategy_evidence_quality --symbol BTCUSDT --base-interval 4h --higher-interval 1d --limit 20`

是否允许用户手动执行：允许。
是否允许 scheduler 调用：不允许；本脚本没有 scheduler job，也不得由 scheduler 配置引用。
必须参数：无；可选 `--evidence-aggregation-id` 精确查询某个 SEA，或按 symbol/interval/limit 查询最近结果。
调用 service：`app/strategy/evidence_quality/service.py::query_strategy_evidence_quality_results`。
不负责业务逻辑：不运行 26B 闸门，不调用 25 pipeline，不修改 25 调度，不修改策略算法，不修改 18/20/21。
数据库影响：只读 `strategy_evidence_quality_check_result`，不写任何业务表。
Redis 影响：不读写 Redis。
Hermes 影响：不发送 Hermes，不写 `alert_message`。
正式 K线影响：不读取或修改正式 K线表。
数据修复与交易边界：不自动修复，不人工改数，不调用真实模型，不读取账户，不生成订单，不自动交易。
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
from app.strategy.evidence_quality.service import StrategyEvidenceQualityGateService
from app.strategy.evidence_quality.types import (
    EXIT_PARAMETER_OR_DATABASE_ERROR,
    NON_TRADING_STATEMENT,
    StrategyEvidenceQualityQueryReport,
    StrategyEvidenceQualityQueryRequest,
    format_strategy_evidence_quality_report_lines,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the 26B read-only CLI parser."""

    parser = argparse.ArgumentParser(description="只读查询 26B 策略证据质量闸门结果。")
    parser.add_argument("--evidence-aggregation-id", default="")
    parser.add_argument("--symbol", default=DEFAULT_KLINE_SYMBOL)
    parser.add_argument("--base-interval", default=KLINE_4H_INTERVAL_VALUE)
    parser.add_argument("--higher-interval", default=KLINE_1D_INTERVAL_VALUE)
    parser.add_argument("--limit", type=int, default=20)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    service: StrategyEvidenceQualityGateService | Any | None = None,
    settings: AppSettings | None = None,
    session_scope_factory: Callable[..., AbstractContextManager[Any]] | None = None,
) -> int:
    """Parse args, call read-only service query, and print Chinese output.

    Parameters allow tests to inject a fake service/session. Return code 0 means
    all queried rows are non-blocking or there are no rows; 1 means at least one
    queried quality result is failed/blocking; 2 means parameter or database
    query failure. This function never writes MySQL, never sends Hermes, never
    calls models, and never trades.
    """

    parser = build_arg_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_OR_DATABASE_ERROR

    request = StrategyEvidenceQualityQueryRequest(
        evidence_aggregation_id=str(args.evidence_aggregation_id or "").strip() or None,
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
    active_service = service or StrategyEvidenceQualityGateService(settings=active_settings)
    try:
        with scope_factory(settings=active_settings, commit_on_success=False) as db_session:
            report = _run_query_service(db_session=db_session, request=request, service=active_service)
    except Exception as exc:  # noqa: BLE001 - CLI maps DB/query failures to exit code 2.
        print(f"数据库查询失败或观测失败：{exc}")
        print(NON_TRADING_STATEMENT)
        return EXIT_PARAMETER_OR_DATABASE_ERROR

    _print_report(report)
    return report.exit_code


def _run_query_service(
    *,
    db_session: Any,
    request: StrategyEvidenceQualityQueryRequest,
    service: StrategyEvidenceQualityGateService | Any,
) -> StrategyEvidenceQualityQueryReport:
    return service.query_strategy_evidence_quality_results(db_session, request=request)


def _validate_request(request: StrategyEvidenceQualityQueryRequest) -> str | None:
    if not request.evidence_aggregation_id and not request.symbol:
        return "symbol 不能为空"
    if request.base_interval != KLINE_4H_INTERVAL_VALUE:
        return "26B 第一版只支持 base_interval=4h"
    if request.higher_interval != KLINE_1D_INTERVAL_VALUE:
        return "26B 第一版只支持 higher_interval=1d"
    if request.limit <= 0:
        return "limit 必须大于 0"
    if request.limit > 200:
        return "limit 不能超过 200，避免一次性输出过多结果"
    return None


def _print_report(report: StrategyEvidenceQualityQueryReport) -> None:
    for line in format_strategy_evidence_quality_report_lines(report):
        print(line)


if __name__ == "__main__":  # pragma: no cover - exercised by CLI use.
    sys.exit(main())
