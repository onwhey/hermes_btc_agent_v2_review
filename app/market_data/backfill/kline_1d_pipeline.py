"""Request, fetch, parse, and parameter helpers for manual 1d backfill.

This file belongs to `app/market_data/backfill`.
It validates bounded 1d backfill requests, splits Binance REST ranges, delegates
to the official REST client interface, and delegates parsing to the shared Kline
parser. It does not write MySQL, write Redis, send Hermes, call DeepSeek,
schedule jobs, repair Klines, or execute trades.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.market_data.backfill.exceptions import KlineBackfillError, KlineBackfillParameterError
from app.market_data.backfill.kline_1d_types import (
    Backfill1dKlineRequestRange,
    ManualKline1dBackfillRequest,
)
from app.market_data.kline_constants import KLINE_1D_INTERVAL_MS, KLINE_1D_INTERVAL_VALUE, TRIGGER_SOURCE_CLI
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_parser import parse_binance_klines


def validate_1d_backfill_request(request: ManualKline1dBackfillRequest) -> None:
    """Validate manual 1d backfill parameters before external access or writes."""

    if not request.symbol.strip():
        raise KlineBackfillParameterError("symbol must not be empty")
    if request.interval_value != KLINE_1D_INTERVAL_VALUE:
        raise KlineBackfillParameterError("interval must be 1d")
    if request.trigger_source != TRIGGER_SOURCE_CLI:
        raise KlineBackfillParameterError("trigger_source must be cli for manual 1d backfill")
    if request.start_open_time_ms < 0 or request.end_open_time_ms < 0:
        raise KlineBackfillParameterError("start/end open time must be greater than or equal to 0")
    if request.end_open_time_ms < request.start_open_time_ms:
        raise KlineBackfillParameterError("end open time must be greater than or equal to start open time")
    if request.start_open_time_ms % KLINE_1D_INTERVAL_MS != 0:
        raise KlineBackfillParameterError("start open time must align to UTC 00:00:00")
    if request.end_open_time_ms % KLINE_1D_INTERVAL_MS != 0:
        raise KlineBackfillParameterError("end open time must align to UTC 00:00:00")
    if request.limit_per_request <= 0:
        raise KlineBackfillParameterError("limit_per_request must be greater than 0")
    if request.limit_per_request > request.max_kline_count:
        raise KlineBackfillParameterError("limit_per_request must not exceed max_kline_count")
    if request.max_kline_count <= 0:
        raise KlineBackfillParameterError("max_kline_count must be greater than 0")
    if request.requested_count > request.max_kline_count:
        raise KlineBackfillParameterError("requested Kline count exceeds max_kline_count")
    if not request.dry_run and not request.confirm_write:
        raise KlineBackfillParameterError("confirm_write is required when dry_run is false")


def build_1d_binance_kline_request_ranges(
    request: ManualKline1dBackfillRequest,
) -> list[Backfill1dKlineRequestRange]:
    """Split one bounded 1d open-time range into Binance REST request ranges."""

    validate_1d_backfill_request(request)
    ranges: list[Backfill1dKlineRequestRange] = []
    current = request.start_open_time_ms
    remaining = request.requested_count
    while remaining > 0:
        count = min(request.limit_per_request, remaining)
        end_open_time_ms = current + (count - 1) * KLINE_1D_INTERVAL_MS
        ranges.append(
            Backfill1dKlineRequestRange(
                start_open_time_ms=current,
                end_open_time_ms=end_open_time_ms,
                limit=count,
            )
        )
        current = end_open_time_ms + KLINE_1D_INTERVAL_MS
        remaining -= count
    return ranges


def fetch_raw_1d_klines_for_backfill(
    binance_client: Any,
    request: ManualKline1dBackfillRequest,
) -> list[Sequence[Any]]:
    """Fetch all raw 1d Klines using only `BinanceRestClient.get_klines`."""

    raw_klines: list[Sequence[Any]] = []
    for request_range in build_1d_binance_kline_request_ranges(request):
        batch = binance_client.get_klines(
            symbol=request.symbol,
            interval=request.interval_value,
            limit=request_range.limit,
            start_time_ms=request_range.start_open_time_ms,
            end_time_ms=request_range.end_time_ms_for_binance,
        )
        raw_klines.extend(batch)
    return raw_klines


def parse_1d_backfill_klines(
    raw_klines: Iterable[Sequence[Any]],
    *,
    symbol: str,
    interval_value: str,
    trigger_source: str,
) -> list[MarketKlineDTO]:
    """Parse Binance raw 1d Klines through the shared Kline parser."""

    return parse_binance_klines(
        raw_klines,
        symbol=symbol,
        interval_value=interval_value,
        trigger_source=trigger_source,
    )


def extract_server_time_ms(server_time_response: Any) -> int:
    """Extract server time from real or fake Binance REST responses."""

    if hasattr(server_time_response, "server_time_ms"):
        return int(server_time_response.server_time_ms)
    if isinstance(server_time_response, Mapping):
        if "serverTime" in server_time_response:
            return int(server_time_response["serverTime"])
        if "server_time_ms" in server_time_response:
            return int(server_time_response["server_time_ms"])
    raise KlineBackfillError("Binance server time response missing server_time_ms")
