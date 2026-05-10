"""Binance public REST constants.

This file belongs to `app/exchange/binance`.
It defines only USD-M Futures public market-data REST paths and validation constants.
It is called by `app/exchange/binance/rest_client.py` and tests.
It does not send HTTP requests by itself.
It does not read or write MySQL.
It does not read or write Redis.
It does not send Hermes alerts.
It does not call DeepSeek or any large language model.
It does not implement account, private stream, signing, or trading execution features.
"""

from __future__ import annotations

FUTURES_PING_PATH = "/fapi/v1/ping"
FUTURES_SERVER_TIME_PATH = "/fapi/v1/time"
FUTURES_EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
FUTURES_KLINES_PATH = "/fapi/v1/klines"

ALLOWED_PUBLIC_REST_PATHS = frozenset(
    {
        FUTURES_PING_PATH,
        FUTURES_SERVER_TIME_PATH,
        FUTURES_EXCHANGE_INFO_PATH,
        FUTURES_KLINES_PATH,
    }
)

ALLOWED_KLINE_INTERVALS = frozenset(
    {
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "6h",
        "8h",
        "12h",
        "1d",
        "3d",
        "1w",
        "1M",
    }
)
