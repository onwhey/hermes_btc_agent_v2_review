"""Redis state helpers for the 10s WebSocket price monitor.

This file belongs to `app/market_data/price_monitor`.
It reads and writes the short-lived `bitcoin_price` JSON state in Redis.
It does not connect to Binance, write MySQL, send Hermes, call DeepSeek,
request REST latest prices, generate advice, or perform trading.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from app.core.exceptions import RedisError, ValidationError
from app.core.time_utils import format_datetime_with_timezone, now_utc, utc_aware_to_prc_aware
from app.market_data.price_monitor.exceptions import PriceStateParseError
from app.market_data.price_monitor.types import (
    PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
    PriceEvent,
    PriceState,
)


def build_price_state_from_event(price_event: PriceEvent) -> PriceState:
    """Build Redis state from the latest valid Binance WebSocket price event."""

    saved_at_utc = now_utc()
    return PriceState(
        symbol=price_event.symbol,
        price=price_event.price,
        event_time_ms=price_event.event_time_ms,
        trade_time_ms=price_event.trade_time_ms,
        saved_at_utc=saved_at_utc,
        saved_at_prc=utc_aware_to_prc_aware(saved_at_utc),
        source=PRICE_SOURCE_BINANCE_WS_AGG_TRADE,
    )


def serialize_price_state(price_state: PriceState) -> str:
    """Serialize `PriceState` to a stable JSON value for Redis."""

    payload = {
        "symbol": price_state.symbol,
        "price": str(price_state.price),
        "source": price_state.source,
        "event_time_ms": price_state.event_time_ms,
        "trade_time_ms": price_state.trade_time_ms,
        "saved_at_utc": price_state.saved_at_utc.isoformat(),
        "saved_at_prc": price_state.saved_at_prc.isoformat(),
        "saved_at_utc_display": format_datetime_with_timezone(price_state.saved_at_utc),
        "saved_at_prc_display": format_datetime_with_timezone(price_state.saved_at_prc),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def save_current_price_state(
    redis_client: Any,
    *,
    key: str,
    price_state: PriceState,
    ttl_seconds: int,
) -> None:
    """Write current price state to Redis and refresh TTL.

    Parameters: `redis_client` is an injected Redis-compatible client; `key`
    is normally `bitcoin_price`; `ttl_seconds` is normally 120 seconds.
    Return value: none.
    Failure scenarios: invalid TTL or Redis driver failures raise explicit errors.
    External service access: writes to Redis through the injected client.
    Data impact: writes only the configured Redis key, no MySQL, no Hermes.
    """

    if ttl_seconds <= 0:
        raise ValidationError("PRICE_MONITOR_REDIS_TTL_SECONDS must be greater than 0")
    try:
        redis_client.set(key, serialize_price_state(price_state), ex=ttl_seconds)
    except Exception as exc:  # noqa: BLE001 - wrap Redis driver errors.
        raise RedisError(f"Redis price state write failed for key={key}: {exc}") from exc


def load_previous_price_state(redis_client: Any, *, key: str) -> PriceState | None:
    """Read previous price state from Redis.

    Parameters: `redis_client` is an injected Redis-compatible client; `key`
    is normally `bitcoin_price`.
    Return value: `PriceState` or `None` if the key does not exist.
    Failure scenarios: Redis driver errors raise `RedisError`; invalid JSON or
    invalid fields raise `PriceStateParseError`.
    External service access: reads Redis through the injected client.
    Data impact: no Redis writes, no MySQL writes, no Hermes sends.
    """

    try:
        raw_value = redis_client.get(key)
    except Exception as exc:  # noqa: BLE001 - wrap Redis driver errors.
        raise RedisError(f"Redis price state read failed for key={key}: {exc}") from exc
    if raw_value is None:
        return None
    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8", errors="replace")
    if not isinstance(raw_value, str):
        raise PriceStateParseError(f"Redis price state for key={key} must be a JSON string")
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise PriceStateParseError(f"Redis price state for key={key} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PriceStateParseError(f"Redis price state for key={key} must be a JSON object")
    return parse_price_state_payload(payload)


def parse_price_state_payload(payload: Mapping[str, Any]) -> PriceState:
    """Parse a Redis JSON payload into `PriceState` with Decimal price."""

    symbol = _required_str(payload, "symbol")
    source = _required_str(payload, "source")
    if source != PRICE_SOURCE_BINANCE_WS_AGG_TRADE:
        raise PriceStateParseError(f"Redis price state source must be {PRICE_SOURCE_BINANCE_WS_AGG_TRADE}")
    price = _parse_positive_decimal(_required_value(payload, "price"), "price")
    event_time_ms = _parse_non_negative_int(_required_value(payload, "event_time_ms"), "event_time_ms")
    trade_time_ms = _parse_non_negative_int(_required_value(payload, "trade_time_ms"), "trade_time_ms")
    saved_at_utc = _parse_datetime(_required_str(payload, "saved_at_utc"), "saved_at_utc")
    saved_at_prc = _parse_datetime(_required_str(payload, "saved_at_prc"), "saved_at_prc")
    return PriceState(
        symbol=symbol,
        price=price,
        event_time_ms=event_time_ms,
        trade_time_ms=trade_time_ms,
        saved_at_utc=saved_at_utc,
        saved_at_prc=saved_at_prc,
        source=source,
    )


def _required_value(payload: Mapping[str, Any], field_name: str) -> Any:
    if field_name not in payload:
        raise PriceStateParseError(f"Redis price state missing required field: {field_name}")
    return payload[field_name]


def _required_str(payload: Mapping[str, Any], field_name: str) -> str:
    value = _required_value(payload, field_name)
    if not isinstance(value, str) or not value.strip():
        raise PriceStateParseError(f"Redis price state field {field_name} must be a non-empty string")
    return value


def _parse_positive_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, float):
        raise PriceStateParseError(f"Redis price state field {field_name} must not be float")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PriceStateParseError(f"Redis price state field {field_name} must be Decimal-compatible") from exc
    if parsed <= 0:
        raise PriceStateParseError(f"Redis price state field {field_name} must be greater than 0")
    return parsed


def _parse_non_negative_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PriceStateParseError(f"Redis price state field {field_name} must be an integer") from exc
    if parsed < 0:
        raise PriceStateParseError(f"Redis price state field {field_name} must be non-negative")
    return parsed


def _parse_datetime(value: str, field_name: str):
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PriceStateParseError(f"Redis price state field {field_name} must be ISO datetime") from exc
    if parsed.tzinfo is None:
        raise PriceStateParseError(f"Redis price state field {field_name} must include timezone")
    return parsed
