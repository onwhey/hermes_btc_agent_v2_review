"""DTOs for structured formal market Kline data.

This file belongs to `app/market_data`.
It defines the phase-06 data object for one parsed BTCUSDT 4h Kline.
It is called by the parser, validator, repository, check script, and tests.
It does not request Binance, create database sessions, write MySQL, read/write Redis,
send Hermes, call large language models, or perform trading execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Sequence

BinanceRawKline = Sequence[Any]


@dataclass(frozen=True)
class MarketKlineDTO:
    """Structured representation of one formal 4h Kline.

    Parameters: fields mirror the phase-06 `market_kline_4h` table shape.
    Return value: immutable DTO used between parser, validator, and repository.
    Failure scenarios: construction itself does not validate business rules; callers
    should use `validate_market_kline` before persistence.
    External service access: none.
    Data impact: no MySQL/Redis writes, no Hermes sends, no large-model calls.
    This DTO does not decide collection ranges, repair data, or execute trades.
    """

    symbol: str
    interval_value: str

    open_time_ms: int
    open_time_utc: datetime
    open_time_prc: datetime

    close_time_ms: int
    close_time_utc: datetime
    close_time_prc: datetime

    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal

    volume: Decimal
    quote_volume: Decimal
    trade_count: int

    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal

    data_source: str
    trigger_source: str

    raw_payload_json: str
    raw_payload_hash: str


@dataclass(frozen=True)
class KlineParseResult:
    """Parser result for a batch of Klines.

    Parameters: `klines` is the parsed DTO list from one raw Binance response.
    Return value: immutable batch wrapper for tests and later services.
    Failure scenarios: parse failures are raised before this object is built.
    External service access and data impact: none.
    """

    klines: tuple[MarketKlineDTO, ...]

