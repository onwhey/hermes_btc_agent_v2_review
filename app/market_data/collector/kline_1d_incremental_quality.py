"""Quality checks for stage-14 BTCUSDT 1d incremental collection.

This file belongs to `app/market_data/collector`. It adapts the existing 1d
formal-table validation and database-context rules to the incremental collector
range: the REST batch must cover the overlapped database boundary and every
missing closed 1d Kline up to the theoretical latest closed day. It reads only
through caller-supplied repositories and does not write formal Klines, write
Redis, send Hermes, call DeepSeek, repair data, schedule jobs, or trade.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from app.market_data.backfill.kline_1d_quality import (
    check_1d_backfill_quality,
    event_values_from_1d_quality_outcome,
)
from app.market_data.backfill.kline_1d_types import (
    DEFAULT_1D_BACKFILL_LIMIT_PER_REQUEST,
    ManualKline1dBackfillRequest,
)
from app.market_data.collector.kline_1d_incremental_types import (
    IncrementalKline1dCollectRequest,
    KLINE_1D_INCREMENTAL_EVENT_TYPE,
)
from app.market_data.kline_constants import KLINE_1D_INTERVAL_MS
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.types import (
    CHECK_TYPE_BATCH_BEFORE_PERSIST,
    KlineQualityIssue,
    KlineQualityIssueType,
    KlineQualityReport,
    KlineQualitySeverity,
    KlineQualityStatus,
)


@dataclass(frozen=True)
class Kline1dIncrementalQualityOutcome:
    """Result wrapper for 1d incremental quality checks and filter counters."""

    report: KlineQualityReport
    parsed_count: int
    closed_count: int
    filtered_unclosed_count: int


def check_incremental_1d_quality(
    db_session: Any,
    klines: Iterable[MarketKlineDTO],
    *,
    request: IncrementalKline1dCollectRequest,
    start_open_time_ms: int,
    end_open_time_ms: int,
    server_time_ms: int,
    repository: Any | None = None,
) -> Kline1dIncrementalQualityOutcome:
    """Run 1d incremental batch and database checks before any formal write.

    Parameters: caller-owned session, parsed DTOs, request, inclusive closed
    open-time range, Binance server time, and optional 1d repository.
    Return value: quality outcome with report and counters.
    Failure scenarios: repository errors propagate to the service where the
    task is marked failed.
    External service access: none.
    Data impact: reads only; it never writes, overwrites, or repairs `market_kline_1d`.
    """

    adapter_request = _adapter_backfill_request(
        request,
        start_open_time_ms=start_open_time_ms,
        end_open_time_ms=end_open_time_ms,
    )
    backfill_outcome = check_1d_backfill_quality(
        db_session,
        klines,
        request=adapter_request,
        server_time_ms=server_time_ms,
        repository=repository,
    )
    report = _with_incremental_identity(
        backfill_outcome.report,
        request=request,
        start_open_time_ms=start_open_time_ms,
        end_open_time_ms=end_open_time_ms,
    )
    if report.passed and backfill_outcome.closed_count < adapter_request.requested_count:
        report = _build_missing_closed_range_report(
            request,
            start_open_time_ms=start_open_time_ms,
            end_open_time_ms=end_open_time_ms,
            checked_klines=tuple(report.writable_klines),
            closed_count=backfill_outcome.closed_count,
            filtered_unclosed_count=backfill_outcome.filtered_unclosed_count,
        )
    return Kline1dIncrementalQualityOutcome(
        report=report,
        parsed_count=backfill_outcome.parsed_count,
        closed_count=backfill_outcome.closed_count,
        filtered_unclosed_count=backfill_outcome.filtered_unclosed_count,
    )


def event_values_from_incremental_1d_quality_outcome(
    request: IncrementalKline1dCollectRequest,
    outcome: Kline1dIncrementalQualityOutcome,
    *,
    start_open_time_ms: int,
    end_open_time_ms: int,
    fetched_count: int,
    quality_check_id: int | None = None,
) -> dict[str, Any]:
    """Build collector_event_log values from a 1d incremental quality outcome."""

    adapter_request = _adapter_backfill_request(
        request,
        start_open_time_ms=start_open_time_ms,
        end_open_time_ms=end_open_time_ms,
    )
    values = event_values_from_1d_quality_outcome(
        adapter_request,
        outcome,
        fetched_count=fetched_count,
        quality_check_id=quality_check_id,
    )
    details = dict(values.get("details") or {})
    details.update(
        {
            "event_type": KLINE_1D_INCREMENTAL_EVENT_TYPE,
            "trigger_source": request.trigger_source,
            "data_source": request.data_source,
            "fetch_mode": "overlap_latest_db_to_expected_latest_closed_1d",
            "dry_run": request.dry_run,
        }
    )
    values["details"] = details
    return values


def _adapter_backfill_request(
    request: IncrementalKline1dCollectRequest,
    *,
    start_open_time_ms: int,
    end_open_time_ms: int,
) -> ManualKline1dBackfillRequest:
    requested_count = max(1, ((end_open_time_ms - start_open_time_ms) // KLINE_1D_INTERVAL_MS) + 1)
    return ManualKline1dBackfillRequest(
        symbol=request.symbol,
        interval_value=request.interval_value,
        start_open_time_ms=start_open_time_ms,
        end_open_time_ms=end_open_time_ms,
        trigger_source=request.trigger_source,
        dry_run=request.dry_run,
        confirm_write=True,
        notify_success=request.notify_success,
        limit_per_request=max(DEFAULT_1D_BACKFILL_LIMIT_PER_REQUEST, requested_count),
        max_kline_count=max(request.max_closed_count + 1, requested_count),
        trace_id=request.trace_id,
    )


def _with_incremental_identity(
    report: KlineQualityReport,
    *,
    request: IncrementalKline1dCollectRequest,
    start_open_time_ms: int,
    end_open_time_ms: int,
) -> KlineQualityReport:
    metadata = dict(report.metadata)
    metadata.update(
        {
            "event_type": KLINE_1D_INCREMENTAL_EVENT_TYPE,
            "requested_start_open_time_ms": start_open_time_ms,
            "requested_end_open_time_ms": end_open_time_ms,
            "fetch_mode": "overlap_latest_db_to_expected_latest_closed_1d",
        }
    )
    return replace(
        report,
        symbol=request.symbol,
        interval_value=request.interval_value,
        check_trigger_source=request.trigger_source,
        metadata=metadata,
    )


def _build_missing_closed_range_report(
    request: IncrementalKline1dCollectRequest,
    *,
    start_open_time_ms: int,
    end_open_time_ms: int,
    checked_klines: tuple[MarketKlineDTO, ...],
    closed_count: int,
    filtered_unclosed_count: int,
) -> KlineQualityReport:
    missing_open_time_ms = _first_missing_open_time(
        checked_klines,
        start_open_time_ms=start_open_time_ms,
        end_open_time_ms=end_open_time_ms,
    )
    issue = KlineQualityIssue(
        issue_type=KlineQualityIssueType.BATCH_NOT_CONTINUOUS,
        severity=KlineQualitySeverity.ERROR,
        message=(
            "Binance REST returned 1d Klines do not cover the incremental closed range; "
            f"first_missing_open_time_ms={missing_open_time_ms}"
        ),
        open_time_ms=missing_open_time_ms,
        field_name="open_time_ms",
        expected_value="present",
        actual_value="missing",
    )
    return KlineQualityReport(
        check_type=CHECK_TYPE_BATCH_BEFORE_PERSIST,
        symbol=request.symbol,
        interval_value=request.interval_value,
        check_trigger_source=request.trigger_source,
        status=KlineQualityStatus.FAILED,
        severity=KlineQualitySeverity.ERROR,
        checked_count=closed_count,
        issues=(issue,),
        writable_klines=(),
        metadata={
            "event_type": KLINE_1D_INCREMENTAL_EVENT_TYPE,
            "requested_start_open_time_ms": start_open_time_ms,
            "requested_end_open_time_ms": end_open_time_ms,
            "expected_closed_count": ((end_open_time_ms - start_open_time_ms) // KLINE_1D_INTERVAL_MS) + 1,
            "closed_count": closed_count,
            "filtered_unclosed_count": filtered_unclosed_count,
            "fetch_mode": "overlap_latest_db_to_expected_latest_closed_1d",
        },
    )


def _first_missing_open_time(
    klines: tuple[MarketKlineDTO, ...],
    *,
    start_open_time_ms: int,
    end_open_time_ms: int,
) -> int:
    actual = {kline.open_time_ms for kline in klines}
    current = start_open_time_ms
    while current <= end_open_time_ms:
        if current not in actual:
            return current
        current += KLINE_1D_INTERVAL_MS
    return start_open_time_ms


__all__ = [
    "Kline1dIncrementalQualityOutcome",
    "check_incremental_1d_quality",
    "event_values_from_incremental_1d_quality_outcome",
]
