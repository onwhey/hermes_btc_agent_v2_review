"""Parser for Binance aggTrade WebSocket price events.

This file belongs to `app/market_data/price_monitor`.
It parses raw Binance public WebSocket `aggTrade` messages into `PriceEvent`.
It does not write Redis, write MySQL, send Hermes, request REST latest prices,
call DeepSeek, generate advice, or perform trading.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from app.core.time_utils import now_utc
from app.exchange.binance.rest_client import normalize_binance_symbol
from app.market_data.price_monitor.exceptions import PriceEventParseError
from app.market_data.price_monitor.types import PRICE_SOURCE_BINANCE_WS_AGG_TRADE, PriceEvent


def parse_agg_trade_event(raw_message: str | Mapping[str, Any], *, expected_symbol: str) -> PriceEvent:
    """Parse and validate one Binance `aggTrade` WebSocket message.

    Parameters: `raw_message` is a JSON string or decoded mapping from Binance;
    `expected_symbol` is the uppercase symbol the monitor subscribed to.
    Return value: `PriceEvent` with Decimal price and Binance event/trade times.
    Failure scenarios: malformed JSON, missing fields, wrong event type, wrong
    symbol, invalid price, non-positive price, or invalid times raise
    `PriceEventParseError`.
    External service access: none.
    Data impact: no Redis/MySQL writes, no Hermes sends, no DeepSeek, no trades.
    """

    data = _load_message(raw_message)
    if "data" in data and isinstance(data["data"], Mapping):
        data = dict(data["data"])

    event_type = data.get("e")
    if event_type != "aggTrade":
        raise PriceEventParseError(f"Binance price event type must be aggTrade, got {event_type!r}")

    expected = normalize_binance_symbol(expected_symbol)
    symbol = _required_str(data, "s")
    if normalize_binance_symbol(symbol) != expected:
        raise PriceEventParseError(f"Binance price event symbol mismatch: expected {expected}, got {symbol}")

    price = _parse_positive_decimal(_required_value(data, "p"), "p")
    event_time_ms = _parse_non_negative_int(_required_value(data, "E"), "E")
    trade_time_ms = _parse_non_negative_int(_required_value(data, "T"), "T")

    return PriceEvent(
        symbol=expected,
        price=price,
        event_time_ms=event_time_ms,
        trade_time_ms=trade_time_ms,
        received_at_utc=now_utc(),
        source=PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
    )


def _load_message(raw_message: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(raw_message, Mapping):
        return dict(raw_message)
    try:
        data = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise PriceEventParseError("Binance price event is not valid JSON") from exc
    if not isinstance(data, dict):
        raise PriceEventParseError("Binance price event JSON must be an object")
    return data


def _required_value(data: Mapping[str, Any], field_name: str) -> Any:
    if field_name not in data:
        raise PriceEventParseError(f"Binance price event missing required field: {field_name}")
    return data[field_name]


def _required_str(data: Mapping[str, Any], field_name: str) -> str:
    value = _required_value(data, field_name)
    if not isinstance(value, str) or not value.strip():
        raise PriceEventParseError(f"Binance price event field {field_name} must be a non-empty string")
    return value


def _parse_positive_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, float):
        raise PriceEventParseError(f"Binance price event field {field_name} must not be float")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PriceEventParseError(f"Binance price event field {field_name} must be Decimal-compatible") from exc
    if price <= 0:
        raise PriceEventParseError(f"Binance price event field {field_name} must be greater than 0")
    return price


def _parse_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise PriceEventParseError(f"Binance price event field {field_name} must be an integer timestamp")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PriceEventParseError(f"Binance price event field {field_name} must be an integer timestamp") from exc
    if parsed < 0:
        raise PriceEventParseError(f"Binance price event field {field_name} must be non-negative")
    return parsed
