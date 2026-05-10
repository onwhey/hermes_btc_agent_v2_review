"""Manual Binance public REST check entry.

Triggered by: a user running `python -m scripts.check_binance_rest`.
Manual execution: allowed.
Scheduler execution: not allowed; this phase provides no scheduler job and this
script should not be referenced by scheduler configuration.
Required parameters: none for dry-run; pass `--request-real-binance` to make real
public Binance REST requests.
Calls: `app.exchange.binance.rest_client.BinanceRestClient`.
Does not contain core business logic beyond CLI argument parsing and health checks.
Does not read or write MySQL.
Does not read or write Redis.
Does not send Hermes alerts.
Does not modify formal Kline tables.
Does not automatically repair data.
Does not execute trades or access private endpoints.
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping, Protocol

from app.core.config import AppSettings, get_settings
from app.core.logger import configure_logging, get_logger
from app.exchange.binance.exceptions import BinanceValidationError
from app.exchange.binance.rest_client import BinanceRestClient, build_kline_params
from app.exchange.binance.types import BinanceHttpResponse


class BinanceRestCheckClient(Protocol):
    """Protocol for the manual check client.

    Parameters: none.
    Return value: structural type used for tests and the concrete client.
    Failure scenarios: concrete methods may raise Binance exceptions.
    External service access: protocol itself accesses nothing.
    Data impact: no MySQL, Redis, Hermes, DeepSeek, or trading execution.
    """

    def ping(self) -> bool:
        """Return public REST connectivity status without writing project data."""

    def get_server_time(self) -> Any:
        """Return parsed public Binance server time."""

    def get_exchange_info(self) -> dict[str, Any]:
        """Return public exchange metadata."""

    def get_klines(
        self,
        *,
        symbol: str | None = None,
        interval: str | None = None,
        limit: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[list[Any]]:
        """Return raw public Kline rows without parsing or writing them."""


def _dry_run_http_get(
    url: str,
    params: Mapping[str, object],
    timeout_seconds: float,
) -> BinanceHttpResponse:
    """Fail fast if dry-run accidentally tries to send a real HTTP request.

    Parameters: public REST URL, query params, and timeout passed by the client.
    Return value: never returns; raises `AssertionError`.
    Failure scenarios: any call means dry-run crossed its no-network boundary.
    External service access: none.
    Data impact: no MySQL, Redis, Hermes, formal Kline writes, or trading execution.
    """

    raise AssertionError("dry-run http_get must not be called")


def _collect_local_config_errors(settings: AppSettings) -> list[str]:
    """Validate Binance REST local config without making a network request.

    Parameters: `settings` is the unified app config.
    Return value: safe validation errors; empty list means local client config passed.
    Failure scenarios: invalid base URL, timeout, retry count, or retry backoff.
    External service access: none; injected dry-run transport raises if called.
    Data impact: no MySQL, Redis, Hermes, formal Kline writes, or trading execution.
    """

    errors: list[str] = []
    try:
        BinanceRestClient(settings=settings, http_get=_dry_run_http_get)
    except BinanceValidationError as exc:
        errors.append(f"Binance REST dry-run config validation failed: {exc}")

    if settings.binance_retry_backoff_seconds < 0:
        errors.append(
            "Binance REST dry-run config validation failed: "
            "BINANCE_RETRY_BACKOFF_SECONDS must be greater than or equal to 0"
        )
    return errors


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the manual check script.

    Parameters: none.
    Return value: configured `argparse.ArgumentParser`.
    Failure scenarios: invalid CLI args are handled by argparse.
    External service access: none.
    Data impact: no MySQL, Redis, Hermes, formal Kline writes, or trading execution.
    """

    parser = argparse.ArgumentParser(
        description="Manual Binance USD-M public REST dry-run or connectivity check.",
    )
    parser.add_argument(
        "--request-real-binance",
        action="store_true",
        help="Make real public Binance REST requests. Without this flag only config and params are checked.",
    )
    parser.add_argument("--symbol", default=None, help="Public symbol for Kline check, default from settings.")
    parser.add_argument("--interval", default=None, help="Public Kline interval, default from settings.")
    parser.add_argument("--limit", type=int, default=None, help="Public Kline limit, default from settings.")
    return parser


