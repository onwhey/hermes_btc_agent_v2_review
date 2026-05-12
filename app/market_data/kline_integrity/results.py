"""Result helpers for phase-11 daily Kline integrity review.

This file belongs to `app/market_data/kline_integrity`.
It converts quality reports into daily review results and formats CLI output.
It does not request Binance, read or write MySQL, read or write Redis, send
Hermes, call DeepSeek, repair Klines, or perform trading execution.
"""

from __future__ import annotations

from typing import Any

from app.market_data.kline_integrity.types import (
    EXIT_QUALITY_FAILED,
    EXIT_SUCCESS,
    DailyKlineIntegrityCheckRequest,
    DailyKlineIntegrityCheckResult,
    DailyKlineIntegrityStatus,
)
from app.market_data.kline_quality.types import KlineQualityReport


def result_from_quality_report(
    request: DailyKlineIntegrityCheckRequest,
    report: KlineQualityReport,
    *,
    quality_record: Any | None,
) -> DailyKlineIntegrityCheckResult:
    """Build the daily review result from a completed quality report."""

    first_issue = report.first_issue
    status = DailyKlineIntegrityStatus.HEALTHY if report.passed else DailyKlineIntegrityStatus.FAILED
    exit_code = EXIT_SUCCESS if report.passed else EXIT_QUALITY_FAILED
    message = "Daily Kline integrity check passed" if report.passed else "Daily Kline integrity check failed"
    return DailyKlineIntegrityCheckResult(
        status=status,
        exit_code=exit_code,
        trace_id=request.trace_id,
        message=message,
        requested_count=request.requested_count,
        checked_count=report.checked_count,
        issue_count=report.issue_count,
        first_issue_type=first_issue.issue_type.value if first_issue else None,
        first_issue_message=first_issue.message if first_issue else None,
        checked_start_time=datetime_to_text(report.start_open_time_utc),
        checked_end_time=datetime_to_text(report.end_open_time_utc),
        quality_check_id=record_id(quality_record),
        details={
            "status": "healthy" if report.passed else "failed",
            "source": "Binance REST official klines",
            "no_repair_performed": True,
            "report": report.to_dict(),
        },
    )


def format_daily_kline_integrity_result_lines(result: DailyKlineIntegrityCheckResult) -> list[str]:
    """Format a daily review result into script-friendly lines."""

    lines = [
        (
            f"daily_kline_integrity_status={result.status.value}; exit_code={result.exit_code}; "
            f"requested_count={result.requested_count}; checked_count={result.checked_count}; "
            f"issue_count={result.issue_count}; alert_status={result.alert_status or ''}"
        ),
        f"trace_id={result.trace_id}",
        f"quality_check_id={result.quality_check_id or ''}",
        f"checked_time_utc={result.checked_start_time or ''}..{result.checked_end_time or ''}",
        f"message={result.message}",
    ]
    if result.first_issue_type:
        lines.append(
            "first_issue="
            f"{result.first_issue_type}; message={result.first_issue_message or ''}"
        )
    return lines


def record_id(record: Any | None) -> int | None:
    """Return an integer id from an ORM/fake record when one is available."""

    value = getattr(record, "id", None)
    return int(value) if value is not None else None


def datetime_to_text(value: Any | None) -> str | None:
    """Return an ISO datetime string, preserving `None` for unknown ranges."""

    return value.isoformat() if value is not None else None
