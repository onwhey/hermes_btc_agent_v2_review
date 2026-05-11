from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.core.exceptions import KlineConflictError, KlineParseError, KlineValidationError
from app.core.time_utils import timestamp_ms_to_utc_datetime, utc_aware_to_prc_aware
from app.market_data.kline_constants import (
    DATA_SOURCE_BINANCE_REST_BY_CLI,
    DATA_SOURCE_BINANCE_REST_BY_SCHEDULER,
    KLINE_4H_INTERVAL_MS,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
    TRIGGER_SOURCE_SCHEDULER,
)
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_parser import calculate_raw_payload_hash, parse_binance_kline
from app.market_data.kline_validator import validate_market_kline
from app.storage.mysql.models.market_kline_4h import MarketKline4h
from app.storage.mysql.repositories import market_kline_4h_repository
from app.storage.mysql.repositories.market_kline_4h_repository import (
    MarketKline4hRepository,
    find_conflicting_core_fields,
)
from scripts.check_market_kline_4h import collect_market_kline_4h_errors


RAW_KLINE = [
    1_700_006_400_000,
    "35000.10000000",
    "36000.20000000",
    "34000.30000000",
    "35500.40000000",
    "123.45600000",
    1_700_020_799_999,
    "4567890.12300000",
    9876,
    "66.70000000",
    "2345678.90000000",
    "0",
]


def build_valid_dto() -> MarketKlineDTO:
    return parse_binance_kline(
        RAW_KLINE,
        symbol="btcusdt",
        interval_value=KLINE_4H_INTERVAL_VALUE,
        trigger_source=TRIGGER_SOURCE_CLI,
    )


def build_dto_with_consistent_times(
    dto: MarketKlineDTO,
    *,
    open_time_ms: int | None = None,
    close_time_ms: int | None = None,
) -> MarketKlineDTO:
    open_ms = dto.open_time_ms if open_time_ms is None else open_time_ms
    close_ms = dto.close_time_ms if close_time_ms is None else close_time_ms
    open_utc = timestamp_ms_to_utc_datetime(open_ms)
    close_utc = timestamp_ms_to_utc_datetime(close_ms)
    return replace(
        dto,
        open_time_ms=open_ms,
        close_time_ms=close_ms,
        open_time_utc=open_utc,
        close_time_utc=close_utc,
        open_time_prc=utc_aware_to_prc_aware(open_utc),
        close_time_prc=utc_aware_to_prc_aware(close_utc),
    )


def test_market_kline_dto_can_be_constructed() -> None:
    dto = build_valid_dto()

    assert isinstance(dto, MarketKlineDTO)
    assert dto.symbol == "BTCUSDT"
    assert dto.interval_value == "4h"


def test_parser_maps_binance_fields_decimal_time_and_data_source() -> None:
    dto = build_valid_dto()

    assert dto.open_time_ms == 1_700_006_400_000
    assert dto.close_time_ms == 1_700_020_799_999
    assert dto.open_price == Decimal("35000.10000000")
    assert dto.high_price == Decimal("36000.20000000")
    assert dto.low_price == Decimal("34000.30000000")
    assert dto.close_price == Decimal("35500.40000000")
    assert dto.volume == Decimal("123.45600000")
    assert dto.quote_volume == Decimal("4567890.12300000")
    assert dto.trade_count == 9876
    assert dto.taker_buy_base_volume == Decimal("66.70000000")
    assert dto.taker_buy_quote_volume == Decimal("2345678.90000000")
    assert dto.open_time_utc.isoformat() == "2023-11-15T00:00:00+00:00"
    assert dto.open_time_prc.isoformat() == "2023-11-15T08:00:00+08:00"
    assert dto.data_source == DATA_SOURCE_BINANCE_REST_BY_CLI
    assert dto.raw_payload_hash == calculate_raw_payload_hash(RAW_KLINE)


