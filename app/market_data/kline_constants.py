"""Constants for formal market Kline data.

This file belongs to `app/market_data`.
It defines phase-06 constants for BTCUSDT 4h formal Kline parsing and validation.
It is called by DTO/parser/validator/repository tests and later market-data services.
It does not request Binance, read/write MySQL, read/write Redis, send Hermes, call
large language models, or perform any trading action.
"""

from __future__ import annotations

DEFAULT_EXCHANGE = "binance"
DEFAULT_MARKET_TYPE = "um_futures"
DEFAULT_KLINE_SYMBOL = "BTCUSDT"

KLINE_4H_INTERVAL_VALUE = "4h"
KLINE_4H_INTERVAL_MS = 14_400_000

TRIGGER_SOURCE_CLI = "cli"
TRIGGER_SOURCE_SCHEDULER = "scheduler"

DATA_SOURCE_BINANCE_REST_BY_CLI = "binance_rest_by_cli"
DATA_SOURCE_BINANCE_REST_BY_SCHEDULER = "binance_rest_by_scheduler"

TRIGGER_SOURCE_TO_DATA_SOURCE = {
    TRIGGER_SOURCE_CLI: DATA_SOURCE_BINANCE_REST_BY_CLI,
    TRIGGER_SOURCE_SCHEDULER: DATA_SOURCE_BINANCE_REST_BY_SCHEDULER,
}

ALLOWED_TRIGGER_SOURCES = frozenset(TRIGGER_SOURCE_TO_DATA_SOURCE)
ALLOWED_DATA_SOURCES = frozenset(TRIGGER_SOURCE_TO_DATA_SOURCE.values())

BINANCE_KLINE_FIELD_COUNT = 12

