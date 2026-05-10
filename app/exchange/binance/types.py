"""Binance public REST data types.

This file belongs to `app/exchange/binance`.
It defines lightweight typed containers for public REST responses and tests.
It is called by `app/exchange/binance/rest_client.py`, scripts, and tests.
It does not send HTTP requests by itself.
It does not read or write MySQL.
It does not read or write Redis.
It does not send Hermes alerts.
It does not call DeepSeek or any large language model.
It does not implement account, signing, private stream, or trading execution features.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

BinanceKlineRaw = list[Any]


@dataclass(frozen=True)
class BinanceHttpResponse:
    """Raw HTTP response returned by the injectable public REST transport.

    Parameters: `status_code` is the HTTP status; `body` is the response text.
    Return value: immutable value object.
    Failure scenarios: none inside this value object.
    External service access: this class does not access external services.
    Data impact: this class does not read/write MySQL or Redis and does not send Hermes.
    """

    status_code: int
    body: str


@dataclass(frozen=True)
class BinanceServerTime:
    """Parsed Binance server time.

    Parameters: `server_time_ms` is Binance UTC milliseconds; `server_time_utc`
    is the same instant as a timezone-aware UTC datetime.
    Return value: immutable value object.
    Failure scenarios: parsing failures are raised by the REST client before this object is built.
    External service access: this class does not access external services.
    Data impact: this class does not read/write MySQL or Redis and does not send Hermes.
    """

    server_time_ms: int
    server_time_utc: datetime


@dataclass(frozen=True)
class BinanceRequestResult:
    """Diagnostic result for a completed public REST request.

    Parameters: `path` is the public REST path; `status_code` is the HTTP status;
    `data` is parsed JSON; `retry_count` is the number of retries already used;
    `elapsed_seconds` is wall-clock request duration.
    Return value: immutable value object.
    Failure scenarios: request failures are raised before this object is built.
    External service access: this class does not access external services.
    Data impact: this class does not read/write MySQL or Redis and does not send Hermes.
    """

    path: str
    status_code: int
    data: Any
    retry_count: int
    elapsed_seconds: float
