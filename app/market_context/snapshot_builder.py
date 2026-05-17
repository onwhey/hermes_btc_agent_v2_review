"""Payload builder for stage-15 MarketContextSnapshot.

This file belongs to `app/market_context`. It assembles a JSON-serializable
summary payload from already validated 4h + 1d formal Kline windows.
It does not request Binance, query MySQL, write MySQL, write Redis, send Hermes,
call DeepSeek or any large language model, generate strategy advice, repair
Klines, or perform trading.
"""

from __future__ import annotations

import json

from app.core.time_utils import format_datetime_with_timezone, timestamp_ms_to_utc_datetime
from app.market_context.snapshot_quality import SnapshotReadinessReport
from app.market_context.snapshot_types import (
    MarketContextSnapshotRequest,
    MarketContextSnapshotStatus,
    SnapshotPersistencePayload,
)
from app.market_data.kline_constants import KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE


def build_market_context_snapshot_payload(
    *,
    snapshot_id: str,
    readiness: SnapshotReadinessReport,
    trigger_source: str,
    created_by: str,
    trace_id: str,
) -> SnapshotPersistencePayload:
    """Build the created snapshot persistence payload from passed readiness.

    Parameters: snapshot identity, passed readiness report, trigger source, creator,
    and trace id.
    Return value: `SnapshotPersistencePayload` with window-index facts only.
    Failure scenarios: missing row fields or JSON serialization failures propagate
    to the service, which records `failed`.
    External service access: none.
    Data impact: none; this function only builds values.
    """

    generated_at_utc = timestamp_ms_to_utc_datetime(readiness.current_time_ms)
    payload = {
        "snapshot_id": snapshot_id,
        "symbol": readiness.symbol,
        "base_interval": KLINE_4H_INTERVAL_VALUE,
        "higher_interval": KLINE_1D_INTERVAL_VALUE,
        "generated_at_utc": generated_at_utc.isoformat(),
        "generated_at_display": {
            "utc": format_datetime_with_timezone(generated_at_utc),
        },
        "latest_4h_open_time_utc": _open_time_text(readiness.base_context.latest_open_time_ms),
        "latest_4h_open_time_ms": readiness.base_context.latest_open_time_ms,
        "latest_1d_open_time_utc": _open_time_text(readiness.higher_context.latest_open_time_ms),
        "latest_1d_open_time_ms": readiness.higher_context.latest_open_time_ms,
        "lookback_4h_count": readiness.base_context.lookback_count,
        "lookback_1d_count": readiness.higher_context.lookback_count,
        "actual_4h_count": readiness.base_context.actual_count,
        "actual_1d_count": readiness.higher_context.actual_count,
        "start_4h_open_time_utc": _open_time_text(readiness.base_context.start_open_time_ms),
        "start_4h_open_time_ms": readiness.base_context.start_open_time_ms,
        "end_4h_open_time_utc": _open_time_text(readiness.base_context.end_open_time_ms),
        "end_4h_open_time_ms": readiness.base_context.end_open_time_ms,
        "start_1d_open_time_utc": _open_time_text(readiness.higher_context.start_open_time_ms),
        "start_1d_open_time_ms": readiness.higher_context.start_open_time_ms,
        "end_1d_open_time_utc": _open_time_text(readiness.higher_context.end_open_time_ms),
        "end_1d_open_time_ms": readiness.higher_context.end_open_time_ms,
        "latest_4h_data_quality_status": readiness.base_context.latest_quality_status,
        "latest_1d_data_quality_status": readiness.higher_context.latest_quality_status,
        "latest_4h_quality_check_id": readiness.base_context.latest_quality_check_id,
        "latest_1d_quality_check_id": readiness.higher_context.latest_quality_check_id,
        "latest_4h_collector_event_id": readiness.base_context.latest_collector_event_id,
        "latest_1d_collector_event_id": readiness.higher_context.latest_collector_event_id,
        "data_freshness": {
            "4h": "fresh",
            "1d": "fresh",
        },
        "quality": {
            "4h": readiness.base_context.latest_quality_status,
            "1d": readiness.higher_context.latest_quality_status,
        },
        "kline_ranges": {
            "4h": _range_payload(readiness.base_context.start_open_time_ms, readiness.base_context.end_open_time_ms),
            "1d": _range_payload(readiness.higher_context.start_open_time_ms, readiness.higher_context.end_open_time_ms),
        },
        "source_tables": {
            "4h": "market_kline_4h",
            "1d": "market_kline_1d",
        },
        "restore_contract": {
            "4h": {
                "query_source_table": "market_kline_4h",
                "query_by": ["symbol", "start_4h_open_time_ms", "end_4h_open_time_ms"],
                "expected_count": readiness.base_context.actual_count,
            },
            "1d": {
                "query_source_table": "market_kline_1d",
                "query_by": ["symbol", "start_1d_open_time_ms", "end_1d_open_time_ms"],
                "expected_count": readiness.higher_context.actual_count,
            },
        },
        "trigger_source": trigger_source,
        "trace_id": trace_id,
        "boundary": {
            "fact_snapshot_only": True,
            "no_decision_content": True,
            "no_large_model_output": True,
            "no_binance_request": True,
            "no_formal_kline_write": True,
        },
    }
    return SnapshotPersistencePayload(
        snapshot_id=snapshot_id,
        status=MarketContextSnapshotStatus.CREATED,
        symbol=readiness.symbol,
        base_interval_value=KLINE_4H_INTERVAL_VALUE,
        higher_interval_value=KLINE_1D_INTERVAL_VALUE,
        latest_4h_open_time_ms=readiness.base_context.latest_open_time_ms,
        latest_1d_open_time_ms=readiness.higher_context.latest_open_time_ms,
        lookback_4h_count=readiness.base_context.lookback_count,
        lookback_1d_count=readiness.higher_context.lookback_count,
        actual_4h_count=readiness.base_context.actual_count,
        actual_1d_count=readiness.higher_context.actual_count,
        start_4h_open_time_ms=readiness.base_context.start_open_time_ms,
        end_4h_open_time_ms=readiness.base_context.end_open_time_ms,
        start_1d_open_time_ms=readiness.higher_context.start_open_time_ms,
        end_1d_open_time_ms=readiness.higher_context.end_open_time_ms,
        latest_4h_data_quality_status=readiness.base_context.latest_quality_status,
        latest_1d_data_quality_status=readiness.higher_context.latest_quality_status,
        latest_4h_collector_event_id=readiness.base_context.latest_collector_event_id,
        latest_1d_collector_event_id=readiness.higher_context.latest_collector_event_id,
        latest_4h_quality_check_id=readiness.base_context.latest_quality_check_id,
        latest_1d_quality_check_id=readiness.higher_context.latest_quality_check_id,
        snapshot_payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        created_by=created_by,
        trigger_source=trigger_source,
        trace_id=trace_id,
    )


