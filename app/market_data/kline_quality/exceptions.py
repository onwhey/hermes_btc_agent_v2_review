"""Exception aliases for the Kline quality module.

This file belongs to `app/market_data/kline_quality`.
It re-exports phase-07 Kline quality exceptions from `app/core/exceptions.py` so
callers can import them from the module boundary without duplicating definitions.
It does not request Binance, read or write MySQL, read or write Redis, send Hermes,
call DeepSeek, alter Klines, or perform trading execution.
"""

from __future__ import annotations

from app.core.exceptions import (
    KlineContinuityError,
    KlineDataMismatchError,
    KlineIntegrityCheckError,
    KlineQualityError,
    KlineUnclosedError,
)

__all__ = [
    "KlineContinuityError",
    "KlineDataMismatchError",
    "KlineIntegrityCheckError",
    "KlineQualityError",
    "KlineUnclosedError",
]
