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
    ALLOWED_DATA_SOURCES,
    KLINE_4H_INTERVAL_MS,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
    TRIGGER_SOURCE_SCHEDULER,
    TRIGGER_SOURCE_TO_DATA_SOURCE,
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
    check_type: str = CHECK_TYPE_RECENT_KLINE_INTEGRITY,
    enforce_database_source_rules: bool = False,
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
    Daily phase-11 callers pass `check_type=daily_kline_integrity` and enable
    strict database-row checks. Those checks still only read MySQL and never repair data.
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
            check_type=check_type,
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
        check_type=check_type,
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
        server_time_ms=active_server_time_ms,
        enforce_database_source_rules=enforce_database_source_rules,
    )

    return build_quality_report(
        check_type=check_type,
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
            "enforce_database_source_rules": enforce_database_source_rules,
        },
    )


def _compare_official_klines_with_database_rows(
    *,
    official_klines: Iterable[MarketKlineDTO],
    database_rows: Iterable[Any],
    server_time_ms: int | None = None,
    enforce_database_source_rules: bool = False,
) -> tuple[KlineQualityIssue, ...]:
    official_by_open_time = {kline.open_time_ms: kline for kline in official_klines}
    database_row_list = list(database_rows)
    database_by_open_time: dict[int, Any] = {}
    issues: list[KlineQualityIssue] = []

    for row in database_row_list:
        open_time_ms = int(row.open_time_ms)
        if open_time_ms in database_by_open_time:
            issues.append(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.DUPLICATE_OPEN_TIME,
                    severity=KlineQualitySeverity.CRITICAL,
                    message=f"Database contains duplicate Kline rows open_time_ms={open_time_ms}",
                    open_time_ms=open_time_ms,
                    field_name="open_time_ms",
                )
            )
            continue
        database_by_open_time[open_time_ms] = row

    if enforce_database_source_rules:
        issues.extend(
            _check_strict_database_row_invariants(
                official_by_open_time=official_by_open_time,
                database_rows=database_row_list,
                server_time_ms=server_time_ms,
            )
        )

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


def _check_strict_database_row_invariants(
    *,
    official_by_open_time: dict[int, MarketKlineDTO],
    database_rows: Iterable[Any],
    server_time_ms: int | None,
) -> tuple[KlineQualityIssue, ...]:
    """Return daily-review-only database row invariant issues.

    The recent checker keeps this strict path opt-in so phase-07 tests and callers
    keep their original behavior. Phase 11 enables it to confirm persisted rows
    still obey the official 4h source and time-boundary rules. The function only
    inspects ORM/fake rows and never writes or repairs `market_kline_4h`.
    """

    issues: list[KlineQualityIssue] = []
    for row in database_rows:
        open_time_ms = int(row.open_time_ms)
        official_kline = official_by_open_time.get(open_time_ms)

        if official_kline is not None:
            if getattr(row, "symbol", None) != official_kline.symbol:
                issues.append(
                    KlineQualityIssue(
                        issue_type=KlineQualityIssueType.INVALID_KLINE,
                        severity=KlineQualitySeverity.ERROR,
                        message=(
                            "Database Kline symbol does not match official range; "
                            f"open_time_ms={open_time_ms}"
                        ),
                        open_time_ms=open_time_ms,
                        field_name="symbol",
                        expected_value=official_kline.symbol,
                        actual_value=str(getattr(row, "symbol", "")),
                    )
                )
            if getattr(row, "interval_value", None) != official_kline.interval_value:
                issues.append(
                    KlineQualityIssue(
                        issue_type=KlineQualityIssueType.INVALID_KLINE,
                        severity=KlineQualitySeverity.ERROR,
                        message=(
                            "Database Kline interval does not match official 4h range; "
                            f"open_time_ms={open_time_ms}"
                        ),
                        open_time_ms=open_time_ms,
                        field_name="interval_value",
                        expected_value=official_kline.interval_value,
                        actual_value=str(getattr(row, "interval_value", "")),
                    )
                )

        data_source = str(getattr(row, "data_source", "") or "")
        trigger_source = str(getattr(row, "trigger_source", "") or "")
        expected_data_source = TRIGGER_SOURCE_TO_DATA_SOURCE.get(trigger_source)
        if data_source not in ALLOWED_DATA_SOURCES or expected_data_source != data_source:
            issues.append(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.INVALID_DATA_SOURCE_MAPPING,
                    severity=KlineQualitySeverity.ERROR,
                    message=(
                        "Database Kline data_source/trigger_source mapping is not an allowed "
                        f"Binance REST official mapping; open_time_ms={open_time_ms}"
                    ),
                    open_time_ms=open_time_ms,
                    field_name="data_source,trigger_source",
                    expected_value=str(expected_data_source or sorted(ALLOWED_DATA_SOURCES)),
                    actual_value=f"{data_source},{trigger_source}",
                )
            )

        close_time_ms = int(getattr(row, "close_time_ms"))
        if server_time_ms is not None and close_time_ms >= server_time_ms:
            issues.append(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.UNCLOSED_KLINE,
                    severity=KlineQualitySeverity.CRITICAL,
                    message=f"Database contains an unclosed Kline open_time_ms={open_time_ms}",
                    open_time_ms=open_time_ms,
                    field_name="close_time_ms",
                    expected_value=f"<{server_time_ms}",
                    actual_value=str(close_time_ms),
                )
            )

        expected_close_time_ms = open_time_ms + KLINE_4H_INTERVAL_MS - 1
        if close_time_ms != expected_close_time_ms:
            issues.append(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.INVALID_KLINE,
                    severity=KlineQualitySeverity.ERROR,
                    message=(
                        "Database Kline close_time_ms does not match the 4h open/close "
                        f"boundary rule; open_time_ms={open_time_ms}"
                    ),
                    open_time_ms=open_time_ms,
                    field_name="close_time_ms",
                    expected_value=str(expected_close_time_ms),
                    actual_value=str(close_time_ms),
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
