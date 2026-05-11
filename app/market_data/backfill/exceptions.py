"""Exceptions for phase-08 manual 4h Kline backfill.

This file belongs to `app/market_data/backfill`.
It defines sanitized exceptions used by the manual backfill service and CLI.
It does not request Binance, read or write MySQL, read or write Redis, send
Hermes, call DeepSeek, repair Klines, or execute trades.
"""

from __future__ import annotations

from app.core.exceptions import KlineError


class KlineBackfillError(KlineError):
    """Base error for manual 4h Kline backfill orchestration."""


class KlineBackfillParameterError(KlineBackfillError):
    """Raised when CLI or service request parameters are invalid."""


class KlineBackfillBlockedError(KlineBackfillError):
    """Raised internally when quality rules block formal Kline writes."""


class KlineBackfillPersistError(KlineBackfillError):
    """Raised when formal Kline persistence fails and must be rolled back."""

