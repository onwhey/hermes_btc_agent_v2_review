"""Recent 4h Kline integrity checker.

This file belongs to `app/market_data/kline_quality`.
It compares official Binance REST Kline rows supplied by an injected or default
client with existing `market_kline_4h` rows. It records differences in a report.
It is called by the quality service and manual script.
It does not write formal Kline rows, write Redis, send Hermes by itself, call
DeepSeek, repair Klines, backfill gaps, overwrite conflicts, delete rows, or trade.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol

from app.core.exceptions import KlineIntegrityCheckError
from app.exchange.binance.rest_client import BinanceRestClient
from app.market_data.kline_constants import (
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
    TRIGGER_SOURCE_SCHEDULER,
)
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_parser import parse_binance_klines
from app.market_data.kline_quality.batch_checker import check_kline_batch_before_persist
from app.market_data.kline_quality.types import (
    CHECK_TRIGGER_SOURCE_SCHEDULER,
    CHECK_TYPE_RECENT_KLINE_INTEGRITY,
    KlineQualityIssue,
    KlineQualityIssueType,
    KlineQualityReport,
    KlineQualitySeverity,
    build_quality_report,
)
from app.storage.mysql.repositories.market_kline_4h_repository import (
    MarketKline4hRepository,
    find_conflicting_core_fields,
)


class KlineIntegrityReaderProtocol(Protocol):
    """Read-only repository shape required by recent integrity checks."""

    def list_by_time_range(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        start_open_time_ms: int,
        end_open_time_ms: int,
    ) -> list[Any]:
        ...


class BinanceKlineClientProtocol(Protocol):
    """Public Kline client shape required by recent integrity checks."""

    def get_server_time(self) -> Any:
        ...

    def get_klines(
        self,
        *,
        symbol: str | None = None,
        interval: str | None = None,
        limit: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[Any]:
        ...


def run_recent_kline_integrity_check(
    db_session: Any,
    *,
    symbol: str,
    interval_value: str = KLINE_4H_INTERVAL_VALUE,
    limit: int,
    check_trigger_source: str,
    binance_client: BinanceKlineClientProtocol | None = None,
    repository: KlineIntegrityReaderProtocol | None = None,
    server_time_ms: int | None = None,
) -> KlineQualityReport:
    """Compare recent official Klines with existing database rows.

    Parameters: caller-owned session, symbol, interval, limit, explicit quality
    check trigger source, and injectable client/repository for tests.
    Return value: report describing missing rows, extra rows, mismatches, or too few
    closed Klines after filtering.
    Failure scenarios: client, parser, or database exceptions propagate as explicit failures.
    External service access: calls Binance only when no fake client is supplied and this method
    is explicitly invoked.
    Data impact: reads `market_kline_4h`; never writes formal Kline rows.
    """

    if limit <= 0:
        raise KlineIntegrityCheckError("recent Kline integrity limit must be greater than 0")

    active_client = binance_client or BinanceRestClient()
    active_repository = repository or MarketKline4hRepository()
    active_server_time_ms = server_time_ms
    if active_server_time_ms is None:
        active_server_time_ms = _extract_server_time_ms(active_client.get_server_time())

    requested_limit = limit + 1
    raw_klines = active_client.get_klines(symbol=symbol, interval=interval_value, limit=requested_limit)
    parser_trigger_source = _parser_trigger_source_from_check_trigger(check_trigger_source)
    parsed_klines = tuple(
        parse_binance_klines(
            raw_klines,
            symbol=symbol,
            interval_value=interval_value,
            trigger_source=parser_trigger_source,
        )
    )
    closed_klines = tuple(
        kline for kline in parsed_klines if _is_closed_by_server_time(kline, active_server_time_ms)
    )
    if len(closed_klines) < limit:
        return build_quality_report(
            check_type=CHECK_TYPE_RECENT_KLINE_INTEGRITY,
            klines=closed_klines,
            issues=(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.INSUFFICIENT_CLOSED_KLINES,
                    severity=KlineQualitySeverity.ERROR,
                    message=(
                        "Not enough closed Klines for recent integrity check; "
                        f"requested_closed_count={limit}, fetched_count={len(parsed_klines)}, "
                        f"closed_count={len(closed_klines)}, server_time_ms={active_server_time_ms}"
                    ),
                    expected_value=str(limit),
                    actual_value=str(len(closed_klines)),
                    field_name="closed_kline_count",
                ),
            ),
            check_trigger_source=check_trigger_source,
            writable_klines=(),
            metadata={
                "requested_closed_count": limit,
                "requested_binance_limit": requested_limit,
                "fetched_count": len(parsed_klines),
                "closed_count": len(closed_klines),
                "filtered_unclosed_count": len(parsed_klines) - len(closed_klines),
            },
        )

    official_klines = closed_klines[-limit:]

    batch_report = check_kline_batch_before_persist(
        official_klines,
        server_time_ms=active_server_time_ms,
        check_type=CHECK_TYPE_RECENT_KLINE_INTEGRITY,
        check_trigger_source=check_trigger_source,
    )
    if not batch_report.passed or not official_klines:
        return batch_report

    start_open_time_ms = official_klines[0].open_time_ms
    end_open_time_ms = official_klines[-1].open_time_ms
    database_rows = active_repository.list_by_time_range(
        db_session,
        symbol=symbol,
        interval_value=interval_value,
        start_open_time_ms=start_open_time_ms,
        end_open_time_ms=end_open_time_ms,
    )
    issues = _compare_official_klines_with_database_rows(
        official_klines=official_klines,
        database_rows=database_rows,
    )

    return build_quality_report(
        check_type=CHECK_TYPE_RECENT_KLINE_INTEGRITY,
        klines=official_klines,
        issues=issues,
        check_trigger_source=check_trigger_source,
        existing_open_time_ms=tuple(sorted(int(row.open_time_ms) for row in database_rows)),
        writable_klines=(),
        metadata={
            "official_count": len(official_klines),
            "database_count": len(database_rows),
            "requested_binance_limit": requested_limit,
            "filtered_unclosed_count": len(parsed_klines) - len(closed_klines),
        },
    )


def _compare_official_klines_with_database_rows(
    *,
    official_klines: Iterable[MarketKlineDTO],
    database_rows: Iterable[Any],
) -> tuple[KlineQualityIssue, ...]:
    official_by_open_time = {kline.open_time_ms: kline for kline in official_klines}
    database_by_open_time = {int(row.open_time_ms): row for row in database_rows}
    issues: list[KlineQualityIssue] = []

    for open_time_ms, official_kline in official_by_open_time.items():
        database_row = database_by_open_time.get(open_time_ms)
        if database_row is None:
            issues.append(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.MISSING_IN_DATABASE,
                    severity=KlineQualitySeverity.ERROR,
                    message=f"Database is missing official Kline open_time_ms={open_time_ms}",
                    open_time_ms=open_time_ms,
                )
            )
            continue

        conflict_fields = find_conflicting_core_fields(database_row, official_kline)
        if conflict_fields:
            issues.append(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.DATABASE_FIELD_MISMATCH,
                    severity=KlineQualitySeverity.CRITICAL,
                    message=(
                        "Database Kline differs from official Kline; "
                        f"open_time_ms={open_time_ms}, fields={','.join(conflict_fields)}"
                    ),
                    open_time_ms=open_time_ms,
                    field_name=",".join(conflict_fields),
                )
            )

    for open_time_ms in sorted(set(database_by_open_time) - set(official_by_open_time)):
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.EXTRA_IN_DATABASE,
                severity=KlineQualitySeverity.WARNING,
                message=f"Database has a row not returned by official recent range open_time_ms={open_time_ms}",
                open_time_ms=open_time_ms,
            )
        )

    return tuple(issues)


def _extract_server_time_ms(server_time_response: Any) -> int:
    value = getattr(server_time_response, "server_time_ms", None)
    if isinstance(value, int):
        return value
    if isinstance(server_time_response, dict) and isinstance(server_time_response.get("serverTime"), int):
        return int(server_time_response["serverTime"])
    if isinstance(server_time_response, int):
        return server_time_response
    raise KlineIntegrityCheckError("Binance server time response does not contain server_time_ms")


def _is_closed_by_server_time(kline: MarketKlineDTO, server_time_ms: int) -> bool:
    return kline.close_time_ms < server_time_ms


def _parser_trigger_source_from_check_trigger(check_trigger_source: str) -> str:
    if check_trigger_source == CHECK_TRIGGER_SOURCE_SCHEDULER:
        return TRIGGER_SOURCE_SCHEDULER
    return TRIGGER_SOURCE_CLI
