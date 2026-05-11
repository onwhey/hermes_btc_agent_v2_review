"""Database-context quality checks for 4h Kline batches.

This file belongs to `app/market_data/kline_quality`.
It compares a parsed batch with existing `market_kline_4h` rows supplied by a
repository or explicit test fixtures. It checks latest-row continuity, duplicate
existing rows, and core-field conflicts before later code writes anything.
It is called by the phase-07 service and tests.
It does not request Binance, write formal Kline rows, write Redis, send Hermes,
call DeepSeek, repair Klines, overwrite conflicts, delete rows, or trade.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol

from app.market_data.kline_constants import KLINE_4H_INTERVAL_MS
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.rules import validate_single_kline_as_quality_issue
from app.market_data.kline_quality.types import (
    CHECK_TRIGGER_SOURCE_SERVICE,
    CHECK_TYPE_DATABASE_CONTEXT,
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


class MarketKlineReaderProtocol(Protocol):
    """Read-only repository shape required by phase-07 database checks."""

    def get_latest(self, db_session: Any, *, symbol: str, interval_value: str) -> Any | None:
        ...

    def list_by_open_times(
        self,
        db_session: Any,
        *,
        symbol: str,
        interval_value: str,
        open_time_ms_list: Iterable[int],
    ) -> list[Any]:
        ...


def check_kline_batch_with_database_context(
    klines: Iterable[MarketKlineDTO],
    *,
    existing_db_klines: Iterable[Any] = (),
    latest_db_kline: Any | None = None,
    check_trigger_source: str = CHECK_TRIGGER_SOURCE_SERVICE,
) -> KlineQualityReport:
    """Check a batch against explicit existing-row context.

    Parameters: `klines` are parsed DTOs; `existing_db_klines` are rows with the
    same unique keys that already exist; `latest_db_kline` is the latest database row.
    Return value: report whose `writable_klines` excludes identical existing rows.
    Failure scenarios: invalid DTOs, latest gaps, or field conflicts become report issues.
    External service access: none.
    Data impact: reads no database by itself and never writes formal Kline rows.
    """

    batch = tuple(klines)
    issues: list[KlineQualityIssue] = []

    if not batch:
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.EMPTY_BATCH,
                severity=KlineQualitySeverity.ERROR,
                message="Kline batch must not be empty before database comparison",
            )
        )
        return build_quality_report(
            check_type=CHECK_TYPE_DATABASE_CONTEXT,
            klines=batch,
            issues=issues,
            check_trigger_source=check_trigger_source,
            writable_klines=(),
        )

    for kline in batch:
        validation_issue = validate_single_kline_as_quality_issue(kline)
        if validation_issue is not None:
            issues.append(validation_issue)

    existing_by_open_time = {
        int(existing.open_time_ms): existing
        for existing in existing_db_klines
        if getattr(existing, "open_time_ms", None) is not None
    }
    if latest_db_kline is not None:
        latest_open_time_ms = getattr(latest_db_kline, "open_time_ms", None)
        if latest_open_time_ms in {kline.open_time_ms for kline in batch}:
            existing_by_open_time.setdefault(int(latest_open_time_ms), latest_db_kline)

    existing_open_time_ms: list[int] = []
    new_klines: list[MarketKlineDTO] = []

    for kline in batch:
        existing = existing_by_open_time.get(kline.open_time_ms)
        if existing is None:
            new_klines.append(kline)
            continue

        conflict_fields = find_conflicting_core_fields(existing, kline)
        if conflict_fields:
            issues.append(_database_conflict_issue(kline, conflict_fields))
            continue
        existing_open_time_ms.append(kline.open_time_ms)

    if latest_db_kline is not None and new_klines:
        latest_open_time_ms = int(latest_db_kline.open_time_ms)
        first_new = min(new_klines, key=lambda item: item.open_time_ms)
        expected_first_new = latest_open_time_ms + KLINE_4H_INTERVAL_MS
        if first_new.open_time_ms != expected_first_new:
            issues.append(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.DATABASE_NOT_CONTINUOUS,
                    severity=KlineQualitySeverity.ERROR,
                    message=(
                        "First new Kline must be continuous with latest database Kline; "
                        f"latest={latest_open_time_ms}, expected={expected_first_new}, "
                        f"actual={first_new.open_time_ms}"
                    ),
                    open_time_ms=first_new.open_time_ms,
                    previous_open_time_ms=latest_open_time_ms,
                    next_open_time_ms=first_new.open_time_ms,
                    field_name="open_time_ms",
                    expected_value=str(expected_first_new),
                    actual_value=str(first_new.open_time_ms),
                )
            )

    return build_quality_report(
        check_type=CHECK_TYPE_DATABASE_CONTEXT,
        klines=batch,
        issues=issues,
        check_trigger_source=check_trigger_source,
        existing_open_time_ms=tuple(sorted(existing_open_time_ms)),
        writable_klines=tuple(new_klines) if not issues else (),
        metadata={
            "existing_identical_count": len(existing_open_time_ms),
            "new_kline_count": len(new_klines),
        },
    )


def check_kline_batch_against_database(
    db_session: Any,
    klines: Iterable[MarketKlineDTO],
    *,
    repository: MarketKlineReaderProtocol | None = None,
    latest_db_kline: Any | None = None,
    check_trigger_source: str = CHECK_TRIGGER_SOURCE_SERVICE,
) -> KlineQualityReport:
    """Read existing rows and check a batch before later persistence.

    Parameters: `db_session` is caller-owned; `repository` must provide read-only
    latest and list-by-open-time methods; `klines` are parsed DTOs.
    Return value: quality report with duplicates filtered and conflicts reported.
    Failure scenarios: database read errors propagate to the caller.
    External service access: none.
    Data impact: reads `market_kline_4h`; never writes or commits.
    """

    batch = tuple(klines)
    if not batch:
        return check_kline_batch_with_database_context(
            batch,
            check_trigger_source=check_trigger_source,
        )

    active_repository = repository or MarketKline4hRepository()
    symbol = batch[0].symbol
    interval_value = batch[0].interval_value
    latest = latest_db_kline
    if latest is None:
        latest = active_repository.get_latest(
            db_session,
            symbol=symbol,
            interval_value=interval_value,
        )
    existing_rows = active_repository.list_by_open_times(
        db_session,
        symbol=symbol,
        interval_value=interval_value,
        open_time_ms_list=[kline.open_time_ms for kline in batch],
    )
    return check_kline_batch_with_database_context(
        batch,
        existing_db_klines=existing_rows,
        latest_db_kline=latest,
        check_trigger_source=check_trigger_source,
    )


def _database_conflict_issue(kline: MarketKlineDTO, conflict_fields: Iterable[str]) -> KlineQualityIssue:
    fields = tuple(conflict_fields)
    return KlineQualityIssue(
        issue_type=KlineQualityIssueType.DATABASE_CONFLICT,
        severity=KlineQualitySeverity.CRITICAL,
        message=(
            "Existing database Kline conflicts with incoming official Kline; "
            f"open_time_ms={kline.open_time_ms}, fields={','.join(fields)}"
        ),
        open_time_ms=kline.open_time_ms,
        field_name=",".join(fields),
    )