def build_blocked_snapshot_payload(
    *,
    snapshot_id: str,
    readiness: SnapshotReadinessReport,
    blocked_reason: str,
    trigger_source: str,
    created_by: str,
    trace_id: str,
) -> SnapshotPersistencePayload:
    """Build a compact blocked snapshot record without embedding full Kline arrays."""

    generated_at_utc = timestamp_ms_to_utc_datetime(readiness.current_time_ms)
    payload = {
        "snapshot_id": snapshot_id,
        "symbol": readiness.symbol,
        "base_interval": KLINE_4H_INTERVAL_VALUE,
        "higher_interval": KLINE_1D_INTERVAL_VALUE,
        "generated_at_utc": generated_at_utc.isoformat(),
        "status": MarketContextSnapshotStatus.BLOCKED.value,
        "blocked_reason": blocked_reason,
        "lookback_4h_count": readiness.base_context.lookback_count,
        "lookback_1d_count": readiness.higher_context.lookback_count,
        "actual_4h_count": readiness.base_context.actual_count,
        "actual_1d_count": readiness.higher_context.actual_count,
        "latest_4h_open_time_utc": _open_time_text(readiness.base_context.latest_open_time_ms),
        "latest_4h_open_time_ms": readiness.base_context.latest_open_time_ms,
        "latest_1d_open_time_utc": _open_time_text(readiness.higher_context.latest_open_time_ms),
        "latest_1d_open_time_ms": readiness.higher_context.latest_open_time_ms,
        "kline_ranges": {
            "4h": _range_payload(readiness.base_context.start_open_time_ms, readiness.base_context.end_open_time_ms),
            "1d": _range_payload(readiness.higher_context.start_open_time_ms, readiness.higher_context.end_open_time_ms),
        },
        "actual_count": {
            "4h": readiness.base_context.actual_count,
            "1d": readiness.higher_context.actual_count,
        },
        "quality": {
            "4h": readiness.base_context.latest_quality_status,
            "1d": readiness.higher_context.latest_quality_status,
        },
        "trigger_source": trigger_source,
        "trace_id": trace_id,
        "boundary": {
            "fact_snapshot_only": True,
            "no_auto_repair": True,
            "no_binance_request": True,
            "no_decision_content": True,
            "no_formal_kline_write": True,
        },
    }
    return SnapshotPersistencePayload(
        snapshot_id=snapshot_id,
        status=MarketContextSnapshotStatus.BLOCKED,
        symbol=readiness.symbol,
        base_interval_value=KLINE_4H_INTERVAL_VALUE,
        higher_interval_value=KLINE_1D_INTERVAL_VALUE,
        blocked_reason=blocked_reason,
        latest_4h_open_time_ms=readiness.base_context.latest_open_time_ms,
        latest_1d_open_time_ms=readiness.higher_context.latest_open_time_ms,
        lookback_4h_count=readiness.base_context.lookback_count,
        lookback_1d_count=readiness.higher_context.lookback_count,
        actual_4h_count=readiness.base_context.actual_count,
        actual_1d_count=readiness.higher_context.actual_count,
        start_4h_open_time_ms=readiness.base_context.start_open_time_ms,
        end_4h_open_time_ms=readiness.base_context.end_open_time_ms,
        start_1d_open_time_ms=readiness.higher_context.start_open_time_ms,
        end_1d_open_time_ms=readiness.higher_context.end_open_time_ms,
        latest_4h_data_quality_status=readiness.base_context.latest_quality_status,
        latest_1d_data_quality_status=readiness.higher_context.latest_quality_status,
        latest_4h_collector_event_id=readiness.base_context.latest_collector_event_id,
        latest_1d_collector_event_id=readiness.higher_context.latest_collector_event_id,
        latest_4h_quality_check_id=readiness.base_context.latest_quality_check_id,
        latest_1d_quality_check_id=readiness.higher_context.latest_quality_check_id,
        snapshot_payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        created_by=created_by,
        trigger_source=trigger_source,
        trace_id=trace_id,
    )


