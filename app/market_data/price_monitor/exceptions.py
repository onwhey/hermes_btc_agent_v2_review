"""Price monitor exception types.

This file belongs to `app/market_data/price_monitor`.
It defines explicit exceptions for WebSocket price event parsing, Redis state
parsing, and monitor request validation. It does not access Binance, MySQL,
Redis, Hermes, DeepSeek, REST latest-price endpoints, or trading capabilities.
"""

from __future__ import annotations

from app.core.exceptions import AppError, ValidationError


class PriceMonitorError(AppError):
    """Base error for the 10s WebSocket price monitor."""


class PriceEventParseError(PriceMonitorError):
    """Raised when a Binance aggTrade message cannot be parsed safely."""


class PriceStateParseError(PriceMonitorError):
    """Raised when Redis `bitcoin_price` state is malformed."""


class PriceMonitorValidationError(ValidationError):
    """Raised when a price monitor request violates phase-10 boundaries."""

