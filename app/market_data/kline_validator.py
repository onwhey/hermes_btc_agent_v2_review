"""Basic field validator for one structured 4h Kline.

This file belongs to `app/market_data`.
It validates one `MarketKlineDTO` before a caller passes it to storage.
It is called by the phase-06 check script, tests, and later Kline services.
It does not check batch continuity, query MySQL, request Binance, write Redis,
send Hermes, call large language models, repair data, or execute trades.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.exceptions import KlineValidationError
from app.core.time_utils import timestamp_ms_to_utc_datetime, utc_aware_to_prc_aware
from app.market_data.kline_constants import (
    KLINE_4H_INTERVAL_MS,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_TO_DATA_SOURCE,
)
from app.market_data.kline_dto import MarketKlineDTO

ZERO = Decimal("0")


def validate_market_kline(kline: MarketKlineDTO) -> MarketKlineDTO:
    """Validate one parsed 4h Kline DTO.

    Parameters: `kline` is a single parsed Kline. It must already come from the
    parser or an equivalent trusted test fixture.
    Return value: the same DTO when all phase-06 field rules pass.
    Failure scenarios: raises `KlineValidationError` with the failed rule name.
    External service access: none.
    Data impact: no MySQL/Redis writes, no Hermes sends, no trading execution.
    This function does not filter unclosed Klines because phase 06 has no
    Binance server-time access; later services must perform that check.
    """

    _validate_identity(kline)
    _validate_time_integrity(kline)
    _validate_ohlc(kline)
    _validate_non_negative_amounts(kline)
    _validate_source_mapping(kline)
    return kline


def _validate_identity(kline: MarketKlineDTO) -> None:
    if not kline.symbol.strip():
        raise KlineValidationError("symbol must not be empty")
    if kline.interval_value != KLINE_4H_INTERVAL_VALUE:
        raise KlineValidationError("interval_value must be 4h")


def _validate_time_integrity(kline: MarketKlineDTO) -> None:
    if kline.open_time_ms >= kline.close_time_ms:
        raise KlineValidationError("open_time_ms must be less than close_time_ms")
    if kline.open_time_utc >= kline.close_time_utc:
        raise KlineValidationError("open_time_utc must be less than close_time_utc")

    expected_close_time_ms = kline.open_time_ms + KLINE_4H_INTERVAL_MS - 1
    if kline.close_time_ms != expected_close_time_ms:
        raise KlineValidationError("close_time_ms must equal open_time_ms + 4h - 1ms")

    # A 4h Binance Kline must start exactly on the UTC 4h boundary. PRC fields
    # remain display-only and are validated against UTC conversion below.
    if kline.open_time_ms % KLINE_4H_INTERVAL_MS != 0:
        raise KlineValidationError("open_time_ms must align to a UTC 4h boundary")

    expected_open_time_utc = timestamp_ms_to_utc_datetime(kline.open_time_ms)
    if kline.open_time_utc.isoformat() != expected_open_time_utc.isoformat():
        raise KlineValidationError("open_time_utc must match open_time_ms")

    expected_close_time_utc = timestamp_ms_to_utc_datetime(kline.close_time_ms)
    if kline.close_time_utc.isoformat() != expected_close_time_utc.isoformat():
        raise KlineValidationError("close_time_utc must match close_time_ms")

    expected_open_time_prc = utc_aware_to_prc_aware(kline.open_time_utc)
    if kline.open_time_prc.isoformat() != expected_open_time_prc.isoformat():
        raise KlineValidationError("open_time_prc must match open_time_utc converted to PRC")

    expected_close_time_prc = utc_aware_to_prc_aware(kline.close_time_utc)
    if kline.close_time_prc.isoformat() != expected_close_time_prc.isoformat():
        raise KlineValidationError("close_time_prc must match close_time_utc converted to PRC")


def _validate_ohlc(kline: MarketKlineDTO) -> None:
    if kline.open_price <= ZERO:
        raise KlineValidationError("open_price must be greater than 0")
    if kline.high_price <= ZERO:
        raise KlineValidationError("high_price must be greater than 0")
    if kline.low_price <= ZERO:
        raise KlineValidationError("low_price must be greater than 0")
    if kline.close_price <= ZERO:
        raise KlineValidationError("close_price must be greater than 0")
    if kline.high_price < kline.open_price:
        raise KlineValidationError("high_price must be greater than or equal to open_price")
    if kline.high_price < kline.close_price:
        raise KlineValidationError("high_price must be greater than or equal to close_price")
    if kline.high_price < kline.low_price:
        raise KlineValidationError("high_price must be greater than or equal to low_price")
    if kline.low_price > kline.open_price:
        raise KlineValidationError("low_price must be less than or equal to open_price")
    if kline.low_price > kline.close_price:
        raise KlineValidationError("low_price must be less than or equal to close_price")


def _validate_non_negative_amounts(kline: MarketKlineDTO) -> None:
    if kline.volume < ZERO:
        raise KlineValidationError("volume must be greater than or equal to 0")
    if kline.quote_volume < ZERO:
        raise KlineValidationError("quote_volume must be greater than or equal to 0")
    if kline.trade_count < 0:
        raise KlineValidationError("trade_count must be greater than or equal to 0")
    if kline.taker_buy_base_volume < ZERO:
        raise KlineValidationError("taker_buy_base_volume must be greater than or equal to 0")
    if kline.taker_buy_quote_volume < ZERO:
        raise KlineValidationError("taker_buy_quote_volume must be greater than or equal to 0")


def _validate_source_mapping(kline: MarketKlineDTO) -> None:
    expected_data_source = TRIGGER_SOURCE_TO_DATA_SOURCE.get(kline.trigger_source)
    if expected_data_source is None:
        raise KlineValidationError("trigger_source is not allowed")
    if kline.data_source != expected_data_source:
        raise KlineValidationError("data_source does not match trigger_source")