def build_failed_snapshot_payload(
    *,
    snapshot_id: str,
    request: MarketContextSnapshotRequest,
    error_message: str,
    trace_id: str,
    current_time_ms: int,
) -> SnapshotPersistencePayload:
    """Build a compact failed snapshot record without embedding Kline arrays."""

    generated_at_utc = timestamp_ms_to_utc_datetime(current_time_ms)
    payload = {
        "snapshot_id": snapshot_id,
        "symbol": request.symbol,
        "base_interval": request.base_interval_value,
        "higher_interval": request.higher_interval_value,
        "generated_at_utc": generated_at_utc.isoformat(),
        "status": MarketContextSnapshotStatus.FAILED.value,
        "error_message": error_message,
        "lookback_4h_count": request.lookback_4h_count,
        "lookback_1d_count": request.lookback_1d_count,
        "actual_4h_count": 0,
        "actual_1d_count": 0,
        "trigger_source": request.trigger_source,
        "trace_id": trace_id,
        "boundary": {
            "fact_snapshot_only": True,
            "no_auto_repair": True,
            "no_binance_request": True,
            "no_decision_content": True,
            "no_formal_kline_write": True,
        },
    }
    return SnapshotPersistencePayload(
        snapshot_id=snapshot_id,
        status=MarketContextSnapshotStatus.FAILED,
        symbol=request.symbol,
        base_interval_value=request.base_interval_value,
        higher_interval_value=request.higher_interval_value,
        error_message=error_message,
        lookback_4h_count=request.lookback_4h_count,
        lookback_1d_count=request.lookback_1d_count,
        actual_4h_count=0,
        actual_1d_count=0,
        snapshot_payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        created_by=request.created_by,
        trigger_source=request.trigger_source,
        trace_id=trace_id,
    )


def _range_payload(start_open_time_ms: int | None, end_open_time_ms: int | None) -> dict[str, object]:
    return {
        "start_open_time_ms": start_open_time_ms,
        "start_open_time_utc": _open_time_text(start_open_time_ms),
        "end_open_time_ms": end_open_time_ms,
        "end_open_time_utc": _open_time_text(end_open_time_ms),
    }


def _open_time_text(open_time_ms: int | None) -> str | None:
    if open_time_ms is None:
        return None
    return timestamp_ms_to_utc_datetime(open_time_ms).isoformat()


__all__ = [
    "build_blocked_snapshot_payload",
    "build_failed_snapshot_payload",
    "build_market_context_snapshot_payload",
]
