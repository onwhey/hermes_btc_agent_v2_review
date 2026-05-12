"""collector_event_log helpers for phase-09 incremental collection.

This file belongs to `app/market_data/collector`.
It writes only through caller-supplied collector repositories and sessions. It
does not request Binance, write formal Klines, write Redis, send Hermes, call
DeepSeek, repair data, schedule jobs, or trade.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.market_data.collector.quality import event_values_from_collect_report
from app.market_data.collector.types import COLLECTOR_EVENT_TYPE, IncrementalKlineCollectRequest
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.types import KlineQualityReport


def record_collect_pre_execution_failure(
    db_session: Any,
    collector_repository: Any,
    request: IncrementalKlineCollectRequest,
    *,
    error_code: str,
    error_message: str,
) -> Any:
    """Create and immediately fail an event before Binance or formal writes."""

    event = collector_repository.create_running_event(
        db_session,
        event_type=COLLECTOR_EVENT_TYPE,
        symbol=request.symbol,
        interval_value=request.interval_value,
        trigger_source=request.trigger_source,
        data_source=request.data_source,
        requested_start_open_time_ms=0,
        requested_end_open_time_ms=0,
        requested_count=request.requested_count,
        trace_id=request.trace_id,
        details={"stage": "pre_execution", "fetch_mode": "recent_closed_klines"},
    )
    return collector_repository.mark_failed(
        db_session,
        event,
        error_code=error_code,
        error_message=error_message,
    )


def mark_collect_existing_or_pre_execution_failure(
    db_session: Any,
    collector_repository: Any,
    request: IncrementalKlineCollectRequest,
    event_log: Any | None,
    *,
    error_code: str,
    error_message: str,
    fetched_count: int,
    parsed_klines: Sequence[MarketKlineDTO],
    closed_klines: Sequence[MarketKlineDTO],
    filtered_unclosed_count: int,
    report: KlineQualityReport | None,
) -> Any:
    """Mark an existing event failed, or create a pre-execution failed event."""

    if event_log is None:
        return record_collect_pre_execution_failure(
            db_session,
            collector_repository,
            request,
            error_code=error_code,
            error_message=error_message,
        )
    values: dict[str, Any] = {
        "fetched_count": fetched_count,
        "parsed_count": len(parsed_klines),
        "closed_count": len(closed_klines),
        "filtered_unclosed_count": filtered_unclosed_count,
        "error_code": error_code,
        "error_message": error_message,
    }
    if report is not None:
        values.update(
            event_values_from_collect_report(
                request,
                report,
                fetched_count=fetched_count,
                parsed_count=len(parsed_klines),
                closed_count=len(closed_klines),
                filtered_unclosed_count=filtered_unclosed_count,
            )
        )
        values["report_json"] = report.to_dict()
    return collector_repository.mark_failed(db_session, event_log, **values)
