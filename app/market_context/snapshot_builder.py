"""Payload builder for stage-15 MarketContextSnapshot.

This file belongs to `app/market_context`. It assembles a JSON-serializable
market fact payload and Kline-reference rows from already validated 4h + 1d
formal Kline windows.
It does not request Binance, query MySQL, write MySQL, write Redis, send Hermes,
call DeepSeek or any large language model, generate strategy advice, repair
Klines, or perform trading.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.time_utils import UTC, format_datetime_with_timezone, timestamp_ms_to_utc_datetime
from app.market_context.snapshot_quality import SnapshotReadinessReport
from app.market_context.snapshot_types import (
    MarketContextSnapshotRequest,
    MarketContextSnapshotStatus,
    SnapshotKlineRef,
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
    Return value: `SnapshotPersistencePayload` with JSON facts and Kline refs.
    Failure scenarios: missing row fields or JSON serialization failures propagate
    to the service, which records `failed`.
    External service access: none.
    Data impact: none; this function only builds values.
    """

    generated_at_utc = timestamp_ms_to_utc_datetime(readiness.current_time_ms)
    klines_4h = [_kline_fact(row) for row in readiness.base_context.rows]
    klines_1d = [_kline_fact(row) for row in readiness.higher_context.rows]
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
        "latest_1d_open_time_utc": _open_time_text(readiness.higher_context.latest_open_time_ms),
        "lookback": {
            "4h": readiness.base_context.lookback_count,
            "1d": readiness.higher_context.lookback_count,
        },
        "actual_count": {
            "4h": readiness.base_context.actual_count,
            "1d": readiness.higher_context.actual_count,
        },
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
        "kline_refs": {
            "4h": [_kline_ref_payload(row, sequence_no=index + 1) for index, row in enumerate(readiness.base_context.rows)],
            "1d": [_kline_ref_payload(row, sequence_no=index + 1) for index, row in enumerate(readiness.higher_context.rows)],
        },
        "klines": {
            "4h": klines_4h,
            "1d": klines_1d,
        },
        "source_tables": {
            "4h": "market_kline_4h",
            "1d": "market_kline_1d",
        },
        "trigger_source": trigger_source,
        "trace_id": trace_id,
        "boundary": {
            "fact_snapshot_only": True,
            "no_decision_conclusion": True,
            "no_large_model_output": True,
            "no_binance_request": True,
            "no_formal_kline_write": True,
        },
    }
    refs = tuple(
        [
            *_kline_refs_from_rows(readiness.base_context.rows, interval_value=KLINE_4H_INTERVAL_VALUE),
            *_kline_refs_from_rows(readiness.higher_context.rows, interval_value=KLINE_1D_INTERVAL_VALUE),
        ]
    )
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
        refs=refs,
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
        "trigger_source": request.trigger_source,
        "trace_id": trace_id,
        "boundary": {
            "fact_snapshot_only": True,
            "no_auto_repair": True,
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


def _kline_fact(row: Any) -> dict[str, object]:
    return {
        "id": _row_int(row, "id"),
        "open_time_ms": _row_int(row, "open_time_ms"),
        "open_time_utc": _datetime_or_ms_text(row, "open_time_utc", "open_time_ms"),
        "open": _decimal_text(_row_value(row, "open_price")),
        "high": _decimal_text(_row_value(row, "high_price")),
        "low": _decimal_text(_row_value(row, "low_price")),
        "close": _decimal_text(_row_value(row, "close_price")),
        "volume": _decimal_text(_row_value(row, "volume")),
        "quote_volume": _decimal_text(_row_value(row, "quote_volume")),
        "trade_count": _row_int(row, "trade_count"),
        "taker_buy_base_volume": _decimal_text(_row_value(row, "taker_buy_base_volume")),
        "taker_buy_quote_volume": _decimal_text(_row_value(row, "taker_buy_quote_volume")),
    }


def _kline_ref_payload(row: Any, *, sequence_no: int) -> dict[str, object]:
    return {
        "id": _row_int(row, "id"),
        "open_time_ms": _row_int(row, "open_time_ms"),
        "open_time_utc": _datetime_or_ms_text(row, "open_time_utc", "open_time_ms"),
        "sequence_no": sequence_no,
    }


def _kline_refs_from_rows(rows: tuple[Any, ...], *, interval_value: str) -> list[SnapshotKlineRef]:
    refs: list[SnapshotKlineRef] = []
    for index, row in enumerate(rows, start=1):
        refs.append(
            SnapshotKlineRef(
                symbol=str(_row_value(row, "symbol")),
                interval_value=interval_value,
                market_kline_id=_required_row_int(row, "id"),
                open_time_ms=_required_row_int(row, "open_time_ms"),
                open_time_utc_text=_datetime_or_ms_text(row, "open_time_utc", "open_time_ms"),
                sequence_no=index,
            )
        )
    return refs


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


def _datetime_or_ms_text(row: Any, datetime_field: str, ms_field: str) -> str:
    value = _row_value(row, datetime_field)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    return timestamp_ms_to_utc_datetime(_required_row_int(row, ms_field)).isoformat()


def _decimal_text(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _row_value(row: Any, field_name: str) -> Any:
    if isinstance(row, dict):
        return row.get(field_name)
    return getattr(row, field_name, None)


def _row_int(row: Any, field_name: str) -> int | None:
    value = _row_value(row, field_name)
    if value is None:
        return None
    return int(value)


def _required_row_int(row: Any, field_name: str) -> int:
    value = _row_int(row, field_name)
    if value is None:
        raise ValueError(f"{field_name} is required for MarketContextSnapshot payload")
    return value


__all__ = [
    "build_blocked_snapshot_payload",
    "build_failed_snapshot_payload",
    "build_market_context_snapshot_payload",
]
