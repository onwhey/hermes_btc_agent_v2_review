"""Quality checks for phase-09 incremental 4h Kline collection.

This file belongs to `app/market_data/collector`.
It reuses phase-07 batch validation and adds database context checks for the
recent closed Kline window. It reads only through caller-supplied repositories.
It does not write formal Klines, write Redis, send Hermes, call DeepSeek, repair
data, schedule jobs, or trade.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.market_data.collector.types import IncrementalKlineCollectRequest
from app.market_data.kline_constants import KLINE_4H_INTERVAL_MS
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.batch_checker import check_kline_batch_before_persist
from app.market_data.kline_quality.report_formatter import format_quality_report_summary
from app.market_data.kline_quality.types import (
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


def check_incremental_collect_quality(
    db_session: Any,
    klines: Iterable[MarketKlineDTO],
    *,
    request: IncrementalKlineCollectRequest,
    server_time_ms: int,
    repository: Any | None = None,
) -> KlineQualityReport:
    """Run phase-07 batch checks plus database-neighbor checks for collection."""

    batch = tuple(klines)
    batch_report = check_kline_batch_before_persist(
        batch,
        server_time_ms=server_time_ms,
        check_trigger_source=request.trigger_source,
    )
    if not batch_report.passed:
        return batch_report
    return _check_incremental_database_context(
        db_session,
        batch,
        request=request,
        repository=repository,
    )


def event_values_from_collect_report(
    request: IncrementalKlineCollectRequest,
    report: KlineQualityReport,
    *,
    fetched_count: int,
    parsed_count: int,
    closed_count: int,
    filtered_unclosed_count: int,
    quality_check_id: int | None = None,
) -> dict[str, Any]:
    """Build `collector_event_log` values from an incremental quality report."""

    first_issue = report.first_issue
    return {
        "fetched_count": fetched_count,
        "parsed_count": parsed_count,
        "closed_count": closed_count,
        "filtered_unclosed_count": filtered_unclosed_count,
        "issue_count": report.issue_count,
        "actual_start_open_time_ms": report.start_open_time_ms,
        "actual_end_open_time_ms": report.end_open_time_ms,
        "quality_check_id": quality_check_id,
        "first_issue_type": first_issue.issue_type.value if first_issue else None,
        "first_issue_message": first_issue.message if first_issue else None,
        "conflict_count": quality_issue_count(report, KlineQualityIssueType.DATABASE_CONFLICT),
        "skipped_count": len(report.existing_open_time_ms),
        "details": {
            "fetch_mode": "recent_closed_klines",
            "requested_closed_limit": request.limit,
            "dry_run": request.dry_run,
            "quality_summary": format_quality_report_summary(report),
        },
    }


def quality_issue_count(report: KlineQualityReport, issue_type: KlineQualityIssueType) -> int:
    """Count issues of one type in a report."""

    return sum(1 for issue in report.issues if issue.issue_type == issue_type)


def _check_incremental_database_context(
    db_session: Any,
    batch: tuple[MarketKlineDTO, ...],
    *,
    request: IncrementalKlineCollectRequest,
    repository: Any | None,
) -> KlineQualityReport:
    active_repository = repository or MarketKline4hRepository()
    issues: list[KlineQualityIssue] = []
    existing_rows = active_repository.list_by_open_times(
        db_session,
        symbol=request.symbol,
        interval_value=request.interval_value,
        open_time_ms_list=[kline.open_time_ms for kline in batch],
    )
    existing_by_open_time = {int(row.open_time_ms): row for row in existing_rows}
    existing_identical: list[int] = []
    writable_klines: list[MarketKlineDTO] = []

    for kline in batch:
        existing = existing_by_open_time.get(kline.open_time_ms)
        if existing is None:
            writable_klines.append(kline)
            continue
        conflict_fields = find_conflicting_core_fields(existing, kline)
        if conflict_fields:
            issues.append(_database_conflict_issue(kline, conflict_fields))
        else:
            existing_identical.append(kline.open_time_ms)

    first = batch[0]
    last = batch[-1]
    previous_row = _get_previous_before(active_repository, db_session, request, first.open_time_ms)
    next_row = _get_next_after(active_repository, db_session, request, last.open_time_ms)
    _append_neighbor_issues(issues, first, last, previous_row=previous_row, next_row=next_row)

    return build_quality_report(
        check_type=CHECK_TYPE_DATABASE_CONTEXT,
        klines=batch,
        issues=issues,
        check_trigger_source=request.trigger_source,
        existing_open_time_ms=tuple(sorted(existing_identical)),
        writable_klines=tuple(writable_klines) if not issues else (),
        metadata={
            "fetch_mode": "recent_closed_klines",
            "requested_closed_limit": request.limit,
            "existing_identical_count": len(existing_identical),
            "new_kline_count": len(writable_klines),
            "previous_open_time_ms": int(previous_row.open_time_ms) if previous_row is not None else None,
            "next_open_time_ms": int(next_row.open_time_ms) if next_row is not None else None,
        },
    )


def _append_neighbor_issues(
    issues: list[KlineQualityIssue],
    first: MarketKlineDTO,
    last: MarketKlineDTO,
    *,
    previous_row: Any | None,
    next_row: Any | None,
) -> None:
    if previous_row is not None:
        previous_open_time_ms = int(previous_row.open_time_ms)
        expected_first = previous_open_time_ms + KLINE_4H_INTERVAL_MS
        if first.open_time_ms != expected_first:
            issues.append(
                _database_not_continuous_issue(
                    message=(
                        "Incremental first Kline does not connect to previous database Kline; "
                        f"previous={previous_open_time_ms}, expected={expected_first}, actual={first.open_time_ms}"
                    ),
                    open_time_ms=first.open_time_ms,
                    previous_open_time_ms=previous_open_time_ms,
                    next_open_time_ms=first.open_time_ms,
                    expected_value=str(expected_first),
                    actual_value=str(first.open_time_ms),
                )
            )
    if next_row is not None:
        next_open_time_ms = int(next_row.open_time_ms)
        expected_next = last.open_time_ms + KLINE_4H_INTERVAL_MS
        if next_open_time_ms != expected_next:
            issues.append(
                _database_not_continuous_issue(
                    message=(
                        "Incremental last Kline does not connect to next database Kline; "
                        f"last={last.open_time_ms}, expected={expected_next}, actual={next_open_time_ms}"
                    ),
                    open_time_ms=last.open_time_ms,
                    previous_open_time_ms=last.open_time_ms,
                    next_open_time_ms=next_open_time_ms,
                    expected_value=str(expected_next),
                    actual_value=str(next_open_time_ms),
                )
            )


def _database_conflict_issue(kline: MarketKlineDTO, conflict_fields: Iterable[str]) -> KlineQualityIssue:
    fields = tuple(conflict_fields)
    return KlineQualityIssue(
        issue_type=KlineQualityIssueType.DATABASE_CONFLICT,
        severity=KlineQualitySeverity.CRITICAL,
        message=(
            "Existing database Kline conflicts with official incremental Kline; "
            f"open_time_ms={kline.open_time_ms}, fields={','.join(fields)}"
        ),
        open_time_ms=kline.open_time_ms,
        field_name=",".join(fields),
    )


def _database_not_continuous_issue(
    *,
    message: str,
    open_time_ms: int,
    previous_open_time_ms: int,
    next_open_time_ms: int,
    expected_value: str,
    actual_value: str,
) -> KlineQualityIssue:
    return KlineQualityIssue(
        issue_type=KlineQualityIssueType.DATABASE_NOT_CONTINUOUS,
        severity=KlineQualitySeverity.ERROR,
        message=message,
        open_time_ms=open_time_ms,
        previous_open_time_ms=previous_open_time_ms,
        next_open_time_ms=next_open_time_ms,
        field_name="open_time_ms",
        expected_value=expected_value,
        actual_value=actual_value,
    )


def _get_previous_before(
    repository: Any,
    db_session: Any,
    request: IncrementalKlineCollectRequest,
    open_time_ms: int,
) -> Any | None:
    if hasattr(repository, "get_previous_before"):
        return repository.get_previous_before(
            db_session,
            symbol=request.symbol,
            interval_value=request.interval_value,
            open_time_ms=open_time_ms,
        )
    return None


def _get_next_after(
    repository: Any,
    db_session: Any,
    request: IncrementalKlineCollectRequest,
    open_time_ms: int,
) -> Any | None:
    if hasattr(repository, "get_next_after"):
        return repository.get_next_after(
            db_session,
            symbol=request.symbol,
            interval_value=request.interval_value,
            open_time_ms=open_time_ms,
        )
    return None
