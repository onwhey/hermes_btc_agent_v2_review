"""Result helpers for phase-09 incremental collection.

This file belongs to `app/market_data/collector`.
It formats collector results and converts quality reports into result objects.
It does not request Binance, write MySQL, write Redis, send Hermes, call
DeepSeek, repair Klines, schedule jobs, or trade.
"""

from __future__ import annotations

from typing import Any

from app.market_data.collector.types import (
    IncrementalKlineCollectRequest,
    IncrementalKlineCollectResult,
    KlineCollectStatus,
)
from app.market_data.kline_quality.report_formatter import format_quality_report_summary
from app.market_data.kline_quality.types import KlineQualityReport


def format_incremental_collect_result_lines(result: IncrementalKlineCollectResult) -> list[str]:
    """Format an incremental collector result for the thin CLI entry point."""

    lines = [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"trace_id={result.trace_id}",
        f"message={result.message}",
        (
            "counts="
            f"requested:{result.requested_count},fetched:{result.fetched_count},"
            f"parsed:{result.parsed_count},closed:{result.closed_count},"
            f"filtered_unclosed:{result.filtered_unclosed_count},"
            f"writable:{result.writable_count},inserted:{result.inserted_count},"
            f"skipped_existing:{result.skipped_existing_count},issues:{result.issue_count}"
        ),
    ]
    if result.first_issue_type or result.first_issue_message:
        lines.append(
            f"first_issue={result.first_issue_type or ''}; message={result.first_issue_message or ''}"
        )
    if result.alert_status:
        lines.append(f"alert_status={result.alert_status}")
    return lines


def result_from_collect_report(
    request: IncrementalKlineCollectRequest,
    report: KlineQualityReport,
    *,
    status: KlineCollectStatus,
    exit_code: int,
    message: str,
    event_log_id: int | None,
    quality_check_id: int | None,
    fetched_count: int,
    parsed_count: int,
    closed_count: int,
    filtered_unclosed_count: int,
    inserted_count: int = 0,
    skipped_existing_count: int | None = None,
) -> IncrementalKlineCollectResult:
    """Build a result object from an incremental quality report."""

    first_issue = report.first_issue
    return IncrementalKlineCollectResult(
        status=status,
        exit_code=exit_code,
        trace_id=request.trace_id,
        message=message,
        requested_count=request.requested_count,
        fetched_count=fetched_count,
        parsed_count=parsed_count,
        closed_count=closed_count,
        filtered_unclosed_count=filtered_unclosed_count,
        writable_count=len(report.writable_klines),
        inserted_count=inserted_count,
        skipped_existing_count=(
            len(report.existing_open_time_ms)
            if skipped_existing_count is None
            else skipped_existing_count
        ),
        issue_count=report.issue_count,
        first_issue_type=first_issue.issue_type.value if first_issue else None,
        first_issue_message=first_issue.message if first_issue else None,
        event_log_id=event_log_id,
        quality_check_id=quality_check_id,
        details={"quality_summary": format_quality_report_summary(report)},
    )


def build_failed_collect_result(
    request: IncrementalKlineCollectRequest,
    *,
    event_log: Any | None,
    exit_code: int,
    message: str,
    error_code: str,
    fetched_count: int = 0,
    parsed_count: int = 0,
    closed_count: int = 0,
) -> IncrementalKlineCollectResult:
    """Build a failed result outside normal quality-report flow."""

    return IncrementalKlineCollectResult(
        status=KlineCollectStatus.FAILED,
        exit_code=exit_code,
        trace_id=request.trace_id,
        message=message,
        requested_count=request.requested_count,
        fetched_count=fetched_count,
        parsed_count=parsed_count,
        closed_count=closed_count,
        filtered_unclosed_count=max(0, parsed_count - closed_count),
        event_log_id=record_id(event_log),
        details={"error_code": error_code},
    )


def record_id(record: Any | None) -> int | None:
    """Return an integer record id from ORM rows or fake test rows."""

    value = getattr(record, "id", None)
    return int(value) if value is not None else None
