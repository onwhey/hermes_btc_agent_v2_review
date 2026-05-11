"""Batch-level 4h Kline quality checker.

This file belongs to `app/market_data/kline_quality`.
It checks one caller-provided batch for phase-06 single-Kline validity, ascending
UTC open-time ms order, duplicate open times, 4h continuity, and unclosed Klines.
It is called by the quality service, database checker, script, and tests.
It does not request Binance, read or write MySQL, read or write Redis, send Hermes,
call DeepSeek, repair Klines, or perform any trading execution.
"""

from __future__ import annotations

from typing import Iterable

from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.rules import (
    continuity_gaps,
    duplicate_open_time_ms_values,
    is_kline_closed_by_server_time,
    is_sorted_by_open_time_ms,
    issue_for_continuity_gap,
    issue_for_duplicate_open_time,
    issue_for_unclosed_kline,
    issue_for_unsorted_batch,
    validate_single_kline_as_quality_issue,
)
from app.market_data.kline_quality.types import (
    CHECK_TRIGGER_SOURCE_SERVICE,
    CHECK_TYPE_BATCH_BEFORE_PERSIST,
    KlineQualityIssue,
    KlineQualityIssueType,
    KlineQualityReport,
    KlineQualitySeverity,
    build_quality_report,
)


def check_kline_batch_before_persist(
    klines: Iterable[MarketKlineDTO],
    *,
    server_time_ms: int,
    check_type: str = CHECK_TYPE_BATCH_BEFORE_PERSIST,
    check_trigger_source: str = CHECK_TRIGGER_SOURCE_SERVICE,
) -> KlineQualityReport:
    """Check a single batch before any formal Kline-table write.

    Parameters: `klines` is the parsed batch; `server_time_ms` must come from
    Binance server time or an explicit caller-provided server-time fixture.
    Return value: `KlineQualityReport` with clear issues and safe writable rows.
    Failure scenarios: invalid check trigger source raises via report construction;
    invalid Klines become report issues.
    External service access: none; this method never reads local time.
    Data impact: no MySQL writes, Redis writes, Hermes sends, automatic repair, or overwrite.
    """

    batch = tuple(klines)
    issues: list[KlineQualityIssue] = []

    if not batch:
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.EMPTY_BATCH,
                severity=KlineQualitySeverity.ERROR,
                message="Kline batch must not be empty",
            )
        )
        return build_quality_report(
            check_type=check_type,
            klines=batch,
            issues=issues,
            check_trigger_source=check_trigger_source,
            writable_klines=(),
        )

    _append_identity_consistency_issues(batch, issues)

    for kline in batch:
        validation_issue = validate_single_kline_as_quality_issue(kline)
        if validation_issue is not None:
            issues.append(validation_issue)

    if not is_sorted_by_open_time_ms(batch):
        issues.append(issue_for_unsorted_batch(batch))

    for open_time_ms in duplicate_open_time_ms_values(batch):
        issues.append(issue_for_duplicate_open_time(open_time_ms))

    if is_sorted_by_open_time_ms(batch):
        for previous_open_time_ms, next_open_time_ms in continuity_gaps(batch):
            issues.append(issue_for_continuity_gap(previous_open_time_ms, next_open_time_ms))

    for kline in batch:
        if not is_kline_closed_by_server_time(kline, server_time_ms=server_time_ms):
            issues.append(issue_for_unclosed_kline(kline, server_time_ms=server_time_ms))

    return build_quality_report(
        check_type=check_type,
        klines=batch,
        issues=issues,
        check_trigger_source=check_trigger_source,
        writable_klines=batch if not issues else (),
    )


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
                message=f"Kline batch must contain one symbol only; symbols={','.join(sorted(symbols))}",
            )
        )

    interval_values = {kline.interval_value for kline in batch}
    if len(interval_values) > 1:
        issues.append(
            KlineQualityIssue(
                issue_type=KlineQualityIssueType.BATCH_INTERVAL_MISMATCH,
                severity=KlineQualitySeverity.ERROR,
                message=(
                    "Kline batch must contain one interval_value only; "
                    f"interval_values={','.join(sorted(interval_values))}"
                ),
            )
        )
