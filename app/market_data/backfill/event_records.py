"""collector_event_log helper functions for manual 4h backfill.

This file belongs to `app/market_data/backfill`.
It keeps event-log failure recording separate from the main backfill orchestration.
It writes only through a caller-supplied collector repository/session and does
not request Binance, write formal Klines, write Redis, send Hermes, call
DeepSeek, repair data, or trade.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.market_data.backfill.quality import event_values_from_quality_report
from app.market_data.backfill.types import BACKFILL_EVENT_TYPE, ManualKlineBackfillRequest
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.types import KlineQualityReport


def record_pre_execution_failure(
    db_session: Any,
    collector_repository: Any,
    request: ManualKlineBackfillRequest,
    *,
    error_code: str,
    error_message: str,
) -> Any:
    """Create and immediately fail an event before Binance or formal writes."""

    event = collector_repository.create_running_event(
        db_session,
        event_type=BACKFILL_EVENT_TYPE,
        symbol=request.symbol,
        interval_value=request.interval_value,
        trigger_source=request.trigger_source,
        data_source=request.data_source,
        requested_start_open_time_ms=request.start_open_time_ms,
        requested_end_open_time_ms=request.end_open_time_ms,
        requested_count=request.requested_count,
        trace_id=request.trace_id,
        details={"stage": "pre_execution"},
    )
    return collector_repository.mark_failed(
        db_session,
        event,
        error_code=error_code,
        error_message=error_message,
    )


def mark_existing_or_pre_execution_failure(
    db_session: Any,
    collector_repository: Any,
    request: ManualKlineBackfillRequest,
    event_log: Any | None,
    *,
    error_code: str,
    error_message: str,
    fetched_count: int,
    parsed_klines: Sequence[MarketKlineDTO],
    report: KlineQualityReport | None,
) -> Any:
    """Mark an existing event failed, or create a pre-execution failed event."""

    if event_log is None:
        return record_pre_execution_failure(
            db_session,
            collector_repository,
            request,
            error_code=error_code,
            error_message=error_message,
        )
    values: dict[str, Any] = {
        "fetched_count": fetched_count,
        "parsed_count": len(parsed_klines),
        "closed_count": len(parsed_klines),
        "error_code": error_code,
        "error_message": error_message,
    }
    if report is not None:
        values.update(event_values_from_quality_report(request, report, fetched_count=fetched_count))
        values["report_json"] = report.to_dict()
    return collector_repository.mark_failed(db_session, event_log, **values)