def collect_binance_rest_errors(
    *,
    settings: AppSettings | None = None,
    request_real_binance: bool = False,
    client: BinanceRestCheckClient | None = None,
    symbol: str | None = None,
    interval: str | None = None,
    limit: int | None = None,
) -> list[str]:
    """Collect manual Binance public REST check errors.

    Parameters: `settings` injects config for tests; `request_real_binance`
    controls whether real public REST requests are made; `client` allows tests to
    mock network access; `symbol`, `interval`, and `limit` override Kline params.
    Return value: list of safe error strings; empty list means the check passed.
    Failure scenarios: local validation or public REST exceptions are converted
    into error strings for CLI output.
    External service access: only when `request_real_binance=True`; dry-run never
    calls a network method.
    Data impact: no MySQL, Redis, Hermes, formal Kline writes, DeepSeek, or trading execution.
    """

    active_settings = settings if settings is not None else get_settings()
    configure_logging(active_settings, enable_file=False)
    logger = get_logger("scripts.check_binance_rest")
    errors: list[str] = []

    request_symbol = symbol or active_settings.binance_default_symbol
    request_interval = interval or active_settings.binance_default_interval
    request_limit = limit or active_settings.binance_kline_default_limit

    errors.extend(_collect_local_config_errors(active_settings))

    try:
        build_kline_params(
            symbol=request_symbol,
            interval=request_interval,
            limit=request_limit,
            max_limit=active_settings.binance_kline_max_limit,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must convert validation failures.
        errors.append(f"Binance REST dry-run validation failed: {exc}")

    if errors:
        return errors

    if not request_real_binance:
        logger.info("Binance public REST dry-run passed; no real request was sent.")
        return errors

    logger.info("User manually triggered real Binance public REST connectivity check.")
    active_client = client if client is not None else BinanceRestClient(settings=active_settings)
    try:
        if not active_client.ping():
            errors.append("Binance public REST ping returned false")
        server_time = active_client.get_server_time()
        if getattr(server_time, "server_time_ms", 0) <= 0:
            errors.append("Binance server time is missing or non-positive")
        exchange_info = active_client.get_exchange_info()
        if not _exchange_info_contains_symbol(exchange_info, request_symbol):
            errors.append(f"Binance exchangeInfo does not contain symbol {request_symbol}")
        klines = active_client.get_klines(
            symbol=request_symbol,
            interval=request_interval,
            limit=request_limit,
        )
        if not klines:
            errors.append("Binance public Kline check returned no rows")
    except Exception as exc:  # noqa: BLE001 - manual CLI reports safe summary only.
        errors.append(f"Binance public REST real check failed: {exc}")
    return errors


def _exchange_info_contains_symbol(exchange_info: dict[str, Any], symbol: str) -> bool:
    symbols = exchange_info.get("symbols")
    if not isinstance(symbols, list):
        return False
    expected_symbol = symbol.strip().upper()
    return any(
        isinstance(item, dict) and item.get("symbol") == expected_symbol
        for item in symbols
    )


def main() -> int:
    """Run the manual Binance public REST check CLI.

    Parameters: command-line arguments.
    Return value: process exit code; `0` for success and `1` for failed checks.
    Failure scenarios: invalid CLI args are handled by argparse; runtime failures
    are reported as safe messages without printing secrets.
    External service access: only with explicit `--request-real-binance`.
    Data impact: no MySQL, Redis, Hermes, formal Kline writes, DeepSeek, or trading execution.
    """

    args = build_argument_parser().parse_args()
    settings = get_settings()
    configure_logging(settings=settings, enable_file=False)
    errors = collect_binance_rest_errors(
        settings=settings,
        request_real_binance=args.request_real_binance,
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
    )
    if errors:
        for error in errors:
            print(error)
        return 1
    if args.request_real_binance:
        print("Binance public REST real connectivity check passed.")
    else:
        print("Binance public REST dry-run passed. No real request was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
