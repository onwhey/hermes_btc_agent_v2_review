"""Human-readable formatting for Kline quality reports.

This file belongs to `app/market_data/kline_quality`.
It turns structured reports into short text for scripts, logs, and fixed-template
alert details. It is called by the quality service and manual script.
It does not request Binance, read or write MySQL, read or write Redis, send Hermes,
call DeepSeek, modify Kline data, or perform any trading execution.
"""

from __future__ import annotations

from app.market_data.kline_quality.types import KlineQualityReport


def format_quality_report_summary(report: KlineQualityReport) -> str:
    """Format a compact quality report summary.

    Parameters: `report` is a phase-07 structured result.
    Return value: short plain-text summary safe for logs and alert details.
    Failure scenarios: none expected for report-generated data.
    External service access and data impact: none.
    """

    first_issue = report.first_issue
    first_issue_text = first_issue.message if first_issue is not None else "none"
    return (
        f"check_type={report.check_type}; "
        f"symbol={report.symbol}; interval={report.interval_value}; "
        f"status={report.status.value}; severity={report.severity.value}; "
        f"checked_count={report.checked_count}; issue_count={report.issue_count}; "
        f"first_issue={first_issue_text}"
    )


def format_quality_report_lines(report: KlineQualityReport) -> list[str]:
    """Format a report into script-friendly lines.

    Parameters: `report` is a phase-07 structured result.
    Return value: list of plain-text lines.
    Failure scenarios: none expected.
    External service access and data impact: none.
    """

    lines = [
        format_quality_report_summary(report),
        f"range_open_time_ms={report.start_open_time_ms}..{report.end_open_time_ms}",
        f"existing_open_time_ms={list(report.existing_open_time_ms)}",
        f"writable_open_time_ms={[kline.open_time_ms for kline in report.writable_klines]}",
    ]
    for issue in report.issues:
        lines.append(
            "issue="
            f"{issue.issue_type.value}; severity={issue.severity.value}; "
            f"open_time_ms={issue.open_time_ms}; message={issue.message}"
        )
    return lines
