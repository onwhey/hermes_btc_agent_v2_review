"""Formal 1d Kline persistence helper for manual backfill.

This file belongs to `app/market_data/backfill`.
It wraps `MarketKline1dRepository.bulk_upsert` in a nested transaction when the
session supports savepoints, so formal 1d writes remain all-or-nothing. It does
not request Binance, send Hermes, write Redis, call DeepSeek, repair Klines,
overwrite conflicts, delete data, schedule jobs, or trade.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.market_data.backfill.exceptions import KlineBackfillPersistError
from app.market_data.kline_dto import MarketKlineDTO
from app.storage.mysql.repositories.market_kline_1d_repository import MarketKline1dRepository


def persist_1d_backfill_klines(
    db_session: Any,
    klines: Iterable[MarketKlineDTO],
    *,
    repository: Any | None = None,
) -> Any:
    """Persist writable 1d Klines through `bulk_upsert` with rollback protection."""

    active_repository = repository or MarketKline1dRepository()
    writable_klines = tuple(klines)
    try:
        if hasattr(db_session, "begin_nested"):
            with db_session.begin_nested():
                return active_repository.bulk_upsert(db_session, writable_klines)
        return active_repository.bulk_upsert(db_session, writable_klines)
    except Exception as exc:  # noqa: BLE001 - formal writes must be all-or-nothing.
        if hasattr(db_session, "rollback"):
            db_session.rollback()
        raise KlineBackfillPersistError(str(exc)) from exc
