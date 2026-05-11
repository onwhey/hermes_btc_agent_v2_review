"""Quality checks specific to phase-08 historical manual backfill.

This file belongs to `app/market_data/backfill`.
It reuses phase-07 batch checks and adds historical left/right database-neighbor
checks so middle gaps can be backfilled without relying on latest-row continuity.
It reads only through caller-supplied repositories and does not write formal
Klines, write Redis, send Hermes, call DeepSeek, repair data, or trade.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.market_data.backfill.types import ManualKlineBackfillRequest
from app.market_data.kline_constants import KLINE_4H_INTERVAL_MS
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.batch_checker import check_kline_batch_before_persist
from app.market_data.kline_quality.report_formatter import format_quality_report_summary
from app.market_data.kline_quality.types import (
    CHECK_TRIGGER_SOURCE_CLI,
    CHECK_TYPE_BATCH_BEFORE_PERSIST,
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


def check_backfill_quality(
    db_session: Any,
    klines: Iterable[MarketKlineDTO],
    *,
    request: ManualKlineBackfillRequest,
    server_time_ms: int,
    repository: Any | None = None,
) -> KlineQualityReport:
    """Run phase-07 batch checks plus phase-08 historical-neighbor checks."""

    batch = tuple(klines)
    batch_report = check_kline_batch_before_persist(
        batch,
        server_time_ms=server_time_ms,
        check_trigger_source=CHECK_TRIGGER_SOURCE_CLI,
    )
    range_issues = _requested_range_issues(request, batch)
    if not batch_report.passed or range_issues:
        issues = tuple(batch_report.issues) + tuple(range_issues)
        return build_quality_report(
            check_type=CHECK_TYPE_BATCH_BEFORE_PERSIST,
            klines=batch,
            issues=issues,
            check_trigger_source=CHECK_TRIGGER_SOURCE_CLI,
            writable_klines=(),
            metadata={
                **dict(batch_report.metadata),
                "requested_count": request.requested_count,
                "range_issue_count": len(range_issues),
            },
        )
    return _check_backfill_database_context(
        db_session,
        batch,
        request=request,
        repository=repository,
    )


def event_values_from_quality_report(
    request: ManualKlineBackfillRequest,
    report: KlineQualityReport,
    *,
    fetched_count: int,
    quality_check_id: int | None = None,
) -> dict[str, Any]:
    """Build collector_event_log values from a quality report."""

    first_issue = report.first_issue
    parsed_count = report.checked_count
    closed_count = closed_kline_count_from_report(report)
    filtered_unclosed_count = parsed_count - closed_count
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
            "requested_start_open_time_ms": request.start_open_time_ms,
            "requested_end_open_time_ms": request.end_open_time_ms,
            "dry_run": request.dry_run,
            "quality_summary": format_quality_report_summary(report),
        },
    }


def closed_kline_count_from_report(report: KlineQualityReport) -> int:
    """Return checked count minus unclosed Kline issues."""

    unclosed = quality_issue_count(report, KlineQualityIssueType.UNCLOSED_KLINE)
    return max(0, report.checked_count - unclosed)


def quality_issue_count(report: KlineQualityReport, issue_type: KlineQualityIssueType) -> int:
    """Count issues of one type in a report."""

    return sum(1 for issue in report.issues if issue.issue_type == issue_type)


def _check_backfill_database_context(
    db_session: Any,
    batch: tuple[MarketKlineDTO, ...],
    *,
    request: ManualKlineBackfillRequest,
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
        check_trigger_source=CHECK_TRIGGER_SOURCE_CLI,
        existing_open_time_ms=tuple(sorted(existing_identical)),
        writable_klines=tuple(writable_klines) if not issues else (),
        metadata={
            "existing_identical_count": len(existing_identical),
            "new_kline_count": len(writable_klines),
            "previous_open_time_ms": int(previous_row.open_time_ms) if previous_row is not None else None,
            "next_open_time_ms": int(next_row.open_time_ms) if next_row is not None else None,
            "requested_start_open_time_ms": request.start_open_time_ms,
            "requested_end_open_time_ms": request.end_open_time_ms,
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
                        "Backfill first Kline does not connect to previous database Kline; "
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
                        "Backfill last Kline does not connect to next database Kline; "
                        f"last={last.open_time_ms}, expected={expected_next}, actual={next_open_time_ms}"
                    ),
                    open_time_ms=last.open_time_ms,
                    previous_open_time_ms=last.open_time_ms,
                    next_open_time_ms=next_open_time_ms,
                    expected_value=str(expected_next),
                    actual_value=str(next_open_time_ms),
                )
            )


def _requested_range_issues(
    request: ManualKlineBackfillRequest,
    batch: tuple[MarketKlineDTO, ...],
) -> tuple[KlineQualityIssue, ...]:
    if not batch:
        return ()
    actual_open_times = {kline.open_time_ms for kline in batch}
    expected_open_times = set(_expected_open_times(request))
    missing = sorted(expected_open_times - actual_open_times)
    extra = sorted(actual_open_times - expected_open_times)
    issues: list[KlineQualityIssue] = []
    if missing:
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.BATCH_NOT_CONTINUOUS,
                severity=KlineQualitySeverity.ERROR,
                message=(
                    "Binance returned Klines do not cover the requested backfill range; "
                    f"first_missing_open_time_ms={missing[0]}, missing_count={len(missing)}"
                ),
                open_time_ms=missing[0],
                field_name="open_time_ms",
                expected_value="present",
                actual_value="missing",
            )
        )
    if extra:
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.BATCH_NOT_CONTINUOUS,
                severity=KlineQualitySeverity.ERROR,
                message=(
                    "Binance returned Klines outside the requested backfill range; "
                    f"first_extra_open_time_ms={extra[0]}, extra_count={len(extra)}"
                ),
                open_time_ms=extra[0],
                field_name="open_time_ms",
                expected_value="inside_requested_range",
                actual_value=str(extra[0]),
            )
        )
    return tuple(issues)


def _expected_open_times(request: ManualKlineBackfillRequest) -> tuple[int, ...]:
    return tuple(
        range(
            request.start_open_time_ms,
            request.end_open_time_ms + KLINE_4H_INTERVAL_MS,
            KLINE_4H_INTERVAL_MS,
        )
    )


def _database_conflict_issue(kline: MarketKlineDTO, conflict_fields: Iterable[str]) -> KlineQualityIssue:
    fields = tuple(conflict_fields)
    return KlineQualityIssue(
        issue_type=KlineQualityIssueType.DATABASE_CONFLICT,
        severity=KlineQualitySeverity.CRITICAL,
        message=(
            "Existing database Kline conflicts with official backfill Kline; "
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
    request: ManualKlineBackfillRequest,
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
    request: ManualKlineBackfillRequest,
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

