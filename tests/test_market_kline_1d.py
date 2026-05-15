from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.core.exceptions import KlineConflictError, KlineValidationError
from app.core.time_utils import now_utc, timestamp_ms_to_utc_datetime, utc_aware_to_prc_aware
from app.market_data.kline_constants import (
    DATA_SOURCE_BINANCE_REST_BY_CLI,
    KLINE_1D_INTERVAL_MS,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_MS,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_parser import parse_binance_kline
from app.storage.mysql.base import Base
from app.storage.mysql.models.market_kline_1d import MarketKline1d
from app.storage.mysql.models.market_kline_4h import MarketKline4h
from app.storage.mysql.repositories import market_kline_1d_repository
from app.storage.mysql.repositories.market_kline_1d_repository import (
    MarketKline1dRepository,
    find_conflicting_1d_core_fields,
)

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, inspect as inspect_engine  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


BASE_OPEN_TIME_MS = 1_700_006_400_000

RAW_1D_KLINE = [
    BASE_OPEN_TIME_MS,
    "35000.10000000",
    "36000.20000000",
    "34000.30000000",
    "35500.40000000",
    "123.45600000",
    BASE_OPEN_TIME_MS + KLINE_1D_INTERVAL_MS - 1,
    "4567890.12300000",
    9876,
    "66.70000000",
    "2345678.90000000",
    "0",
]


def build_1d_dto(*, day_offset: int = 0, close_price: str = "35500.40000000") -> MarketKlineDTO:
    open_time_ms = BASE_OPEN_TIME_MS + day_offset * KLINE_1D_INTERVAL_MS
    raw = list(RAW_1D_KLINE)
    raw[0] = open_time_ms
    raw[4] = close_price
    raw[6] = open_time_ms + KLINE_1D_INTERVAL_MS - 1
    return parse_binance_kline(
        raw,
        symbol="btcusdt",
        interval_value=KLINE_1D_INTERVAL_VALUE,
        trigger_source=TRIGGER_SOURCE_CLI,
    )


def build_4h_dto() -> MarketKlineDTO:
    raw = list(RAW_1D_KLINE)
    raw[6] = raw[0] + KLINE_4H_INTERVAL_MS - 1
    return parse_binance_kline(
        raw,
        symbol="btcusdt",
        interval_value=KLINE_4H_INTERVAL_VALUE,
        trigger_source=TRIGGER_SOURCE_CLI,
    )


@pytest.fixture
def sqlite_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    MarketKline1d.__table__.create(engine)
    MarketKline4h.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_market_kline_1d_metadata_defines_independent_table() -> None:
    engine = create_engine("sqlite:///:memory:")
    MarketKline1d.__table__.create(engine)
    MarketKline4h.__table__.create(engine)

    table_names = set(inspect_engine(engine).get_table_names())

    assert MarketKline1d.__tablename__ == "market_kline_1d"
    assert MarketKline4h.__tablename__ == "market_kline_4h"
    assert MarketKline1d.__tablename__ != MarketKline4h.__tablename__
    assert "market_kline_1d" in Base.metadata.tables
    assert "market_kline_4h" in Base.metadata.tables
    assert table_names == {"market_kline_1d", "market_kline_4h"}
    engine.dispose()


def test_market_kline_1d_migration_creates_only_1d_table() -> None:
    migration_path = Path("migrations/versions/20260516_14_create_market_kline_1d.py")
    text = migration_path.read_text(encoding="utf-8")

    assert '"market_kline_1d"' in text
    assert "uq_market_kline_1d_symbol_interval_open_time_ms" in text
    assert "op.create_table(" in text
    assert 'op.create_table(\n        "market_kline_4h"' not in text
    assert 'op.add_column("market_kline_4h"' not in text
    assert 'op.drop_column("market_kline_4h"' not in text


def test_market_kline_1d_unique_constraint_prevents_duplicate_open_time(sqlite_session: Session) -> None:
    first = _model_from_dto(build_1d_dto(), row_id=1)
    duplicate = _model_from_dto(build_1d_dto(close_price="35600.40000000"), row_id=2)

    sqlite_session.add(first)
    sqlite_session.commit()
    sqlite_session.add(duplicate)
    with pytest.raises(IntegrityError):
        sqlite_session.commit()
    sqlite_session.rollback()


def test_1d_and_4h_tables_are_isolated(sqlite_session: Session) -> None:
    sqlite_session.add(_model_from_dto(build_1d_dto(), row_id=1))
    sqlite_session.add(_model_from_dto(build_4h_dto(), row_id=1, model_class=MarketKline4h))
    sqlite_session.commit()

    assert sqlite_session.query(MarketKline1d).count() == 1
    assert sqlite_session.query(MarketKline4h).count() == 1
    assert sqlite_session.query(MarketKline1d).one().interval_value == KLINE_1D_INTERVAL_VALUE
    assert sqlite_session.query(MarketKline4h).one().interval_value == KLINE_4H_INTERVAL_VALUE


def test_repository_queries_latest_and_recent_1d_klines(sqlite_session: Session) -> None:
    repository = MarketKline1dRepository()
    for index in range(3):
        sqlite_session.add(_model_from_dto(build_1d_dto(day_offset=index), row_id=index + 1))
    sqlite_session.commit()

    latest = repository.get_latest(sqlite_session, symbol="BTCUSDT")
    recent_ascending = repository.list_recent(sqlite_session, symbol="BTCUSDT", limit=2)
    recent_descending = repository.list_recent(sqlite_session, symbol="BTCUSDT", limit=2, ascending=False)

    assert latest is not None
    assert latest.open_time_ms == BASE_OPEN_TIME_MS + 2 * KLINE_1D_INTERVAL_MS
    assert [row.open_time_ms for row in recent_ascending] == [
        BASE_OPEN_TIME_MS + KLINE_1D_INTERVAL_MS,
        BASE_OPEN_TIME_MS + 2 * KLINE_1D_INTERVAL_MS,
    ]
    assert [row.open_time_ms for row in recent_descending] == [
        BASE_OPEN_TIME_MS + 2 * KLINE_1D_INTERVAL_MS,
        BASE_OPEN_TIME_MS + KLINE_1D_INTERVAL_MS,
    ]


def test_repository_bulk_upsert_inserts_1d_rows_and_skips_identical_rows() -> None:
    dto = build_1d_dto()
    repository = MarketKline1dRepository()

    insert_session = _FakeSession(existing=None)
    insert_result = repository.bulk_upsert(insert_session, [dto])

    assert insert_result.input_count == 1
    assert insert_result.inserted_count == 1
    assert insert_result.skipped_count == 0
    assert len(insert_session.added) == 1
    assert isinstance(insert_session.added[0], MarketKline1d)
    assert not isinstance(insert_session.added[0], MarketKline4h)
    assert insert_session.added[0].interval_value == KLINE_1D_INTERVAL_VALUE
    assert insert_session.flushed is True

    skip_session = _FakeSession(existing=insert_session.added[0])
    skip_result = repository.bulk_upsert(skip_session, [dto])

    assert skip_result.inserted_count == 0
    assert skip_result.skipped_count == 1
    assert skip_session.added == []


def test_repository_bulk_upsert_rejects_conflicts_without_overwrite() -> None:
    dto = build_1d_dto()
    existing = _model_from_dto(dto, row_id=1)
    existing.close_price = Decimal("1")

    assert find_conflicting_1d_core_fields(existing, dto) == ["close_price"]
    with pytest.raises(KlineConflictError):
        MarketKline1dRepository().bulk_upsert(_FakeSession(existing=existing), [dto])


def test_repository_rejects_4h_dto_and_does_not_write_1d_table() -> None:
    invalid = build_4h_dto()
    fake_session = _FakeSession(existing=None)

    with pytest.raises(KlineValidationError):
        MarketKline1dRepository().bulk_upsert(fake_session, [invalid])

    assert fake_session.added == []
    assert fake_session.flushed is False


def test_repository_requires_1d_open_time_on_utc_midnight_boundary() -> None:
    dto = build_1d_dto()
    shifted_open_time_ms = dto.open_time_ms + 1
    shifted_open_utc = timestamp_ms_to_utc_datetime(shifted_open_time_ms)
    invalid = replace(
        dto,
        open_time_ms=shifted_open_time_ms,
        close_time_ms=shifted_open_time_ms + KLINE_1D_INTERVAL_MS - 1,
        open_time_utc=shifted_open_utc,
        open_time_prc=utc_aware_to_prc_aware(shifted_open_utc),
        close_time_utc=timestamp_ms_to_utc_datetime(shifted_open_time_ms + KLINE_1D_INTERVAL_MS - 1),
        close_time_prc=utc_aware_to_prc_aware(
            timestamp_ms_to_utc_datetime(shifted_open_time_ms + KLINE_1D_INTERVAL_MS - 1)
        ),
    )

    with pytest.raises(KlineValidationError):
        MarketKline1dRepository().bulk_upsert(_FakeSession(existing=None), [invalid])


def test_market_kline_1d_repository_does_not_import_external_alerting_or_scheduler_layers() -> None:
    imported_names = _imported_module_names(inspect.getsource(market_kline_1d_repository))
    forbidden_imports = (
        "app.exchange.binance.rest_client",
        "app.alerting",
        "app.storage.redis",
        "app.scheduler",
    )

    for forbidden in forbidden_imports:
        assert forbidden not in imported_names

    source = inspect.getsource(market_kline_1d_repository)
    assert "BinanceRestClient" not in source
    assert "get_klines" not in source


class _ScalarOneOrNoneResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    def __init__(self, existing: Any = None) -> None:
        self.existing = existing
        self.added: list[Any] = []
        self.flushed = False

    def execute(self, _statement: Any) -> _ScalarOneOrNoneResult:
        return _ScalarOneOrNoneResult(self.existing)

    def add(self, record: Any) -> None:
        self.added.append(record)

    def flush(self) -> None:
        self.flushed = True


def _model_from_dto(
    dto: MarketKlineDTO,
    *,
    row_id: int,
    model_class: type[MarketKline1d] | type[MarketKline4h] = MarketKline1d,
) -> MarketKline1d | MarketKline4h:
    now = now_utc()
    now_prc = utc_aware_to_prc_aware(now)
    return model_class(
        id=row_id,
        symbol=dto.symbol,
        interval_value=dto.interval_value,
        open_time_ms=dto.open_time_ms,
        open_time_utc=dto.open_time_utc,
        open_time_prc=dto.open_time_prc,
        close_time_ms=dto.close_time_ms,
        close_time_utc=dto.close_time_utc,
        close_time_prc=dto.close_time_prc,
        open_price=dto.open_price,
        high_price=dto.high_price,
        low_price=dto.low_price,
        close_price=dto.close_price,
        volume=dto.volume,
        quote_volume=dto.quote_volume,
        trade_count=dto.trade_count,
        taker_buy_base_volume=dto.taker_buy_base_volume,
        taker_buy_quote_volume=dto.taker_buy_quote_volume,
        data_source=DATA_SOURCE_BINANCE_REST_BY_CLI,
        trigger_source=TRIGGER_SOURCE_CLI,
        raw_payload_json=dto.raw_payload_json,
        raw_payload_hash=dto.raw_payload_hash,
        created_at_utc=now,
        created_at_prc=now_prc,
        updated_at_utc=now,
        updated_at_prc=now_prc,
    )


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names
