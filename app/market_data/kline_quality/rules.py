"""Pure phase-07 Kline quality rules.

This file belongs to `app/market_data/kline_quality`.
It provides stateless helpers for single-Kline validation, UTC millisecond
continuity, duplicate detection, and closed-Kline checks based on server time.
It is called by batch, database, and integrity checkers.
It does not request Binance, read or write MySQL, read or write Redis, send Hermes,
call DeepSeek, or perform any trading execution.
"""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from app.core.exceptions import KlineValidationError
from app.market_data.kline_constants import KLINE_4H_INTERVAL_MS
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_validator import validate_market_kline
from app.market_data.kline_quality.types import (
    KlineQualityIssue,
    KlineQualityIssueType,
    KlineQualitySeverity,
)


def validate_single_kline_as_quality_issue(kline: MarketKlineDTO) -> KlineQualityIssue | None:
    """Run the phase-06 single-Kline validator and convert failures to issues.

    Parameters: `kline` is one parsed DTO.
    Return value: `None` when valid, otherwise a structured quality issue.
    Failure scenarios: only `KlineValidationError` is converted; unexpected errors propagate.
    External service access and data impact: none.
    """

    try:
        validate_market_kline(kline)
    except KlineValidationError as exc:
        return KlineQualityIssue(
            issue_type=KlineQualityIssueType.INVALID_KLINE,
            severity=KlineQualitySeverity.CRITICAL,
            message=str(exc),
            open_time_ms=getattr(kline, "open_time_ms", None),
        )
    return None


def is_sorted_by_open_time_ms(klines: Sequence[MarketKlineDTO]) -> bool:
    """Return whether input Klines are already ascending by UTC open-time ms."""

    return all(
        previous.open_time_ms <= current.open_time_ms
        for previous, current in zip(klines, klines[1:])
    )


def duplicate_open_time_ms_values(klines: Sequence[MarketKlineDTO]) -> tuple[int, ...]:
    """Return duplicate UTC open-time ms values in ascending order.

    Parameters: `klines` is the caller-provided batch.
    Return value: duplicated open-time values.
    Failure scenarios: none expected.
    External service access and data impact: none.
    """

    counts = Counter(kline.open_time_ms for kline in klines)
    return tuple(sorted(open_time_ms for open_time_ms, count in counts.items() if count > 1))


def continuity_gaps(
    klines: Sequence[MarketKlineDTO],
    *,
    interval_ms: int = KLINE_4H_INTERVAL_MS,
) -> tuple[tuple[int, int], ...]:
    """Return adjacent open-time pairs that break 4h continuity.

    Parameters: `klines` must be checked in the same sequence supplied by caller.
    Return value: pairs `(previous_open_time_ms, next_open_time_ms)` with a gap.
    Failure scenarios: none expected.
    External service access and data impact: none.
    """

    gaps: list[tuple[int, int]] = []
    for previous, current in zip(klines, klines[1:]):
        if current.open_time_ms - previous.open_time_ms != interval_ms:
            gaps.append((previous.open_time_ms, current.open_time_ms))
    return tuple(gaps)


def is_next_open_time_continuous(
    previous_open_time_ms: int,
    next_open_time_ms: int,
    *,
    interval_ms: int = KLINE_4H_INTERVAL_MS,
) -> bool:
    """Return whether two UTC open-time ms values are exactly one 4h step apart."""

    return next_open_time_ms - previous_open_time_ms == interval_ms


def is_kline_closed_by_server_time(kline: MarketKlineDTO, *, server_time_ms: int) -> bool:
    """Return whether a Kline is closed according to Binance server time.

    Parameters: `kline` is one parsed DTO; `server_time_ms` should come from
    Binance server time or an explicit caller-provided server-time fixture.
    Return value: `True` only when `close_time_ms` is strictly before server time.
    Failure scenarios: invalid server time is treated as a caller validation error.
    External service access and data impact: none; this function never reads local time.
    """

    if server_time_ms <= 0:
        raise ValueError("server_time_ms must be greater than 0")
    return kline.close_time_ms < server_time_ms


def issue_for_unsorted_batch(klines: Sequence[MarketKlineDTO]) -> KlineQualityIssue:
    """Build an issue for an input batch that is not ascending by open-time ms."""

    return KlineQualityIssue(
        issue_type=KlineQualityIssueType.BATCH_NOT_SORTED,
        severity=KlineQualitySeverity.ERROR,
        message="Kline batch must be ascending by open_time_ms",
        open_time_ms=_first_unsorted_open_time_ms(klines),
    )


def issue_for_duplicate_open_time(open_time_ms: int) -> KlineQualityIssue:
    """Build an issue for a duplicate open-time ms value."""

    return KlineQualityIssue(
        issue_type=KlineQualityIssueType.DUPLICATE_OPEN_TIME,
        severity=KlineQualitySeverity.ERROR,
        message=f"Kline batch contains duplicate open_time_ms={open_time_ms}",
        open_time_ms=open_time_ms,
    )


def issue_for_continuity_gap(previous_open_time_ms: int, next_open_time_ms: int) -> KlineQualityIssue:
    """Build an issue for a missing 4h step inside one batch."""

    return KlineQualityIssue(
        issue_type=KlineQualityIssueType.BATCH_NOT_CONTINUOUS,
        severity=KlineQualitySeverity.ERROR,
        message=(
            "Adjacent Klines must differ by 14400000 ms; "
            f"previous={previous_open_time_ms}, next={next_open_time_ms}"
        ),
        previous_open_time_ms=previous_open_time_ms,
        next_open_time_ms=next_open_time_ms,
    )


def issue_for_unclosed_kline(kline: MarketKlineDTO, *, server_time_ms: int) -> KlineQualityIssue:
    """Build an issue for a Kline whose close time has not passed server time."""

    return KlineQualityIssue(
        issue_type=KlineQualityIssueType.UNCLOSED_KLINE,
        severity=KlineQualitySeverity.CRITICAL,
        message=(
            "Kline is not closed by server_time_ms; "
            f"open_time_ms={kline.open_time_ms}, close_time_ms={kline.close_time_ms}, "
            f"server_time_ms={server_time_ms}"
        ),
        open_time_ms=kline.open_time_ms,
        expected_value=f"< {server_time_ms}",
        actual_value=str(kline.close_time_ms),
        field_name="close_time_ms",
    )


def _first_unsorted_open_time_ms(klines: Sequence[MarketKlineDTO]) -> int | None:
    for previous, current in zip(klines, klines[1:]):
        if current.open_time_ms < previous.open_time_ms:
            return current.open_time_ms
    return None
