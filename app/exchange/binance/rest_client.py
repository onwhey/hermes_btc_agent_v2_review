"""Binance USD-M Futures public REST client.

This file belongs to `app/exchange/binance`.
It wraps only public market-data REST endpoints needed by phase 05:
`/fapi/v1/ping`, `/fapi/v1/time`, `/fapi/v1/exchangeInfo`, and
`/fapi/v1/klines`.
It is called by `scripts/check_binance_rest.py`, later market-data services, and tests.
It may access Binance public REST only when an explicit method is called.
It does not read or write MySQL.
It does not read or write Redis.
It does not send Hermes alerts.
It does not call DeepSeek or any large language model.
It does not implement API-key signing, private endpoints, WebSocket, data persistence,
Kline quality checks, strategy advice, or trading execution.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, cast

from app.core.config import AppSettings, get_settings
from app.core.logger import get_logger
from app.core.time_utils import timestamp_ms_to_utc_datetime
from app.exchange.binance.constants import (
    ALLOWED_KLINE_INTERVALS,
    ALLOWED_PUBLIC_REST_PATHS,
    FUTURES_EXCHANGE_INFO_PATH,
    FUTURES_KLINES_PATH,
    FUTURES_PING_PATH,
    FUTURES_SERVER_TIME_PATH,
)
from app.exchange.binance.exceptions import (
    BinanceHTTPError,
    BinanceRateLimitError,
    BinanceRequestError,
    BinanceResponseError,
    BinanceTimeoutError,
    BinanceValidationError,
)
from app.exchange.binance.types import (
    BinanceHttpResponse,
    BinanceKlineRaw,
    BinanceServerTime,
)

BinanceHttpGet = Callable[[str, Mapping[str, object], float], BinanceHttpResponse]


def default_http_get(
    url: str,
    params: Mapping[str, object],
    timeout_seconds: float,
) -> BinanceHttpResponse:
    """Send one public Binance REST GET request through the standard library.

    Parameters: `url` is the public endpoint URL without query string; `params`
    are public market-data query parameters; `timeout_seconds` is the socket timeout.
    Return value: raw status code and response body.
    Failure scenarios: network failures, DNS failures, and timeouts propagate to
    `BinanceRestClient`, where they are converted into explicit Binance exceptions.
    External service access: this function performs an HTTP GET to Binance only
    when explicitly called by a client method or manual check script.
    Data impact: it does not read/write MySQL or Redis and does not send Hermes.
    This function does not sign requests or access private endpoints.
    """

    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(
        full_url,
        headers={"User-Agent": "hermes-btc-agent/phase05-public-rest"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            return BinanceHttpResponse(status_code=response.status, body=body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return BinanceHttpResponse(status_code=exc.code, body=body)


def normalize_binance_symbol(symbol: str) -> str:
    """Normalize a public market-data symbol without guessing data sources.

    Parameters: `symbol` is the Binance public symbol such as `BTCUSDT`.
    Return value: uppercase symbol.
    Failure scenarios: empty or whitespace-only symbols raise `BinanceValidationError`.
    External service access: none.
    Data impact: no MySQL, Redis, Hermes, Kline writes, or trading execution.
    """

    normalized = symbol.strip().upper()
    if not normalized:
        raise BinanceValidationError("BINANCE symbol must not be empty")
    if any(char.isspace() for char in normalized):
        raise BinanceValidationError("BINANCE symbol must not contain whitespace")
    return normalized


def validate_kline_interval(interval: str) -> str:
    """Validate a Binance public Kline interval.

    Parameters: `interval` is the raw interval string from config, CLI, or caller.
    Return value: validated interval string.
    Failure scenarios: unsupported intervals raise `BinanceValidationError`.
    External service access: none.
    Data impact: no MySQL, Redis, Hermes, Kline writes, or trading execution.
    """

    normalized = interval.strip()
    if normalized not in ALLOWED_KLINE_INTERVALS:
        allowed = ", ".join(sorted(ALLOWED_KLINE_INTERVALS))
        raise BinanceValidationError(f"Unsupported Binance Kline interval: {interval}. Allowed: {allowed}")
    return normalized


def validate_kline_limit(limit: int, *, max_limit: int) -> int:
    """Validate the public Binance Kline limit.

    Parameters: `limit` is requested row count; `max_limit` is the configured upper bound.
    Return value: validated integer limit.
    Failure scenarios: non-positive or too-large limits raise `BinanceValidationError`.
    External service access: none.
    Data impact: no MySQL, Redis, Hermes, Kline writes, or trading execution.
    """

    if max_limit <= 0:
        raise BinanceValidationError("BINANCE_KLINE_MAX_LIMIT must be greater than 0")
    if limit <= 0:
        raise BinanceValidationError("Binance Kline limit must be greater than 0")
    if limit > max_limit:
        raise BinanceValidationError(
            f"Binance Kline limit {limit} exceeds configured max limit {max_limit}"
        )
    return limit


def build_kline_params(
    *,
    symbol: str,
    interval: str,
    limit: int,
    max_limit: int,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
) -> dict[str, object]:
    """Build validated public `/fapi/v1/klines` query parameters.

    Parameters: `symbol`, `interval`, and `limit` map to Binance public REST
    parameters; `start_time_ms` and `end_time_ms` are optional UTC millisecond
    bounds from Binance time semantics.
    Return value: query parameter dictionary using Binance official names.
    Failure scenarios: invalid symbol, interval, limit, or time range raises
    `BinanceValidationError`.
    External service access: none.
    Data impact: this function does not read/write MySQL or Redis, does not send
    Hermes, and does not write formal Kline data.
    """

    params: dict[str, object] = {
        "symbol": normalize_binance_symbol(symbol),
        "interval": validate_kline_interval(interval),
        "limit": validate_kline_limit(limit, max_limit=max_limit),
    }
    if start_time_ms is not None:
        if start_time_ms < 0:
            raise BinanceValidationError("start_time_ms must be greater than or equal to 0")
        params["startTime"] = start_time_ms
    if end_time_ms is not None:
        if end_time_ms < 0:
            raise BinanceValidationError("end_time_ms must be greater than or equal to 0")
        params["endTime"] = end_time_ms
    if start_time_ms is not None and end_time_ms is not None and start_time_ms >= end_time_ms:
        raise BinanceValidationError("start_time_ms must be less than end_time_ms")
    return params


class BinanceRestClient:
    """Client for Binance USD-M Futures public REST endpoints.

    Parameters: `settings` provides base URL, timeout, retry, and Kline defaults;
    `http_get` is injectable so tests can mock all network access.
    Return value: client instance.
    Failure scenarios: invalid local config raises `BinanceValidationError`;
    request failures raise explicit Binance exceptions from client methods.
    External service access: no connection occurs at construction time; public
    HTTP requests occur only when `ping`, `get_server_time`, `get_exchange_info`,
    or `get_klines` is called.
    Data impact: no MySQL writes, Redis writes, Hermes sends, DeepSeek calls,
    or trading execution.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        http_get: BinanceHttpGet | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._http_get = http_get if http_get is not None else default_http_get
        self._logger = get_logger("exchange.binance.rest_client")
        self._base_url = self._validate_base_url(self._settings.binance_base_url)
        self._timeout_seconds = self._validate_timeout(self._settings.binance_timeout_seconds)
        self._max_retries = self._validate_max_retries(self._settings.binance_max_retries)
        self._retry_backoff_seconds = max(0.0, self._settings.binance_retry_backoff_seconds)

    @staticmethod
    def _validate_base_url(base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        parsed = urllib.parse.urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BinanceValidationError("BINANCE_BASE_URL must be an http(s) URL")
        return normalized

    @staticmethod
    def _validate_timeout(timeout_seconds: float) -> float:
        if timeout_seconds <= 0:
            raise BinanceValidationError("BINANCE_TIMEOUT_SECONDS must be greater than 0")
        return timeout_seconds

    @staticmethod
    def _validate_max_retries(max_retries: int) -> int:
        if max_retries < 0:
            raise BinanceValidationError("BINANCE_MAX_RETRIES must be greater than or equal to 0")
        return max_retries

    def _build_public_url(self, path: str) -> str:
        if path not in ALLOWED_PUBLIC_REST_PATHS:
            raise BinanceValidationError(f"Unsupported Binance public REST path: {path}")
        return f"{self._base_url}{path}"

    def _request_json(
        self,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Any:
        """Request one allowed public REST path and parse JSON.

        Parameters: `path` must be in `ALLOWED_PUBLIC_REST_PATHS`; `params` are
        public market-data query parameters.
        Return value: parsed JSON body.
        Failure scenarios: timeout, network error, rate limit, HTTP error, or
        invalid JSON raises explicit Binance exceptions.
        External service access: performs one or more bounded public REST requests.
        Data impact: no MySQL, Redis, Hermes, Kline writes, DeepSeek, or trading execution.
        """

        request_params = dict(params or {})
        url = self._build_public_url(path)
        last_error: Exception | None = None
        total_attempts = self._max_retries + 1

        for attempt_number in range(1, total_attempts + 1):
            try:
                response = self._http_get(url, request_params, self._timeout_seconds)
            except (TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt_number < total_attempts:
                    self._log_retry(path, attempt_number, "timeout")
                    self._sleep_before_retry(attempt_number)
                    continue
                raise BinanceTimeoutError(f"Binance public REST request timed out: {path}") from exc
            except urllib.error.URLError as exc:
                last_error = exc
                if _url_error_is_timeout(exc):
                    if attempt_number < total_attempts:
                        self._log_retry(path, attempt_number, "timeout")
                        self._sleep_before_retry(attempt_number)
                        continue
                    raise BinanceTimeoutError(f"Binance public REST request timed out: {path}") from exc
                if attempt_number < total_attempts:
                    self._log_retry(path, attempt_number, "network_error")
                    self._sleep_before_retry(attempt_number)
                    continue
                raise BinanceRequestError(f"Binance public REST network error: {path}") from exc
            except OSError as exc:
                last_error = exc
                if attempt_number < total_attempts:
                    self._log_retry(path, attempt_number, "network_error")
                    self._sleep_before_retry(attempt_number)
                    continue
                raise BinanceRequestError(f"Binance public REST network error: {path}") from exc

            if self._status_should_retry(response.status_code) and attempt_number < total_attempts:
                self._log_retry(path, attempt_number, f"http_{response.status_code}")
                self._sleep_before_retry(attempt_number)
                continue

            return self._parse_successful_response(path, response)

        raise BinanceRequestError(f"Binance public REST request failed: {path}") from last_error

    def _parse_successful_response(self, path: str, response: BinanceHttpResponse) -> Any:
        if response.status_code == 429:
            raise BinanceRateLimitError(
                f"Binance public REST rate limited: {path}",
                status_code=response.status_code,
                path=path,
            )
        if not 200 <= response.status_code < 300:
            binance_code = _extract_binance_error_code(response.body)
            raise BinanceHTTPError(
                f"Binance public REST HTTP error {response.status_code}: {path}",
                status_code=response.status_code,
                path=path,
                binance_code=binance_code,
            )

        body = response.body.strip() or "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise BinanceResponseError(f"Binance public REST returned invalid JSON: {path}") from exc

        if isinstance(data, dict) and _response_contains_binance_error(data):
            code = data.get("code")
            raise BinanceResponseError(f"Binance public REST returned error code {code}: {path}")
        return data

    @staticmethod
    def _status_should_retry(status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code < 600

    def _sleep_before_retry(self, attempt_number: int) -> None:
        if self._retry_backoff_seconds <= 0:
            return
        time.sleep(self._retry_backoff_seconds * attempt_number)

    def _log_retry(self, path: str, attempt_number: int, reason: str) -> None:
        self._logger.warning(
            "Retrying Binance public REST request path=%s attempt=%s reason=%s",
            path,
            attempt_number,
            reason,
        )

    def ping(self) -> bool:
        """Check Binance public REST connectivity.

        Parameters: none.
        Return value: `True` when `/fapi/v1/ping` returns a successful JSON body.
        Failure scenarios: timeout, HTTP, network, or parsing errors raise Binance exceptions.
        External service access: sends one bounded public REST request when called.
        Data impact: no MySQL, Redis, Hermes, Kline writes, DeepSeek, or trading execution.
        """

        self._request_json(FUTURES_PING_PATH)
        return True

    def get_server_time(self) -> BinanceServerTime:
        """Fetch Binance server time from the public REST endpoint.

        Parameters: none.
        Return value: `BinanceServerTime` containing UTC milliseconds and UTC datetime.
        Failure scenarios: missing or invalid `serverTime` raises `BinanceResponseError`.
        External service access: sends one bounded public REST request when called.
        Data impact: no MySQL, Redis, Hermes, Kline writes, DeepSeek, or trading execution.
        """

        data = self._request_json(FUTURES_SERVER_TIME_PATH)
        if not isinstance(data, dict) or not isinstance(data.get("serverTime"), int):
            raise BinanceResponseError("Binance server time response missing integer serverTime")
        server_time_ms = cast(int, data["serverTime"])
        return BinanceServerTime(
            server_time_ms=server_time_ms,
            server_time_utc=timestamp_ms_to_utc_datetime(server_time_ms),
        )

    def get_exchange_info(self) -> dict[str, Any]:
        """Fetch public USD-M Futures exchange information.

        Parameters: none.
        Return value: parsed JSON dictionary from `/fapi/v1/exchangeInfo`.
        Failure scenarios: non-dictionary response raises `BinanceResponseError`.
        External service access: sends one bounded public REST request when called.
        Data impact: no MySQL, Redis, Hermes, Kline writes, DeepSeek, or trading execution.
        """

        data = self._request_json(FUTURES_EXCHANGE_INFO_PATH)
        if not isinstance(data, dict):
            raise BinanceResponseError("Binance exchange info response must be a JSON object")
        return cast(dict[str, Any], data)

    def get_klines(
        self,
        *,
        symbol: str | None = None,
        interval: str | None = None,
        limit: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[BinanceKlineRaw]:
        """Fetch raw public Kline rows without parsing or persisting them.

        Parameters: `symbol`, `interval`, `limit`, `start_time_ms`, and
        `end_time_ms` map to Binance public `/fapi/v1/klines` parameters.
        Return value: raw list rows exactly as returned by Binance.
        Failure scenarios: invalid params, timeout, HTTP, network, parsing, or
        non-list response raises explicit Binance exceptions.
        External service access: sends one bounded public REST request when called.
        Data impact: this method does not parse to DTO, validate continuity, write
        MySQL, write Redis, send Hermes, call DeepSeek, or execute trades.
        """

        request_symbol = symbol if symbol is not None else self._settings.binance_default_symbol
        request_interval = interval if interval is not None else self._settings.binance_default_interval
        request_limit = limit if limit is not None else self._settings.binance_kline_default_limit
        params = build_kline_params(
            symbol=request_symbol,
            interval=request_interval,
            limit=request_limit,
            max_limit=self._settings.binance_kline_max_limit,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        data = self._request_json(FUTURES_KLINES_PATH, params=params)
        if not isinstance(data, list):
            raise BinanceResponseError("Binance Kline response must be a JSON array")
        for row in data:
            if not isinstance(row, list):
                raise BinanceResponseError("Each Binance Kline row must be a JSON array")
        return cast(list[BinanceKlineRaw], data)


def _url_error_is_timeout(exc: urllib.error.URLError) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(reason, (TimeoutError, socket.timeout))


def _extract_binance_error_code(body: str) -> int | None:
    try:
        data = json.loads(body or "{}")
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("code"), int):
        return cast(int, data["code"])
    return None


def _response_contains_binance_error(data: Mapping[str, Any]) -> bool:
    code = data.get("code")
    message = data.get("msg")
    return isinstance(code, int) and code != 0 and message is not None