def test_parser_maps_scheduler_trigger_source() -> None:
    dto = parse_binance_kline(
        RAW_KLINE,
        symbol="BTCUSDT",
        interval_value="4h",
        trigger_source=TRIGGER_SOURCE_SCHEDULER,
    )

    assert dto.data_source == DATA_SOURCE_BINANCE_REST_BY_SCHEDULER


def test_parser_rejects_short_raw_rows_and_invalid_decimal() -> None:
    with pytest.raises(KlineParseError):
        parse_binance_kline(
            RAW_KLINE[:6],
            symbol="BTCUSDT",
            interval_value="4h",
            trigger_source=TRIGGER_SOURCE_CLI,
        )

    invalid_decimal = list(RAW_KLINE)
    invalid_decimal[1] = "not-a-decimal"
    with pytest.raises(KlineParseError):
        parse_binance_kline(
            invalid_decimal,
            symbol="BTCUSDT",
            interval_value="4h",
            trigger_source=TRIGGER_SOURCE_CLI,
        )


def test_validator_accepts_valid_kline_and_rejects_ohlc_errors() -> None:
    dto = build_valid_dto()

    assert validate_market_kline(dto) is dto

    with pytest.raises(KlineValidationError):
        validate_market_kline(replace(dto, high_price=Decimal("35000")))

    with pytest.raises(KlineValidationError):
        validate_market_kline(replace(dto, low_price=Decimal("35600")))


def test_validator_rejects_source_mapping_errors() -> None:
    dto = build_valid_dto()

    with pytest.raises(KlineValidationError):
        validate_market_kline(replace(dto, trigger_source="unknown"))

    with pytest.raises(KlineValidationError):
        validate_market_kline(replace(dto, data_source=DATA_SOURCE_BINANCE_REST_BY_SCHEDULER))


def test_validator_rejects_close_time_ms_not_matching_4h_interval() -> None:
    dto = build_valid_dto()
    invalid = build_dto_with_consistent_times(
        dto,
        close_time_ms=dto.open_time_ms + KLINE_4H_INTERVAL_MS,
    )

    with pytest.raises(KlineValidationError):
        validate_market_kline(invalid)


def test_validator_rejects_open_time_ms_not_on_utc_4h_boundary() -> None:
    dto = build_valid_dto()
    shifted_open_time_ms = dto.open_time_ms + 1
    invalid = build_dto_with_consistent_times(
        dto,
        open_time_ms=shifted_open_time_ms,
        close_time_ms=shifted_open_time_ms + KLINE_4H_INTERVAL_MS - 1,
    )

    with pytest.raises(KlineValidationError):
        validate_market_kline(invalid)


def test_validator_rejects_open_time_utc_mismatching_open_time_ms() -> None:
    dto = build_valid_dto()
    invalid = replace(
        dto,
        open_time_utc=timestamp_ms_to_utc_datetime(dto.open_time_ms + 1),
    )

    with pytest.raises(KlineValidationError):
        validate_market_kline(invalid)


def test_validator_rejects_close_time_utc_mismatching_close_time_ms() -> None:
    dto = build_valid_dto()
    invalid = replace(
        dto,
        close_time_utc=timestamp_ms_to_utc_datetime(dto.close_time_ms + 1),
    )

    with pytest.raises(KlineValidationError):
        validate_market_kline(invalid)


def test_validator_rejects_open_time_prc_not_converted_from_open_time_utc() -> None:
    dto = build_valid_dto()
    invalid = replace(
        dto,
        open_time_prc=utc_aware_to_prc_aware(timestamp_ms_to_utc_datetime(dto.open_time_ms + 1)),
    )

    with pytest.raises(KlineValidationError):
        validate_market_kline(invalid)


def test_validator_rejects_close_time_prc_not_converted_from_close_time_utc() -> None:
    dto = build_valid_dto()
    invalid = replace(
        dto,
        close_time_prc=utc_aware_to_prc_aware(timestamp_ms_to_utc_datetime(dto.close_time_ms + 1)),
    )

    with pytest.raises(KlineValidationError):
        validate_market_kline(invalid)


