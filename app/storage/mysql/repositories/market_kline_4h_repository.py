"""Repository for the formal BTCUSDT 4h Kline table.

This file belongs to `app/storage/mysql/repositories`.
It reads and writes only `market_kline_4h` through a caller-provided session.
It is called by tests, the phase-06 check script for import validation, and later services.
It does not request Binance, create sessions, commit transactions, read/write Redis,
send Hermes, call large language models, repair Kline data, or execute trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, TYPE_CHECKING

from app.core.exceptions import KlineConflictError
from app.core.time_utils import now_utc, utc_aware_to_prc_aware
from app.market_data.kline_constants import DEFAULT_EXCHANGE, DEFAULT_MARKET_TYPE
from app.market_data.kline_validator import validate_market_kline
from app.storage.mysql.models.market_kline_4h import MarketKline4h

if TYPE_CHECKING:
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


@dataclass(frozen=True)
class KlineUpsertResult:
    """Summary returned by `bulk_upsert`.

    Parameters: counts describe inserts and idempotent skips in one repository call.
    Return value: immutable summary object.
    Failure scenarios: conflicts raise before a summary is returned.
    External service access and data impact: this object itself has none.
    """

    input_count: int
    inserted_count: int
    skipped_count: int


class MarketKline4hRepository:
    """Data access helper for `market_kline_4h`.

    Parameters: none; callers pass sessions into each method.
    Return value: repository instance.
    Failure scenarios: database errors propagate to the caller; conflicts raise
    `KlineConflictError`.
    External service access: none.
    Data impact: methods may read/write `market_kline_4h` only when a session is passed.
    """

    def get_by_open_time(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        open_time_ms: int,
    ) -> MarketKline4h | None:
        """Return one Kline by the phase-06 unique key.

        Parameters: caller-provided session plus `symbol`, `interval_value`, `open_time_ms`.
        Return value: ORM row or `None`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads `market_kline_4h`, does not write or commit.
        """

        _require_sqlalchemy()
        stmt = (
            select(MarketKline4h)
            .where(MarketKline4h.symbol == symbol)
            .where(MarketKline4h.interval_value == interval_value)
            .where(MarketKline4h.open_time_ms == open_time_ms)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_latest(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
    ) -> MarketKline4h | None:
        """Return the latest Kline ordered by `open_time_ms`.

        Parameters: caller-provided session plus target `symbol` and `interval_value`.
        Return value: ORM row or `None`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only `market_kline_4h`; it never sorts by database id.
        """

        _require_sqlalchemy()
        stmt = (
            select(MarketKline4h)
            .where(MarketKline4h.symbol == symbol)
            .where(MarketKline4h.interval_value == interval_value)
            .order_by(MarketKline4h.open_time_ms.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_previous_before(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        open_time_ms: int,
    ) -> MarketKline4h | None:
        """Return the nearest Kline before an open-time boundary.

        Parameters: caller-provided session, identity fields, and exclusive
        `open_time_ms` boundary.
        Return value: the previous row or `None`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only `market_kline_4h`; used by phase-08 historical
        backfill to check the left neighbor without relying on latest-row logic.
        """

        _require_sqlalchemy()
        stmt = (
            select(MarketKline4h)
            .where(MarketKline4h.symbol == symbol)
            .where(MarketKline4h.interval_value == interval_value)
            .where(MarketKline4h.open_time_ms < open_time_ms)
            .order_by(MarketKline4h.open_time_ms.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def get_next_after(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        open_time_ms: int,
    ) -> MarketKline4h | None:
        """Return the nearest Kline after an open-time boundary.

        Parameters: caller-provided session, identity fields, and exclusive
        `open_time_ms` boundary.
        Return value: the next row or `None`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only `market_kline_4h`; used by phase-08 historical
        backfill to check the right neighbor before any formal write.
        """

        _require_sqlalchemy()
        stmt = (
            select(MarketKline4h)
            .where(MarketKline4h.symbol == symbol)
            .where(MarketKline4h.interval_value == interval_value)
            .where(MarketKline4h.open_time_ms > open_time_ms)
            .order_by(MarketKline4h.open_time_ms.asc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def list_by_time_range(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        start_open_time_ms: int,
        end_open_time_ms: int,
    ) -> list[MarketKline4h]:
        """List Klines within an inclusive UTC millisecond open-time range.

        Parameters: caller-provided session, identity fields, and inclusive bounds.
        Return value: rows ordered by `open_time_ms`.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only; PRC fields are not used for ordering.
        """

        _require_sqlalchemy()
        stmt = (
            select(MarketKline4h)
            .where(MarketKline4h.symbol == symbol)
            .where(MarketKline4h.interval_value == interval_value)
            .where(MarketKline4h.open_time_ms >= start_open_time_ms)
            .where(MarketKline4h.open_time_ms <= end_open_time_ms)
            .order_by(MarketKline4h.open_time_ms.asc())
        )
        return list(db_session.execute(stmt).scalars().all())

    def list_by_open_times(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        open_time_ms_list: Iterable[int],
    ) -> list[MarketKline4h]:
        """List Klines for explicit open-time millisecond values.

        Parameters: caller-provided session, identity fields, and an open-time iterable.
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
            select(MarketKline4h)
            .where(MarketKline4h.symbol == symbol)
            .where(MarketKline4h.interval_value == interval_value)
            .where(MarketKline4h.open_time_ms.in_(open_times))
            .order_by(MarketKline4h.open_time_ms.asc())
        )
        return list(db_session.execute(stmt).scalars().all())

    def count_by_time_range(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        start_open_time_ms: int,
        end_open_time_ms: int,
    ) -> int:
        """Count Klines in an inclusive UTC millisecond open-time range.

        Parameters: caller-provided session, identity fields, and inclusive bounds.
        Return value: integer row count.
        Failure scenarios: database execution errors propagate.
        External service access: none.
        Data impact: reads only.
        """

        _require_sqlalchemy()
        stmt = (
            select(func.count())
            .select_from(MarketKline4h)
            .where(MarketKline4h.symbol == symbol)
            .where(MarketKline4h.interval_value == interval_value)
            .where(MarketKline4h.open_time_ms >= start_open_time_ms)
            .where(MarketKline4h.open_time_ms <= end_open_time_ms)
        )
        return int(db_session.execute(stmt).scalar_one())

    def bulk_upsert(
        self,
        db_session: Any,
        klines: Iterable["MarketKlineDTO"],
    ) -> KlineUpsertResult:
        """Insert missing Klines and reject core-field conflicts.

        Parameters: caller-provided session plus validated or parse-ready DTOs.
        Return value: `KlineUpsertResult` with inserted and idempotently skipped counts.
        Failure scenarios: validation failures or existing-row core conflicts raise.
        External service access: none.
        Data impact: may add rows to `market_kline_4h`, never commits, never overwrites
        conflicting existing rows, and never deletes or repairs formal Kline data.
        """

        input_klines = list(klines)
        inserted_count = 0
        skipped_count = 0

        for kline in input_klines:
            validate_market_kline(kline)
            existing = self.get_by_open_time(
                db_session,
                symbol=kline.symbol,
                interval_value=kline.interval_value,
                open_time_ms=kline.open_time_ms,
            )
            if existing is None:
                db_session.add(_model_from_dto(kline))
                inserted_count += 1
                continue

            conflict_fields = find_conflicting_core_fields(existing, kline)
            if conflict_fields:
                fields = ", ".join(conflict_fields)
                raise KlineConflictError(
                    "market_kline_4h conflict for "
                    f"symbol={kline.symbol}, interval_value={kline.interval_value}, "
                    f"open_time_ms={kline.open_time_ms}; fields={fields}"
                )
            skipped_count += 1

        if hasattr(db_session, "flush"):
            db_session.flush()
        return KlineUpsertResult(
            input_count=len(input_klines),
            inserted_count=inserted_count,
            skipped_count=skipped_count,
        )


def find_conflicting_core_fields(existing: MarketKline4h, incoming: "MarketKlineDTO") -> list[str]:
    """Return core fields that differ for the same Kline unique key.

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


def create_default_market_kline_4h_repository() -> MarketKline4hRepository:
    """Create the default phase-06 Kline repository object.

    Parameters: none.
    Return value: repository instance.
    Failure scenarios: none expected.
    External service access and data impact: none.
    """

    return MarketKline4hRepository()


def _model_from_dto(kline: "MarketKlineDTO") -> MarketKline4h:
    now = now_utc()
    now_prc = utc_aware_to_prc_aware(now)
    return MarketKline4h(
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


def _require_sqlalchemy() -> None:
    if select is None or func is None:
        raise RuntimeError("SQLAlchemy is required for MarketKline4hRepository queries")

