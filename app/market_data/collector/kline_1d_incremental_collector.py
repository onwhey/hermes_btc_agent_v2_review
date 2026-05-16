"""BTCUSDT 1d incremental Kline collector service.

Call chain for the optional manual verification CLI:
scripts/collect_1d_klines.py::main
    -> app/market_data/collector/kline_1d_incremental_collector.py::run_incremental_1d_collection
    -> app/core/task_lock.py::RedisTaskLock.acquire_lock
    -> app/storage/mysql/repositories/market_kline_1d_repository.py::get_latest
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
    -> app/market_data/kline_parser.py::parse_binance_klines
    -> app/market_data/collector/kline_1d_incremental_quality.py::check_incremental_1d_quality
    -> app/storage/mysql/repositories/market_kline_1d_repository.py::bulk_upsert

This file belongs to `app/market_data/collector`. It orchestrates stage-14 1d
incremental collection from official Binance REST public Klines. It may request
Binance public Klines, read/write MySQL event and 1d Kline tables, use Redis
only for the Kline write task lock, and send fixed Hermes alerts on real-run
blocked/failed outcomes. It does not implement scheduler jobs, WebSocket price
monitoring, Redis price cache, DeepSeek calls, strategy advice, automatic
repair, overwrite/delete of formal Klines, or trading execution.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.core.exceptions import RedisError
from app.core.logger import get_logger
from app.core.task_lock import RedisTaskLock, build_kline_write_lock_key
from app.market_data.backfill.kline_1d_pipeline import extract_server_time_ms
from app.market_data.collector.exceptions import KlineCollectParameterError
from app.market_data.collector.kline_1d_incremental_flow import (
    create_incremental_1d_running_event,
    default_collector_event_repository,
    default_kline_1d_repository,
    handle_incremental_1d_noop_success,
    handle_incremental_1d_pre_fetch_blocked,
    handle_incremental_1d_quality_blocked,
    handle_incremental_1d_success,
    handle_incremental_1d_task_failure,
    persist_incremental_1d_klines_when_needed,
    record_incremental_1d_quality_report,
    try_acquire_incremental_1d_lock,
)
from app.market_data.collector.kline_1d_incremental_quality import (
    Kline1dIncrementalQualityOutcome,
    check_incremental_1d_quality,
)
from app.market_data.collector.kline_1d_incremental_types import (
    EXIT_PARAMETER_ERROR,
    EXIT_QUALITY_BLOCKED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    IncrementalKline1dCollectRequest,
    IncrementalKline1dCollectResult,
    IncrementalKline1dRequestRange,
    KlineCollectStatus,
    format_incremental_1d_collect_result_lines,
)
from app.market_data.kline_constants import (
    ALLOWED_TRIGGER_SOURCES,
    KLINE_1D_INTERVAL_MS,
    KLINE_1D_INTERVAL_VALUE,
)
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_parser import parse_binance_klines

LOGGER = get_logger("market_data.collector.kline_1d")


def run_incremental_1d_collection(
    request: IncrementalKline1dCollectRequest,
    *,
    db_session: Any,
    binance_client: Any | None = None,
    task_lock: Any | None = None,
    kline_repository: Any | None = None,
    data_quality_repository: Any | None = None,
    collector_event_repository: Any | None = None,
    alert_sender: Any | None = None,
    alert_repository: Any | None = None,
) -> IncrementalKline1dCollectResult:
    """Run one all-or-nothing incremental 1d Kline collection.

    Parameters: request plus caller-owned session and injectable dependencies.
    Return value: structured result with status, counts, alert status, and exit code.
    Failure scenarios: parameter errors, lock failures, empty table, quality blocks,
    persistence errors, and unexpected task failures are converted to explicit
    results.
    External services: may call Binance, Redis task lock, MySQL, and Hermes only
    through injected/default dependencies.
    Data impact: formal writes target only `market_kline_1d` after quality checks pass.
    """

    try:
        validate_incremental_1d_collect_request(request)
    except KlineCollectParameterError as exc:
        return IncrementalKline1dCollectResult(
            status=KlineCollectStatus.FAILED,
            exit_code=EXIT_PARAMETER_ERROR,
            trace_id=request.trace_id,
            message=str(exc),
            details={"error_code": "parameter_error", "formal_write_performed": False},
        )

    active_lock = task_lock or RedisTaskLock()
    active_collector_repository = collector_event_repository or default_collector_event_repository()
    active_kline_repository = kline_repository or default_kline_1d_repository()
    lock_key = build_kline_write_lock_key(symbol=request.symbol, interval_value=request.interval_value)
    event_log: Any | None = None
    lock_acquired = False
    fetched_count = 0
    parsed_klines: tuple[MarketKlineDTO, ...] = ()
    final_outcome: Kline1dIncrementalQualityOutcome | None = None
    requested_start_open_time_ms = 0
    requested_end_open_time_ms = 0
    requested_count = 0

    try:
        lock_result = try_acquire_incremental_1d_lock(
            request,
            db_session=db_session,
            task_lock=active_lock,
            collector_repository=active_collector_repository,
            lock_key=lock_key,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
        if isinstance(lock_result, IncrementalKline1dCollectResult):
            return lock_result
        lock_acquired = True

        latest_row = active_kline_repository.get_latest(db_session, symbol=request.symbol)
        if latest_row is None:
            event_log = create_incremental_1d_running_event(
                db_session,
                active_collector_repository,
                request,
                lock_key=lock_key,
                requested_start_open_time_ms=0,
                requested_end_open_time_ms=0,
                requested_count=0,
            )
            return handle_incremental_1d_pre_fetch_blocked(
                request,
                db_session=db_session,
                collector_repository=active_collector_repository,
                event_log=event_log,
                message="1d 数据尚未初始化，请先执行手动 backfill",
                issue_type="empty_market_kline_1d",
                alert_sender=alert_sender,
                alert_repository=alert_repository,
                details={
                    "lock_key": lock_key,
                    "formal_write_performed": False,
                    "range_unavailable_reason": "1d 数据尚未初始化",
                },
            )

        active_binance_client = binance_client or _default_binance_client()
        server_time_ms = extract_server_time_ms(active_binance_client.get_server_time())
        expected_latest_open_time_ms = expected_latest_closed_1d_open_time(server_time_ms)
        latest_open_time_ms = int(latest_row.open_time_ms)
        latest_close_time_ms = int(latest_row.close_time_ms)

        pre_fetch_block = _pre_fetch_block_reason(
            latest_open_time_ms=latest_open_time_ms,
            latest_close_time_ms=latest_close_time_ms,
            expected_latest_open_time_ms=expected_latest_open_time_ms,
            server_time_ms=server_time_ms,
        )
        if pre_fetch_block is not None:
            event_log = create_incremental_1d_running_event(
                db_session,
                active_collector_repository,
                request,
                lock_key=lock_key,
                requested_start_open_time_ms=latest_open_time_ms,
                requested_end_open_time_ms=expected_latest_open_time_ms,
                requested_count=0,
            )
            return handle_incremental_1d_pre_fetch_blocked(
                request,
                db_session=db_session,
                collector_repository=active_collector_repository,
                event_log=event_log,
                message=pre_fetch_block,
                issue_type="unclosed_kline",
                alert_sender=alert_sender,
                alert_repository=alert_repository,
                details={
                    "latest_open_time_ms": latest_open_time_ms,
                    "latest_close_time_ms": latest_close_time_ms,
                    "expected_latest_open_time_ms": expected_latest_open_time_ms,
                    "server_time_ms": server_time_ms,
                    "formal_write_performed": False,
                },
            )

        if latest_open_time_ms == expected_latest_open_time_ms:
            event_log = create_incremental_1d_running_event(
                db_session,
                active_collector_repository,
                request,
                lock_key=lock_key,
                requested_start_open_time_ms=latest_open_time_ms,
                requested_end_open_time_ms=expected_latest_open_time_ms,
                requested_count=0,
            )
            return handle_incremental_1d_noop_success(
                request,
                db_session=db_session,
                collector_repository=active_collector_repository,
                event_log=event_log,
                latest_open_time_ms=latest_open_time_ms,
                expected_latest_open_time_ms=expected_latest_open_time_ms,
                alert_sender=alert_sender,
                alert_repository=alert_repository,
            )

        request_range = build_incremental_1d_request_range(
            latest_open_time_ms=latest_open_time_ms,
            expected_latest_open_time_ms=expected_latest_open_time_ms,
        )
        requested_start_open_time_ms = request_range.start_open_time_ms
        requested_end_open_time_ms = request_range.end_open_time_ms
        requested_count = request_range.requested_closed_count
        if requested_count > request.max_closed_count:
            event_log = create_incremental_1d_running_event(
                db_session,
                active_collector_repository,
                request,
                lock_key=lock_key,
                requested_start_open_time_ms=requested_start_open_time_ms,
                requested_end_open_time_ms=requested_end_open_time_ms,
                requested_count=requested_count,
            )
            return handle_incremental_1d_pre_fetch_blocked(
                request,
                db_session=db_session,
                collector_repository=active_collector_repository,
                event_log=event_log,
                message=(
                    "1d 增量缺口过大，本服务不会自动初始化或执行不受控的大范围历史回补；"
                    "请先人工选择已收盘区间执行手动 backfill。"
                ),
                issue_type="incremental_gap_too_large",
                alert_sender=alert_sender,
                alert_repository=alert_repository,
                details={
                    "requested_count": requested_count,
                    "max_closed_count": request.max_closed_count,
                    "formal_write_performed": False,
                },
            )

        event_log = create_incremental_1d_running_event(
            db_session,
            active_collector_repository,
            request,
            lock_key=lock_key,
            requested_start_open_time_ms=requested_start_open_time_ms,
            requested_end_open_time_ms=requested_end_open_time_ms,
            requested_count=requested_count,
        )
        raw_klines = fetch_raw_1d_klines_for_incremental(active_binance_client, request, request_range)
        fetched_count = len(raw_klines)
        parsed_klines = tuple(
            parse_incremental_1d_klines(
                raw_klines,
                symbol=request.symbol,
                interval_value=request.interval_value,
                trigger_source=request.trigger_source,
            )
        )
        final_outcome = check_incremental_1d_quality(
            db_session,
            parsed_klines,
            request=request,
            start_open_time_ms=requested_start_open_time_ms,
            end_open_time_ms=requested_end_open_time_ms,
            server_time_ms=server_time_ms,
            repository=active_kline_repository,
        )
        quality_record = record_incremental_1d_quality_report(
            db_session,
            final_outcome.report,
            repository=data_quality_repository,
        )
        if not final_outcome.report.passed:
            return handle_incremental_1d_quality_blocked(
                request,
                db_session=db_session,
                collector_repository=active_collector_repository,
                event_log=event_log,
                outcome=final_outcome,
                quality_record=quality_record,
                fetched_count=fetched_count,
                requested_start_open_time_ms=requested_start_open_time_ms,
                requested_end_open_time_ms=requested_end_open_time_ms,
                alert_sender=alert_sender,
                alert_repository=alert_repository,
            )

        write_result = persist_incremental_1d_klines_when_needed(
            request,
            db_session=db_session,
            outcome=final_outcome,
            kline_repository=active_kline_repository,
        )
        return handle_incremental_1d_success(
            request,
            db_session=db_session,
            collector_repository=active_collector_repository,
            event_log=event_log,
            outcome=final_outcome,
            quality_record=quality_record,
            write_result=write_result,
            fetched_count=fetched_count,
            requested_start_open_time_ms=requested_start_open_time_ms,
            requested_end_open_time_ms=requested_end_open_time_ms,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    except Exception as exc:  # noqa: BLE001 - collection failures must be recorded and alerted.
        LOGGER.exception("1d Kline incremental collection failed trace_id=%s", request.trace_id)
        return handle_incremental_1d_task_failure(
            request,
            db_session=db_session,
            collector_repository=active_collector_repository,
            event_log=event_log,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
            fetched_count=fetched_count,
            parsed_klines=parsed_klines,
            outcome=final_outcome,
            requested_start_open_time_ms=requested_start_open_time_ms,
            requested_end_open_time_ms=requested_end_open_time_ms,
            requested_count=requested_count,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    finally:
        if lock_acquired:
            try:
                active_lock.release_lock(key=lock_key, owner=request.trace_id)
            except RedisError:
                LOGGER.exception("Failed to release 1d Kline write lock key=%s trace_id=%s", lock_key, request.trace_id)


def validate_incremental_1d_collect_request(request: IncrementalKline1dCollectRequest) -> None:
    """Validate 1d incremental collector parameters before external access or writes."""

    if not request.symbol.strip():
        raise KlineCollectParameterError("symbol must not be empty")
    if request.interval_value != KLINE_1D_INTERVAL_VALUE:
        raise KlineCollectParameterError("interval must be 1d")
    if request.trigger_source not in ALLOWED_TRIGGER_SOURCES:
        raise KlineCollectParameterError("trigger_source must be cli or scheduler")
    if request.max_closed_count <= 0:
        raise KlineCollectParameterError("max_closed_count must be greater than 0")
    if request.lock_ttl_seconds <= 0:
        raise KlineCollectParameterError("lock_ttl_seconds must be greater than 0")
    if not request.dry_run and not request.confirm_write:
        raise KlineCollectParameterError("confirm_write is required when dry_run is false")


def expected_latest_closed_1d_open_time(server_time_ms: int) -> int:
    """Return the open time of the latest closed UTC daily Kline."""

    if server_time_ms <= KLINE_1D_INTERVAL_MS:
        raise KlineCollectParameterError("server_time_ms is too early to calculate latest closed 1d Kline")
    current_day_open_time_ms = (server_time_ms // KLINE_1D_INTERVAL_MS) * KLINE_1D_INTERVAL_MS
    return current_day_open_time_ms - KLINE_1D_INTERVAL_MS


def build_incremental_1d_request_range(
    *,
    latest_open_time_ms: int,
    expected_latest_open_time_ms: int,
) -> IncrementalKline1dRequestRange:
    """Build the overlapped REST range from database boundary through expected latest."""

    return IncrementalKline1dRequestRange(
        start_open_time_ms=latest_open_time_ms,
        end_open_time_ms=expected_latest_open_time_ms,
        include_current_unclosed_probe=True,
    )


def fetch_raw_1d_klines_for_incremental(
    binance_client: Any,
    request: IncrementalKline1dCollectRequest,
    request_range: IncrementalKline1dRequestRange,
) -> list[Sequence[Any]]:
    """Fetch the overlapped 1d REST batch using only `BinanceRestClient.get_klines`."""

    return list(
        binance_client.get_klines(
            symbol=request.symbol,
            interval=request.interval_value,
            limit=request_range.limit,
            start_time_ms=request_range.start_open_time_ms,
            end_time_ms=request_range.end_time_ms_for_binance,
        )
    )


def parse_incremental_1d_klines(
    raw_klines: Sequence[Sequence[Any]],
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


def _pre_fetch_block_reason(
    *,
    latest_open_time_ms: int,
    latest_close_time_ms: int,
    expected_latest_open_time_ms: int,
    server_time_ms: int,
) -> str | None:
    if latest_open_time_ms > expected_latest_open_time_ms or latest_close_time_ms >= server_time_ms:
        return (
            "最新 1d K线时间晚于当前理论最新已收盘日 K，"
            "疑似未收盘 K线误写正式表或系统时间异常。"
        )
    if latest_open_time_ms % KLINE_1D_INTERVAL_MS != 0:
        return "market_kline_1d 最新日 K open_time 未对齐 UTC 00:00，系统不会自动修复或继续写入。"
    expected_close_time_ms = latest_open_time_ms + KLINE_1D_INTERVAL_MS - 1
    if latest_close_time_ms != expected_close_time_ms:
        return "market_kline_1d 最新日 K close_time 不符合 1d 周期，系统不会自动修复或继续写入。"
    return None


def _default_binance_client() -> Any:
    from app.exchange.binance.rest_client import BinanceRestClient

    return BinanceRestClient()


__all__ = [
    "build_incremental_1d_request_range",
    "expected_latest_closed_1d_open_time",
    "fetch_raw_1d_klines_for_incremental",
    "format_incremental_1d_collect_result_lines",
    "parse_incremental_1d_klines",
    "run_incremental_1d_collection",
    "validate_incremental_1d_collect_request",
]
