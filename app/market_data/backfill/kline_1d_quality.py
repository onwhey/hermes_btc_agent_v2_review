"""Quality checks specific to manual BTCUSDT 1d historical backfill.

This file belongs to `app/market_data/backfill`.
It checks parsed 1d Klines for UTC daily boundaries, closed status by Binance
server time, batch continuity, database conflicts, and left/right neighbor
continuity before any formal 1d table write. It reads only through caller
supplied repositories and does not write formal Klines, write Redis, send
Hermes, call DeepSeek, repair data, schedule jobs, or trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from app.core.exceptions import KlineValidationError
from app.market_data.backfill.kline_1d_types import ManualKline1dBackfillRequest
from app.market_data.kline_constants import KLINE_1D_INTERVAL_MS
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.rules import (
    duplicate_open_time_ms_values,
    is_kline_closed_by_server_time,
    is_sorted_by_open_time_ms,
)
from app.market_data.kline_quality.types import (
    CHECK_TRIGGER_SOURCE_CLI,
    CHECK_TYPE_BATCH_BEFORE_PERSIST,
    CHECK_TYPE_DATABASE_CONTEXT,
    KlineQualityIssue,
    KlineQualityIssueType,
    KlineQualityReport,
    KlineQualitySeverity,
    KlineQualityStatus,
    build_quality_report,
)
from app.storage.mysql.repositories.market_kline_1d_repository import (
    MarketKline1dRepository,
    find_conflicting_1d_core_fields,
    validate_market_kline_1d,
)


@dataclass(frozen=True)
class Kline1dBackfillQualityOutcome:
    """Result wrapper for 1d quality checks and unclosed filtering counters."""

    report: KlineQualityReport
    parsed_count: int
    closed_count: int
    filtered_unclosed_count: int


def check_1d_backfill_quality(
    db_session: Any,
    klines: Iterable[MarketKlineDTO],
    *,
    request: ManualKline1dBackfillRequest,
    server_time_ms: int,
    repository: Any | None = None,
) -> Kline1dBackfillQualityOutcome:
    """Run 1d batch checks plus historical-neighbor database checks.

    Parameters: caller-owned session, parsed DTOs, request, Binance server time,
    and optional repository.
    Return value: quality outcome with report and unclosed-filter counters.
    Failure scenarios: repository errors propagate to the service, where the task
    is marked failed.
    External service access: none.
    Data impact: reads only; it never writes or repairs formal Klines.
    """

    parsed_batch = tuple(klines)
    closed_batch, unclosed_batch = _split_closed_klines(parsed_batch, server_time_ms=server_time_ms)
    pre_filter_issues = list(_unexpected_unclosed_issues(parsed_batch, unclosed_batch))
    if pre_filter_issues:
        report = _build_report(
            request=request,
            klines=parsed_batch,
            issues=pre_filter_issues,
            check_type=CHECK_TYPE_BATCH_BEFORE_PERSIST,
            writable_klines=(),
            metadata={
                "parsed_count": len(parsed_batch),
                "closed_count": len(closed_batch),
                "filtered_unclosed_count": len(unclosed_batch),
            },
        )
        return Kline1dBackfillQualityOutcome(
            report=report,
            parsed_count=len(parsed_batch),
            closed_count=len(closed_batch),
            filtered_unclosed_count=len(unclosed_batch),
        )

    batch_report = _check_closed_batch_before_database(
        closed_batch,
        request=request,
        server_time_ms=server_time_ms,
        filtered_unclosed_count=len(unclosed_batch),
    )
    if not batch_report.passed:
        return Kline1dBackfillQualityOutcome(
            report=batch_report,
            parsed_count=len(parsed_batch),
            closed_count=len(closed_batch),
            filtered_unclosed_count=len(unclosed_batch),
        )

    database_report = _check_1d_backfill_database_context(
        db_session,
        closed_batch,
        request=request,
        server_time_ms=server_time_ms,
        repository=repository,
        filtered_unclosed_count=len(unclosed_batch),
        parsed_count=len(parsed_batch),
    )
    return Kline1dBackfillQualityOutcome(
        report=database_report,
        parsed_count=len(parsed_batch),
        closed_count=len(closed_batch),
        filtered_unclosed_count=len(unclosed_batch),
    )


def event_values_from_1d_quality_outcome(
    request: ManualKline1dBackfillRequest,
    outcome: Kline1dBackfillQualityOutcome,
    *,
    fetched_count: int,
    quality_check_id: int | None = None,
) -> dict[str, Any]:
    """Build collector_event_log values from a 1d quality outcome."""

    report = outcome.report
    first_issue = report.first_issue
    return {
        "fetched_count": fetched_count,
        "parsed_count": outcome.parsed_count,
        "closed_count": outcome.closed_count,
        "filtered_unclosed_count": outcome.filtered_unclosed_count,
        "issue_count": report.issue_count,
        "actual_start_open_time_ms": report.start_open_time_ms,
        "actual_end_open_time_ms": report.end_open_time_ms,
        "quality_check_id": quality_check_id,
        "first_issue_type": first_issue.issue_type.value if first_issue else None,
        "first_issue_message": first_issue.message if first_issue else None,
        "conflict_count": _quality_issue_count(report, KlineQualityIssueType.DATABASE_CONFLICT),
        "skipped_count": len(report.existing_open_time_ms),
        "details": {
            "requested_start_open_time_ms": request.start_open_time_ms,
            "requested_end_open_time_ms": request.end_open_time_ms,
            "dry_run": request.dry_run,
            "filtered_unclosed_count": outcome.filtered_unclosed_count,
        },
    }


def _split_closed_klines(
    batch: Sequence[MarketKlineDTO],
    *,
    server_time_ms: int,
) -> tuple[tuple[MarketKlineDTO, ...], tuple[MarketKlineDTO, ...]]:
    closed: list[MarketKlineDTO] = []
    unclosed: list[MarketKlineDTO] = []
    for kline in batch:
        if is_kline_closed_by_server_time(kline, server_time_ms=server_time_ms):
            closed.append(kline)
        else:
            unclosed.append(kline)
    return tuple(closed), tuple(unclosed)


def _unexpected_unclosed_issues(
    parsed_batch: Sequence[MarketKlineDTO],
    unclosed_batch: Sequence[MarketKlineDTO],
) -> tuple[KlineQualityIssue, ...]:
    if not unclosed_batch:
        return ()
    latest_open_time_ms = max(kline.open_time_ms for kline in parsed_batch)
    issues: list[KlineQualityIssue] = []
    for kline in unclosed_batch:
        if kline.open_time_ms != latest_open_time_ms:
            issues.append(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.UNCLOSED_KLINE,
                    severity=KlineQualitySeverity.ERROR,
                    message=(
                        "REST 返回批次中存在非最后一根未收盘 1d K线，"
                        "系统已阻止写入正式 1d 表。"
                    ),
                    open_time_ms=kline.open_time_ms,
                    field_name="close_time_ms",
                    expected_value="only_latest_unclosed_can_be_filtered",
                    actual_value=str(kline.close_time_ms),
                )
            )
    return tuple(issues)


def _check_closed_batch_before_database(
    batch: tuple[MarketKlineDTO, ...],
    *,
    request: ManualKline1dBackfillRequest,
    server_time_ms: int,
    filtered_unclosed_count: int,
) -> KlineQualityReport:
    issues: list[KlineQualityIssue] = []
    expected_open_times = set(_expected_closed_open_times(request, server_time_ms=server_time_ms))

    if not batch:
        return _build_report(
            request=request,
            klines=(),
            issues=(),
            check_type=CHECK_TYPE_BATCH_BEFORE_PERSIST,
            writable_klines=(),
            metadata={
                "requested_count": request.requested_count,
                "filtered_unclosed_count": filtered_unclosed_count,
                "expected_closed_count": len(expected_open_times),
            },
        )

    _append_identity_consistency_issues(batch, issues)
    for kline in batch:
        try:
            validate_market_kline_1d(kline)
        except KlineValidationError as exc:
            issues.append(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.INVALID_KLINE,
                    severity=KlineQualitySeverity.CRITICAL,
                    message=str(exc),
                    open_time_ms=getattr(kline, "open_time_ms", None),
                )
            )

    if not is_sorted_by_open_time_ms(batch):
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.BATCH_NOT_SORTED,
                severity=KlineQualitySeverity.ERROR,
                message="1d Kline batch must be ascending by open_time_ms",
            )
        )

    for open_time_ms in duplicate_open_time_ms_values(batch):
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.DUPLICATE_OPEN_TIME,
                severity=KlineQualitySeverity.ERROR,
                message=f"1d Kline batch contains duplicate open_time_ms={open_time_ms}",
                open_time_ms=open_time_ms,
            )
        )

    if is_sorted_by_open_time_ms(batch):
        for previous, current in zip(batch, batch[1:]):
            if current.open_time_ms - previous.open_time_ms != KLINE_1D_INTERVAL_MS:
                issues.append(
                    KlineQualityIssue(
                        issue_type=KlineQualityIssueType.BATCH_NOT_CONTINUOUS,
                        severity=KlineQualitySeverity.ERROR,
                        message=(
                            "Adjacent 1d Klines must differ by 86400000 ms; "
                            f"previous={previous.open_time_ms}, next={current.open_time_ms}"
                        ),
                        previous_open_time_ms=previous.open_time_ms,
                        next_open_time_ms=current.open_time_ms,
                    )
                )

    actual_open_times = {kline.open_time_ms for kline in batch}
    missing = sorted(expected_open_times - actual_open_times)
    extra = sorted(actual_open_times - expected_open_times)
    if missing:
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.BATCH_NOT_CONTINUOUS,
                severity=KlineQualitySeverity.ERROR,
                message=(
                    "Binance returned 1d Klines do not cover the requested closed range; "
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
                    "Binance returned 1d Klines outside the requested closed range; "
                    f"first_extra_open_time_ms={extra[0]}, extra_count={len(extra)}"
                ),
                open_time_ms=extra[0],
                field_name="open_time_ms",
                expected_value="inside_requested_closed_range",
                actual_value=str(extra[0]),
            )
        )

    return _build_report(
        request=request,
        klines=batch,
        issues=issues,
        check_type=CHECK_TYPE_BATCH_BEFORE_PERSIST,
        writable_klines=batch if not issues else (),
        metadata={
            "requested_count": request.requested_count,
            "filtered_unclosed_count": filtered_unclosed_count,
            "expected_closed_count": len(expected_open_times),
        },
    )


def _check_1d_backfill_database_context(
    db_session: Any,
    batch: tuple[MarketKlineDTO, ...],
    *,
    request: ManualKline1dBackfillRequest,
    server_time_ms: int,
    repository: Any | None,
    filtered_unclosed_count: int,
    parsed_count: int,
) -> KlineQualityReport:
    active_repository = repository or MarketKline1dRepository()
    issues: list[KlineQualityIssue] = []
    existing_in_request_range = _list_existing_in_request_range(active_repository, db_session, request)
    _append_unclosed_database_row_issues(issues, existing_in_request_range, server_time_ms=server_time_ms)

    if not batch:
        return _build_report(
            request=request,
            klines=(),
            issues=issues,
            check_type=CHECK_TYPE_DATABASE_CONTEXT,
            writable_klines=(),
            metadata={
                "existing_identical_count": 0,
                "new_kline_count": 0,
                "parsed_count": parsed_count,
                "filtered_unclosed_count": filtered_unclosed_count,
            },
        )

    existing_rows = active_repository.list_by_open_times(
        db_session,
        symbol=request.symbol,
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
        conflict_fields = find_conflicting_1d_core_fields(existing, kline)
        if conflict_fields:
            issues.append(_database_conflict_issue(kline, conflict_fields))
        else:
            existing_identical.append(kline.open_time_ms)

    first = batch[0]
    last = batch[-1]
    previous_row = active_repository.get_previous_before(
        db_session,
        symbol=request.symbol,
        open_time_ms=first.open_time_ms,
    )
    next_row = active_repository.get_next_after(
        db_session,
        symbol=request.symbol,
        open_time_ms=last.open_time_ms,
    )
    _append_unclosed_database_row_issues(
        issues,
        tuple(row for row in (previous_row, next_row) if row is not None),
        server_time_ms=server_time_ms,
    )
    _append_neighbor_issues(issues, first, last, previous_row=previous_row, next_row=next_row)

    return _build_report(
        request=request,
        klines=batch,
        issues=issues,
        check_type=CHECK_TYPE_DATABASE_CONTEXT,
        existing_open_time_ms=tuple(sorted(existing_identical)),
        writable_klines=tuple(writable_klines) if not issues else (),
        metadata={
            "existing_identical_count": len(existing_identical),
            "new_kline_count": len(writable_klines),
            "previous_open_time_ms": int(previous_row.open_time_ms) if previous_row is not None else None,
            "next_open_time_ms": int(next_row.open_time_ms) if next_row is not None else None,
            "requested_start_open_time_ms": request.start_open_time_ms,
            "requested_end_open_time_ms": request.end_open_time_ms,
            "parsed_count": parsed_count,
            "filtered_unclosed_count": filtered_unclosed_count,
        },
    )


def _build_report(
    *,
    request: ManualKline1dBackfillRequest,
    klines: Sequence[MarketKlineDTO],
    issues: Sequence[KlineQualityIssue],
    check_type: str,
    writable_klines: Sequence[MarketKlineDTO],
    existing_open_time_ms: Sequence[int] = (),
    metadata: dict[str, object] | None = None,
) -> KlineQualityReport:
    if klines:
        return build_quality_report(
            check_type=check_type,
            klines=klines,
            issues=issues,
            check_trigger_source=CHECK_TRIGGER_SOURCE_CLI,
            existing_open_time_ms=existing_open_time_ms,
            writable_klines=writable_klines,
            metadata=metadata or {},
        )
    status = KlineQualityStatus.FAILED if issues else KlineQualityStatus.PASSED
    severity = _highest_severity(issues)
    return KlineQualityReport(
        check_type=check_type,
        symbol=request.symbol,
        interval_value=request.interval_value,
        check_trigger_source=CHECK_TRIGGER_SOURCE_CLI,
        status=status,
        severity=severity,
        checked_count=0,
        issues=tuple(issues),
        start_open_time_ms=None,
        start_open_time_utc=None,
        start_open_time_prc=None,
        end_open_time_ms=None,
        end_open_time_utc=None,
        end_open_time_prc=None,
        existing_open_time_ms=tuple(existing_open_time_ms),
        writable_klines=(),
        metadata=dict(metadata or {}),
    )


def _expected_closed_open_times(
    request: ManualKline1dBackfillRequest,
    *,
    server_time_ms: int,
) -> tuple[int, ...]:
    values: list[int] = []
    current = request.start_open_time_ms
    while current <= request.end_open_time_ms:
        close_time_ms = current + KLINE_1D_INTERVAL_MS - 1
        if close_time_ms < server_time_ms:
            values.append(current)
        current += KLINE_1D_INTERVAL_MS
    return tuple(values)


def _append_identity_consistency_issues(
    batch: tuple[MarketKlineDTO, ...],
    issues: list[KlineQualityIssue],
) -> None:
    symbols = {kline.symbol for kline in batch}
    if len(symbols) > 1:
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.BATCH_SYMBOL_MISMATCH,
                severity=KlineQualitySeverity.ERROR,
                message=f"1d Kline batch must contain one symbol only; symbols={','.join(sorted(symbols))}",
            )
        )
    interval_values = {kline.interval_value for kline in batch}
    if len(interval_values) > 1:
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.BATCH_INTERVAL_MISMATCH,
                severity=KlineQualitySeverity.ERROR,
                message=(
                    "1d Kline batch must contain one interval_value only; "
                    f"interval_values={','.join(sorted(interval_values))}"
                ),
            )
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
        expected_first = previous_open_time_ms + KLINE_1D_INTERVAL_MS
        if first.open_time_ms != expected_first:
            issues.append(
                _database_not_continuous_issue(
                    message=(
                        "1d backfill first Kline does not connect to previous database Kline; "
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
        expected_next = last.open_time_ms + KLINE_1D_INTERVAL_MS
        if next_open_time_ms != expected_next:
            issues.append(
                _database_not_continuous_issue(
                    message=(
                        "1d backfill last Kline does not connect to next database Kline; "
                        f"last={last.open_time_ms}, expected={expected_next}, actual={next_open_time_ms}"
                    ),
                    open_time_ms=last.open_time_ms,
                    previous_open_time_ms=last.open_time_ms,
                    next_open_time_ms=next_open_time_ms,
                    expected_value=str(expected_next),
                    actual_value=str(next_open_time_ms),
                )
            )


def _list_existing_in_request_range(
    repository: Any,
    db_session: Any,
    request: ManualKline1dBackfillRequest,
) -> tuple[Any, ...]:
    if hasattr(repository, "list_by_time_range"):
        return tuple(
            repository.list_by_time_range(
                db_session,
                symbol=request.symbol,
                start_open_time_ms=request.start_open_time_ms,
                end_open_time_ms=request.end_open_time_ms,
            )
        )
    return ()


def _append_unclosed_database_row_issues(
    issues: list[KlineQualityIssue],
    rows: Sequence[Any],
    *,
    server_time_ms: int,
) -> None:
    for row in rows:
        close_time_ms = int(getattr(row, "close_time_ms", 0) or 0)
        if close_time_ms >= server_time_ms:
            open_time_ms = int(getattr(row, "open_time_ms", 0) or 0)
            issues.append(
                KlineQualityIssue(
                    issue_type=KlineQualityIssueType.UNCLOSED_KLINE,
                    severity=KlineQualitySeverity.CRITICAL,
                    message=(
                        "market_kline_1d 中存在未收盘日 K，疑似未收盘 K线误写正式表；"
                        "本次回补不会自动删除、覆盖或修复该记录。"
                    ),
                    open_time_ms=open_time_ms,
                    field_name="close_time_ms",
                    expected_value=f"< {server_time_ms}",
                    actual_value=str(close_time_ms),
                )
            )


def _database_conflict_issue(kline: MarketKlineDTO, conflict_fields: Iterable[str]) -> KlineQualityIssue:
    fields = tuple(conflict_fields)
    return KlineQualityIssue(
        issue_type=KlineQualityIssueType.DATABASE_CONFLICT,
        severity=KlineQualitySeverity.CRITICAL,
        message=(
            "Existing market_kline_1d row conflicts with official 1d backfill Kline; "
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


def _highest_severity(issues: Sequence[KlineQualityIssue]) -> KlineQualitySeverity:
    if not issues:
        return KlineQualitySeverity.INFO
    order = {
        KlineQualitySeverity.INFO: 0,
        KlineQualitySeverity.WARNING: 1,
        KlineQualitySeverity.ERROR: 2,
        KlineQualitySeverity.CRITICAL: 3,
    }
    highest = KlineQualitySeverity.INFO
    for issue in issues:
        if order[issue.severity] > order[highest]:
            highest = issue.severity
    return highest


def _quality_issue_count(report: KlineQualityReport, issue_type: KlineQualityIssueType) -> int:
    return sum(1 for issue in report.issues if issue.issue_type == issue_type)