def test_model_and_repository_can_be_imported() -> None:
    assert MarketKline4h.__name__ == "MarketKline4h"
    assert MarketKline4hRepository.__name__ == "MarketKline4hRepository"


def test_repository_detects_core_field_conflict_without_overwrite() -> None:
    dto = build_valid_dto()
    existing = MarketKline4h(
        symbol=dto.symbol,
        interval_value=dto.interval_value,
        open_time_ms=dto.open_time_ms,
        open_price=dto.open_price,
        high_price=dto.high_price,
        low_price=dto.low_price,
        close_price=Decimal("1"),
        volume=dto.volume,
        quote_volume=dto.quote_volume,
        trade_count=dto.trade_count,
        taker_buy_base_volume=dto.taker_buy_base_volume,
        taker_buy_quote_volume=dto.taker_buy_quote_volume,
        close_time_ms=dto.close_time_ms,
    )

    assert find_conflicting_core_fields(existing, dto) == ["close_price"]


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


def test_repository_bulk_upsert_inserts_missing_rows_and_skips_identical_rows() -> None:
    dto = build_valid_dto()
    repository = MarketKline4hRepository()

    insert_session = _FakeSession(existing=None)
    insert_result = repository.bulk_upsert(insert_session, [dto])

    assert insert_result.input_count == 1
    assert insert_result.inserted_count == 1
    assert insert_result.skipped_count == 0
    assert len(insert_session.added) == 1
    assert insert_session.flushed is True

    existing = insert_session.added[0]
    skip_session = _FakeSession(existing=existing)
    skip_result = repository.bulk_upsert(skip_session, [dto])

    assert skip_result.inserted_count == 0
    assert skip_result.skipped_count == 1
    assert skip_session.added == []


def test_repository_bulk_upsert_rejects_conflicts() -> None:
    dto = build_valid_dto()
    existing = MarketKline4h(
        symbol=dto.symbol,
        interval_value=dto.interval_value,
        open_time_ms=dto.open_time_ms,
        open_price=dto.open_price,
        high_price=dto.high_price,
        low_price=dto.low_price,
        close_price=Decimal("1"),
        volume=dto.volume,
        quote_volume=dto.quote_volume,
        trade_count=dto.trade_count,
        taker_buy_base_volume=dto.taker_buy_base_volume,
        taker_buy_quote_volume=dto.taker_buy_quote_volume,
        close_time_ms=dto.close_time_ms,
    )

    with pytest.raises(KlineConflictError):
        MarketKline4hRepository().bulk_upsert(_FakeSession(existing=existing), [dto])


def test_migration_only_creates_market_kline_4h_table() -> None:
    migration_path = Path("migrations/versions/20260511_06_create_market_kline_4h.py")
    text = migration_path.read_text(encoding="utf-8")

    assert '"market_kline_4h"' in text
    assert '"collector_event_log"' not in text
    assert '"data_quality_check"' not in text
    assert '"alert_message"' not in text
    assert "op.create_table(" in text
    assert "uq_market_kline_4h_symbol_interval_open_time_ms" in text


def test_market_kline_modules_do_not_import_external_or_alerting_layers() -> None:
    modules = [
        "app.market_data.kline_parser",
        "app.market_data.kline_validator",
        "app.storage.mysql.repositories.market_kline_4h_repository",
        "scripts.check_market_kline_4h",
    ]
    forbidden_imports = (
        "app.exchange.binance.rest_client",
        "app.alerting",
        "app.storage.redis",
        "app.scheduler",
    )

    for module_name in modules:
        module = __import__(module_name, fromlist=["_"])
        imported_names = _imported_module_names(inspect.getsource(module))
        for forbidden in forbidden_imports:
            assert forbidden not in imported_names

    source = inspect.getsource(market_kline_4h_repository)
    assert "BinanceRestClient" not in source
    assert "get_klines" not in source


def test_check_market_kline_4h_script_is_pure_local_check() -> None:
    assert collect_market_kline_4h_errors() == []


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
