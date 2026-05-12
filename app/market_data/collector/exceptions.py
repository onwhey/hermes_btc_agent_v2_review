"""Exceptions for phase-09 4h Kline incremental collection.

This file belongs to `app/market_data/collector`.
It defines service-level errors only. It does not request Binance, write MySQL,
write Redis, send Hermes, call DeepSeek, repair Klines, or execute trades.
"""

from __future__ import annotations


class KlineCollectError(Exception):
    """Base exception for incremental collector failures."""


class KlineCollectParameterError(KlineCollectError):
    """Raised when a collector request has invalid local parameters."""


class KlineCollectPersistError(KlineCollectError):
    """Raised when formal Kline persistence fails all-or-nothing."""

