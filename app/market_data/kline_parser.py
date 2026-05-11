"""Parser for Binance REST raw Kline rows.

This file belongs to `app/market_data`.
It converts Binance `/fapi/v1/klines` raw arrays into `MarketKlineDTO`.
It is called by the phase-06 check script, tests, and later Kline services.
It does not request Binance, query MySQL, write MySQL, read/write Redis, send
Hermes, call large language models, check batch continuity, or perform trades.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

from app.core.exceptions import KlineParseError, KlineValidationError
from app.core.time_utils import timestamp_ms_to_utc_datetime, utc_aware_to_prc_aware
from app.market_data.kline_constants import (
    BINANCE_KLINE_FIELD_COUNT,
    TRIGGER_SOURCE_TO_DATA_SOURCE,
)
from app.market_data.kline_dto import MarketKlineDTO


def calculate_raw_payload_json(raw_kline: Sequence[Any]) -> str:
    """Serialize one raw Binance Kline row into canonical JSON text.

    Parameters: `raw_kline` is the raw row supplied by Binance REST or a test fixture.
    Return value: compact JSON string preserving the array order.
    Failure scenarios: non-JSON-serializable values raise `KlineParseError`.
    External service access: none.
    Data impact: no database, Redis, Hermes, or trading side effects.
    """

    try:
        return json.dumps(list(raw_kline), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise KlineParseError("raw Binance Kline payload is not JSON serializable") from exc


def calculate_raw_payload_hash(raw_kline: Sequence[Any]) -> str:
    """Calculate a deterministic SHA-256 hash for one raw Binance Kline row.

    Parameters: `raw_kline` is the raw row supplied by Binance REST or a test fixture.
    Return value: lowercase SHA-256 hex digest of canonical JSON text.
    Failure scenarios: serialization failures raise `KlineParseError`.
    External service access and data impact: none.
    """

    payload_json = calculate_raw_payload_json(raw_kline)
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def data_source_from_trigger_source(trigger_source: str) -> str:
    """Map explicit trigger source to the formal Kline data source.

    Parameters: `trigger_source` must be `cli` or `scheduler`.
    Return value: corresponding Binance REST data source string.
    Failure scenarios: unsupported trigger source raises `KlineValidationError`.
    External service access: none.
    Data impact: no MySQL, Redis, Hermes, or trading side effects.
    """

    normalized = trigger_source.strip()
    try:
        return TRIGGER_SOURCE_TO_DATA_SOURCE[normalized]
    except KeyError as exc:
        raise KlineValidationError(f"unsupported Kline trigger_source: {trigger_source}") from exc


def parse_binance_kline(
    raw_kline: Sequence[Any],
    *,
    symbol: str,
    interval_value: str,
    trigger_source: str,
) -> MarketKlineDTO:
    """Parse one Binance `/fapi/v1/klines` raw row.

    Parameters: `raw_kline` follows Binance official field order; `symbol` and
    `interval_value` come from the caller; `trigger_source` is explicit and is
    never guessed from script names or runtime context.
    Return value: one `MarketKlineDTO`.
    Failure scenarios: short row, invalid Decimal, invalid timestamp, empty symbol,
    or unsupported trigger source raises a Kline exception.
    External service access: none.
    Data impact: no MySQL write, Redis write, Hermes send, or trading execution.
    """

    row = list(raw_kline)
    if len(row) < BINANCE_KLINE_FIELD_COUNT:
        raise KlineParseError(
            f"raw Binance Kline row has {len(row)} fields, expected at least {BINANCE_KLINE_FIELD_COUNT}"
        )

    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise KlineParseError("Kline symbol must not be empty")

    normalized_interval = interval_value.strip()
    normalized_trigger_source = trigger_source.strip()
    data_source = data_source_from_trigger_source(normalized_trigger_source)

    open_time_ms = _parse_int_field(row[0], "open_time_ms")
    close_time_ms = _parse_int_field(row[6], "close_time_ms")
    trade_count = _parse_int_field(row[8], "trade_count")

    open_time_utc = _timestamp_to_utc(open_time_ms, "open_time_ms")
    close_time_utc = _timestamp_to_utc(close_time_ms, "close_time_ms")

    payload_json = calculate_raw_payload_json(row)
    payload_hash = calculate_raw_payload_hash(row)

    return MarketKlineDTO(
        symbol=normalized_symbol,
        interval_value=normalized_interval,
        open_time_ms=open_time_ms,
        open_time_utc=open_time_utc,
        open_time_prc=utc_aware_to_prc_aware(open_time_utc),
        close_time_ms=close_time_ms,
        close_time_utc=close_time_utc,
        close_time_prc=utc_aware_to_prc_aware(close_time_utc),
        open_price=_parse_decimal_field(row[1], "open_price"),
        high_price=_parse_decimal_field(row[2], "high_price"),
        low_price=_parse_decimal_field(row[3], "low_price"),
        close_price=_parse_decimal_field(row[4], "close_price"),
        volume=_parse_decimal_field(row[5], "volume"),
        quote_volume=_parse_decimal_field(row[7], "quote_volume"),
        trade_count=trade_count,
        taker_buy_base_volume=_parse_decimal_field(row[9], "taker_buy_base_volume"),
        taker_buy_quote_volume=_parse_decimal_field(row[10], "taker_buy_quote_volume"),
        data_source=data_source,
        trigger_source=normalized_trigger_source,
        raw_payload_json=payload_json,
        raw_payload_hash=payload_hash,
    )


def parse_binance_klines(
    raw_klines: Iterable[Sequence[Any]],
    *,
    symbol: str,
    interval_value: str,
    trigger_source: str,
) -> list[MarketKlineDTO]:
    """Parse multiple Binance raw Kline rows without checking continuity.

    Parameters: `raw_klines` is an iterable of Binance raw arrays; other arguments
    are passed to `parse_binance_kline`.
    Return value: list of parsed DTOs in the same order as input.
    Failure scenarios: any invalid row raises a Kline exception and stops parsing.
    External service access and data impact: none.
    """

    return [
        parse_binance_kline(
            raw_kline,
            symbol=symbol,
            interval_value=interval_value,
            trigger_source=trigger_source,
        )
        for raw_kline in raw_klines
    ]


def _parse_decimal_field(raw_value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as exc:
        raise KlineParseError(f"invalid Decimal field {field_name}") from exc


def _parse_int_field(raw_value: Any, field_name: str) -> int:
    try:
        return int(raw_value)
    except (TypeError, ValueError) as exc:
        raise KlineParseError(f"invalid integer field {field_name}") from exc


def _timestamp_to_utc(timestamp_ms: int, field_name: str):
    try:
        return timestamp_ms_to_utc_datetime(timestamp_ms)
    except (OverflowError, OSError, ValueError) as exc:
        raise KlineParseError(f"invalid timestamp field {field_name}") from exc

