"""Persistent 10s WebSocket price monitor process entry.

Triggered by: a user, systemd, or supervisor starting a long-running process.
Manual execution: allowed with `--trigger-source cli`.
Scheduler execution: scheduler must not start this script every 10 seconds.
Required args: `--trigger-source cli|systemd|supervisor`.
Calls: `app/market_data/price_monitor/price_monitor_service.py::run_price_monitor`.
External effects: the service opens Binance public WebSocket, writes Redis
`bitcoin_price`, and may send Hermes fixed-template alerts.
This script itself does not connect WebSocket, read/write Redis, send Hermes,
request Binance REST, write MySQL, modify formal Klines, repair data, call
DeepSeek, generate advice, or perform trading.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from typing import Sequence

from app.market_data.price_monitor.exceptions import PriceMonitorValidationError
from app.market_data.price_monitor.price_monitor_service import (
    build_price_monitor_config_from_settings,
    run_price_monitor,
)
from app.market_data.price_monitor.types import (
    ALLOWED_PRICE_MONITOR_TRIGGER_SOURCES,
    EXIT_PARAMETER_ERROR,
    PriceMonitorResult,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser for the persistent WebSocket price monitor."""

    parser = argparse.ArgumentParser(description="Run BTCUSDT 10s WebSocket price monitor.")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--trigger-source", required=True, choices=sorted(ALLOWED_PRICE_MONITOR_TRIGGER_SOURCES))
    parser.add_argument("--monitor-interval-seconds", type=int, default=None)
    parser.add_argument("--price-change-threshold", default=None)
    parser.add_argument("--redis-key", default=None)
    parser.add_argument("--redis-ttl-seconds", type=int, default=None)
    parser.add_argument("--enable-price-alerts", action="store_true", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args, call service, print result, and return exit code."""

    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if int(exc.code) == 0 else EXIT_PARAMETER_ERROR

    try:
        threshold = _parse_optional_decimal(args.price_change_threshold)
        config = build_price_monitor_config_from_settings(
            trigger_source=args.trigger_source,
            symbol=args.symbol.strip().upper() if args.symbol else None,
            monitor_interval_seconds=args.monitor_interval_seconds,
            price_change_threshold=threshold,
            redis_key=args.redis_key,
            redis_ttl_seconds=args.redis_ttl_seconds,
            enable_price_alerts=args.enable_price_alerts,
        )
    except (InvalidOperation, PriceMonitorValidationError, ValueError) as exc:
        print(f"参数错误：{exc}")
        return EXIT_PARAMETER_ERROR

    result = run_price_monitor(config)
    for line in format_price_monitor_result_lines(result):
        print(line)
    return result.exit_code


def format_price_monitor_result_lines(result: PriceMonitorResult) -> list[str]:
    """Format service result for CLI output."""

    return [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"trace_id={result.trace_id}",
        f"message={result.message}",
        f"redis_written={result.redis_written}",
        f"alert_status={result.alert_status}",
    ]


def _parse_optional_decimal(raw_value: str | None) -> Decimal | None:
    if raw_value is None:
        return None
    if raw_value.strip() == "":
        raise ValueError("price-change-threshold must not be empty")
    return Decimal(raw_value)


if __name__ == "__main__":
    raise SystemExit(main())

