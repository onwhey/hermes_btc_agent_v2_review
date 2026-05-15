"""Repository for the formal BTCUSDT 1d Kline table.

This file belongs to `app/storage/mysql/repositories`.
It reads and writes only `market_kline_1d` through a caller-provided session.
It is called by tests and later stage-14 1d backfill or collector services.
It does not request Binance, create sessions, commit transactions, read/write Redis,
send Hermes, call large language models, repair Kline data, or execute trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, TYPE_CHECKING

from app.core.exceptions import KlineConflictError, KlineValidationError
from app.core.time_utils import now_utc, timestamp_ms_to_utc_datetime, utc_aware_to_prc_aware
from app.market_data.kline_constants import (
    DEFAULT_EXCHANGE,
    DEFAULT_MARKET_TYPE,
    KLINE_1D_INTERVAL_MS,
    KLINE_1D_INTERVAL_VALUE,
    TRIGGER_SOURCE_TO_DATA_SOURCE,
)
from app.storage.mysql.models.market_kline_1d import MarketKline1d

if TYPE_CHECKING:
    from datetime import datetime

    from app.market_data.kline_dto import MarketKlineDTO

try:
    from sqlalchemy import func, select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    func = select = None  # type: ignore[assignment]


CORE_CONFLICT_FIELDS = (
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "close_time_ms",
)

ZERO = Decimal("0")


@dataclass(frozen=True)
class Kline1dUpsertResult:
    """Summary returned by `bulk_upsert`.

    Parameters: counts describe inserts and idempotent skips in one repository call.
    Return value: immutable summary object.
    Failure scenarios: conflicts or validation failures raise before a summary is returned.
    External service access and data impact: this object itself has none.
    """

    input_count: int
    inserted_count: int
    skipped_count: int


class MarketKline1dRepository:
    """Data access helper for `market_kline_1d`.

    Parameters: none; callers pass sessions into each method.
    Return value: repository instance.
    Failure scenarios: database errors propagate to the caller; validation failures raise
    `KlineValidationError`; conflicts raise `KlineConflictError`.
    External service access: none.
    Data impact: methods may read/write `market_kline_1d` only when a session is passed.
    """

    def get_by_open_time(
        self,
        db_session: Any,
        *,
        symbol: str,
        open_time_ms: int,
    ) -> MarketKline1d | None:
        """Return one 1d Kline by `symbol + interval_value + open_time_ms`.

        Parameters: caller-provided session plus `symbol` and `open_time_ms`.
        Return value: ORM row or `None`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads `market_kline_1d`, does not write or commit.
        """

        _require_sqlalchemy()
        stmt = (
            select(MarketKline1d)
            .where(MarketKline1d.symbol == symbol)
            .where(MarketKline1d.interval_value == KLINE_1D_INTERVAL_VALUE)
            .where(MarketKline1d.open_time_ms == open_time_ms)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_latest(
        self,
        db_session: Any,
        *,
        symbol: str,
    ) -> MarketKline1d | None:
        """Return the latest 1d Kline ordered by `open_time_ms`.

        Parameters: caller-provided session plus target `symbol`.
        Return value: ORM row or `None`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only `market_kline_1d`; it never sorts by database id.
        """

        _require_sqlalchemy()
        stmt = (
            select(MarketKline1d)
            .where(MarketKline1d.symbol == symbol)
            .where(MarketKline1d.interval_value == KLINE_1D_INTERVAL_VALUE)
            .order_by(MarketKline1d.open_time_ms.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_previous_before(
        self,
        db_session: Any,
        *,
        symbol: str,
        open_time_ms: int,
    ) -> MarketKline1d | None:
        """Return the nearest 1d Kline before an open-time boundary.

        Parameters: caller-provided session, `symbol`, and exclusive `open_time_ms`.
        Return value: the previous row or `None`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only `market_kline_1d`; used by manual backfill context checks.
        """

        _require_sqlalchemy()
        stmt = (
            select(MarketKline1d)
            .where(MarketKline1d.symbol == symbol)
            .where(MarketKline1d.interval_value == KLINE_1D_INTERVAL_VALUE)
            .where(MarketKline1d.open_time_ms < open_time_ms)
            .order_by(MarketKline1d.open_time_ms.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_next_after(
        self,
        db_session: Any,
        *,
        symbol: str,
        open_time_ms: int,
    ) -> MarketKline1d | None:
        """Return the nearest 1d Kline after an open-time boundary.

        Parameters: caller-provided session, `symbol`, and exclusive `open_time_ms`.
        Return value: the next row or `None`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only `market_kline_1d`; used by manual backfill context checks.
        """

        _require_sqlalchemy()
        stmt = (
            select(MarketKline1d)
            .where(MarketKline1d.symbol == symbol)
            .where(MarketKline1d.interval_value == KLINE_1D_INTERVAL_VALUE)
            .where(MarketKline1d.open_time_ms > open_time_ms)
            .order_by(MarketKline1d.open_time_ms.asc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def list_recent(
        self,
        db_session: Any,
        *,
        symbol: str,
        limit: int,
        ascending: bool = True,
    ) -> list[MarketKline1d]:
        """Return recent 1d Klines for one symbol.

        Parameters: caller-provided session, `symbol`, positive `limit`, and output ordering.
        Return value: rows ordered ascending by default, or descending when requested.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only and uses UTC open time for ordering.
        """

        _require_sqlalchemy()
        if limit <= 0:
            return []
        stmt = (
            select(MarketKline1d)
            .where(MarketKline1d.symbol == symbol)
            .where(MarketKline1d.interval_value == KLINE_1D_INTERVAL_VALUE)
            .order_by(MarketKline1d.open_time_utc.desc())
            .limit(limit)
        )
        rows = list(db_session.execute(stmt).scalars().all())
        if ascending:
            return list(reversed(rows))
        return rows

    def list_by_time_range(
        self,
        db_session: Any,
        *,
        symbol: str,
        start_open_time_ms: int,
        end_open_time_ms: int,
    ) -> list[MarketKline1d]:
        """List 1d Klines within an inclusive UTC millisecond open-time range.

        Parameters: caller-provided session, `symbol`, and inclusive open-time bounds.
        Return value: rows ordered by `open_time_ms`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only; PRC fields are not used for ordering.
        """

        _require_sqlalchemy()
        stmt = (
            select(MarketKline1d)
            .where(MarketKline1d.symbol == symbol)
            .where(MarketKline1d.interval_value == KLINE_1D_INTERVAL_VALUE)
            .where(MarketKline1d.open_time_ms >= start_open_time_ms)
            .where(MarketKline1d.open_time_ms <= end_open_time_ms)
            .order_by(MarketKline1d.open_time_ms.asc())
        )
        return list(db_session.execute(stmt).scalars().all())

    def list_by_open_times(
        self,
        db_session: Any,
        *,
        symbol: str,
        open_time_ms_list: Iterable[int],
    ) -> list[MarketKline1d]:
        """List 1d Klines for explicit open-time millisecond values.

        Parameters: caller-provided session, `symbol`, and an open-time iterable.
        Return value: rows ordered by `open_time_ms`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only and does not repair missing rows.
        """

        _require_sqlalchemy()
        open_times = list(open_time_ms_list)
        if not open_times:
            return []
        stmt = (
            select(MarketKline1d)
            .where(MarketKline1d.symbol == symbol)
            .where(MarketKline1d.interval_value == KLINE_1D_INTERVAL_VALUE)
            .where(MarketKline1d.open_time_ms.in_(open_times))
            .order_by(MarketKline1d.open_time_ms.asc())
        )
        return list(db_session.execute(stmt).scalars().all())

    def count_by_time_range(
        self,
        db_session: Any,
        *,
        symbol: str,
        start_open_time_ms: int,
        end_open_time_ms: int,
    ) -> int:
        """Count 1d Klines in an inclusive UTC millisecond open-time range.

        Parameters: caller-provided session, `symbol`, and inclusive open-time bounds.
        Return value: integer row count.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only.
        """

        _require_sqlalchemy()
        stmt = (
            select(func.count())
            .select_from(MarketKline1d)
            .where(MarketKline1d.symbol == symbol)
            .where(MarketKline1d.interval_value == KLINE_1D_INTERVAL_VALUE)
            .where(MarketKline1d.open_time_ms >= start_open_time_ms)
            .where(MarketKline1d.open_time_ms <= end_open_time_ms)
        )
        return int(db_session.execute(stmt).scalar_one())

    def bulk_upsert(
        self,
        db_session: Any,
        klines: Iterable["MarketKlineDTO"],
    ) -> Kline1dUpsertResult:
        """Insert missing 1d Klines and reject core-field conflicts.

        Parameters: caller-provided session plus parsed 1d DTOs.
        Return value: `Kline1dUpsertResult` with inserted and idempotently skipped counts.
        Failure scenarios: validation failures or existing-row core conflicts raise.
        External service access: none.
        Data impact: may add rows to `market_kline_1d`, never commits, never overwrites
        conflicting existing rows, and never deletes or repairs formal Kline data.
        """

        input_klines = list(klines)
        inserted_count = 0
        skipped_count = 0

        for kline in input_klines:
            validate_market_kline_1d(kline)
            existing = self.get_by_open_time(
                db_session,
                symbol=kline.symbol,
                open_time_ms=kline.open_time_ms,
            )
            if existing is None:
                db_session.add(_model_from_dto(kline))
                inserted_count += 1
                continue

            conflict_fields = find_conflicting_1d_core_fields(existing, kline)
            if conflict_fields:
                fields = ", ".join(conflict_fields)
                raise KlineConflictError(
                    "market_kline_1d conflict for "
                    f"symbol={kline.symbol}, interval_value={kline.interval_value}, "
                    f"open_time_ms={kline.open_time_ms}; fields={fields}"
                )
            skipped_count += 1

        if hasattr(db_session, "flush"):
            db_session.flush()
        return Kline1dUpsertResult(
            input_count=len(input_klines),
            inserted_count=inserted_count,
            skipped_count=skipped_count,
        )


def validate_market_kline_1d(kline: "MarketKlineDTO") -> None:
    """Validate the minimal 1d persistence boundary before writing the formal table.

    Parameters: one parsed DTO intended for `market_kline_1d`.
    Return value: `None` when valid.
    Failure scenarios: raises `KlineValidationError` for interval, time-boundary,
    source-mapping, and core OHLCV defects.
    External service access: none.
    Data impact: this function is read-only and never repairs input data.
    """

    if not kline.symbol:
        raise KlineValidationError("1d Kline symbol must not be empty")
    if kline.interval_value != KLINE_1D_INTERVAL_VALUE:
        raise KlineValidationError(
            f"1d repository only accepts interval_value={KLINE_1D_INTERVAL_VALUE}"
        )

    expected_close_time_ms = kline.open_time_ms + KLINE_1D_INTERVAL_MS - 1
    if kline.close_time_ms != expected_close_time_ms:
        raise KlineValidationError("1d close_time_ms must equal open_time_ms + 86400000 - 1")
    if kline.open_time_ms % KLINE_1D_INTERVAL_MS != 0:
        raise KlineValidationError("1d open_time_ms must align to UTC 00:00:00")

    _assert_datetime_matches_ms("open_time_utc", kline.open_time_utc, kline.open_time_ms)
    _assert_datetime_matches_ms("close_time_utc", kline.close_time_utc, kline.close_time_ms)
    _assert_prc_matches_utc("open_time_prc", kline.open_time_utc, kline.open_time_prc)
    _assert_prc_matches_utc("close_time_prc", kline.close_time_utc, kline.close_time_prc)

    if kline.open_time_utc >= kline.close_time_utc:
        raise KlineValidationError("1d open_time_utc must be before close_time_utc")
    if kline.open_price <= ZERO or kline.high_price <= ZERO or kline.low_price <= ZERO:
        raise KlineValidationError("1d open/high/low prices must be positive")
    if kline.close_price <= ZERO:
        raise KlineValidationError("1d close_price must be positive")
    if kline.high_price < max(kline.open_price, kline.close_price, kline.low_price):
        raise KlineValidationError("1d high_price is lower than one OHLC component")
    if kline.low_price > min(kline.open_price, kline.close_price, kline.high_price):
        raise KlineValidationError("1d low_price is higher than one OHLC component")
    if kline.volume < ZERO or kline.quote_volume < ZERO:
        raise KlineValidationError("1d volume fields must not be negative")
    if kline.taker_buy_base_volume < ZERO or kline.taker_buy_quote_volume < ZERO:
        raise KlineValidationError("1d taker-buy volume fields must not be negative")
    if kline.trade_count < 0:
        raise KlineValidationError("1d trade_count must not be negative")

    expected_data_source = TRIGGER_SOURCE_TO_DATA_SOURCE.get(kline.trigger_source)
    if expected_data_source is None:
        raise KlineValidationError(f"unsupported 1d trigger_source={kline.trigger_source}")
    if kline.data_source != expected_data_source:
        raise KlineValidationError(
            "1d data_source must match trigger_source mapping: "
            f"trigger_source={kline.trigger_source}, data_source={kline.data_source}"
        )


def find_conflicting_1d_core_fields(existing: MarketKline1d, incoming: "MarketKlineDTO") -> list[str]:
    """Return core fields that differ for the same 1d Kline unique key.

    Parameters: `existing` is the database row; `incoming` is the parsed DTO.
    Return value: list of conflicting field names.
    Failure scenarios: missing attributes propagate as `AttributeError`.
    External service access and data impact: none.
    """

    conflicts: list[str] = []
    for field_name in CORE_CONFLICT_FIELDS:
        if getattr(existing, field_name) != getattr(incoming, field_name):
            conflicts.append(field_name)
    return conflicts


def create_default_market_kline_1d_repository() -> MarketKline1dRepository:
    """Create the default stage-14 1d Kline repository object.

    Parameters: none.
    Return value: repository instance.
    Failure scenarios: none expected.
    External service access and data impact: none.
    """

    return MarketKline1dRepository()


def _model_from_dto(kline: "MarketKlineDTO") -> MarketKline1d:
    now = now_utc()
    now_prc = utc_aware_to_prc_aware(now)
    return MarketKline1d(
        exchange=DEFAULT_EXCHANGE,
        market_type=DEFAULT_MARKET_TYPE,
        symbol=kline.symbol,
        interval_value=kline.interval_value,
        open_time_ms=kline.open_time_ms,
        open_time_utc=kline.open_time_utc,
        open_time_prc=kline.open_time_prc,
        close_time_ms=kline.close_time_ms,
        close_time_utc=kline.close_time_utc,
        close_time_prc=kline.close_time_prc,
        open_price=kline.open_price,
        high_price=kline.high_price,
        low_price=kline.low_price,
        close_price=kline.close_price,
        volume=kline.volume,
        quote_volume=kline.quote_volume,
        trade_count=kline.trade_count,
        taker_buy_base_volume=kline.taker_buy_base_volume,
        taker_buy_quote_volume=kline.taker_buy_quote_volume,
        data_source=kline.data_source,
        trigger_source=kline.trigger_source,
        raw_payload_json=kline.raw_payload_json,
        raw_payload_hash=kline.raw_payload_hash,
        created_at_utc=now,
        created_at_prc=now_prc,
        updated_at_utc=now,
        updated_at_prc=now_prc,
    )


def _assert_datetime_matches_ms(field_name: str, value: "datetime", timestamp_ms: int) -> None:
    expected_value = timestamp_ms_to_utc_datetime(timestamp_ms)
    if value != expected_value:
        raise KlineValidationError(f"1d {field_name} must match its millisecond timestamp")


def _assert_prc_matches_utc(field_name: str, utc_value: "datetime", prc_value: "datetime") -> None:
    expected_value = utc_aware_to_prc_aware(utc_value)
    if prc_value != expected_value:
        raise KlineValidationError(f"1d {field_name} must be converted by time_utils")


def _require_sqlalchemy() -> None:
    if select is None or func is None:
        raise RuntimeError("SQLAlchemy is required for MarketKline1dRepository queries")
