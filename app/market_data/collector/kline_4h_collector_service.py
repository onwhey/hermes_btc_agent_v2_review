"""BTCUSDT 4h incremental Kline collector service.

Call chain:
scripts/collect_4h_klines.py::main
    -> app/market_data/collector/kline_4h_collector_service.py::run_incremental_4h_collection
    -> app/core/task_lock.py::RedisTaskLock.acquire_lock
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
    -> app/market_data/kline_parser.py::parse_binance_klines
    -> app/market_data/collector/quality.py::check_incremental_collect_quality
    -> app/market_data/backfill/persistence.py::persist_backfill_klines

This file belongs to `app/market_data/collector`.
It orchestrates phase-09 incremental collection from official Binance REST
public 4h Klines. It may request Binance public Klines, read/write MySQL event
and Kline tables, use Redis only for the Kline write task lock, and send fixed
Hermes alerts on blocked/failed outcomes. It does not implement scheduler jobs,
WebSocket price monitoring, Redis price cache, DeepSeek calls, strategy advice,
automatic repair, overwrite/delete of formal Klines, or trading execution.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from app.core.exceptions import RedisError
from app.core.logger import get_logger
from app.core.task_lock import RedisTaskLock, build_kline_write_lock_key
from app.market_data.backfill.persistence import persist_backfill_klines
from app.market_data.backfill.pipeline import extract_server_time_ms
from app.market_data.collector.alerts import (
    send_collect_failure_alert_and_adjust_exit_code,
    send_collect_success_alert_and_adjust_exit_code,
)
from app.market_data.collector.event_records import (
    mark_collect_existing_or_pre_execution_failure,
    record_collect_pre_execution_failure,
)
from app.market_data.collector.exceptions import KlineCollectParameterError
from app.market_data.collector.quality import (
    check_incremental_collect_quality,
    event_values_from_collect_report,
)
from app.market_data.collector.results import (
    build_failed_collect_result,
    format_incremental_collect_result_lines,
    record_id,
    result_from_collect_report,
)
from app.market_data.collector.types import (
    COLLECTOR_EVENT_TYPE,
    EXIT_PARAMETER_ERROR,
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_BLOCKED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    IncrementalKlineCollectRequest,
    IncrementalKlineCollectResult,
    KlineCollectStatus,
)
from app.market_data.kline_constants import (
    ALLOWED_TRIGGER_SOURCES,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
    TRIGGER_SOURCE_SCHEDULER,
)
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_parser import parse_binance_klines
from app.market_data.kline_quality.types import KlineQualityReport

LOGGER = get_logger("market_data.collector.kline_4h")


def run_incremental_4h_collection(
    request: IncrementalKlineCollectRequest,
    *,
    db_session: Any,
    binance_client: Any | None = None,
    task_lock: Any | None = None,
    kline_repository: Any | None = None,
    data_quality_repository: Any | None = None,
    collector_event_repository: Any | None = None,
    alert_sender: Any | None = None,
    alert_repository: Any | None = None,
) -> IncrementalKlineCollectResult:
    """Run one all-or-nothing incremental 4h Kline collection.

    External services are accessed only through injected/default dependencies.
    Formal Kline writes happen only after quality checks pass. Failures are
    converted to explicit results and mandatory fixed-template Hermes alerts.
    """

    try:
        validate_incremental_collect_request(request)
    except KlineCollectParameterError as exc:
        return IncrementalKlineCollectResult(
            status=KlineCollectStatus.FAILED,
            exit_code=EXIT_PARAMETER_ERROR,
            trace_id=request.trace_id,
            message=str(exc),
            requested_count=request.requested_count,
            details={"error_code": "parameter_error"},
        )

    active_lock = task_lock or RedisTaskLock()
    active_collector_repository = collector_event_repository or _default_collector_event_repository()
    lock_key = build_kline_write_lock_key(symbol=request.symbol, interval_value=request.interval_value)
    event_log: Any | None = None
    lock_acquired = False
    fetched_count = 0
    parsed_klines: tuple[MarketKlineDTO, ...] = ()
    closed_klines: tuple[MarketKlineDTO, ...] = ()
    filtered_unclosed_count = 0
    final_report: KlineQualityReport | None = None

    try:
        lock_result = _try_acquire_lock(
            request,
            db_session=db_session,
            task_lock=active_lock,
            collector_repository=active_collector_repository,
            lock_key=lock_key,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
        if isinstance(lock_result, IncrementalKlineCollectResult):
            return lock_result
        lock_acquired = True

        event_log = _create_running_event(db_session, active_collector_repository, request, lock_key=lock_key)

        active_binance_client = binance_client or _default_binance_client()
        server_time_ms = extract_server_time_ms(active_binance_client.get_server_time())
        raw_klines = active_binance_client.get_klines(
            symbol=request.symbol,
            interval=request.interval_value,
            limit=request.limit + 1,
        )
        fetched_count = len(raw_klines)
        parsed_klines = tuple(
            parse_binance_klines(
                raw_klines,
                symbol=request.symbol,
                interval_value=request.interval_value,
                trigger_source=request.trigger_source,
            )
        )
        closed_all = tuple(kline for kline in parsed_klines if kline.close_time_ms < server_time_ms)
        filtered_unclosed_count = max(0, len(parsed_klines) - len(closed_all))
        closed_klines = tuple(sorted(closed_all, key=lambda item: item.open_time_ms))[-request.limit :]

        final_report = check_incremental_collect_quality(
            db_session,
            closed_klines,
            request=request,
            server_time_ms=server_time_ms,
            repository=kline_repository,
        )
        quality_record = _record_quality_report(db_session, final_report, repository=data_quality_repository)

        if not final_report.passed:
            return _handle_quality_blocked(
                request,
                db_session=db_session,
                collector_repository=active_collector_repository,
                event_log=event_log,
                report=final_report,
                quality_record=quality_record,
                fetched_count=fetched_count,
                parsed_count=len(parsed_klines),
                closed_count=len(closed_klines),
                filtered_unclosed_count=filtered_unclosed_count,
                alert_sender=alert_sender,
                alert_repository=alert_repository,
            )

        write_result = _write_when_needed(
            request,
            db_session=db_session,
            report=final_report,
            kline_repository=kline_repository,
        )
        return _handle_success(
            request,
            db_session=db_session,
            collector_repository=active_collector_repository,
            event_log=event_log,
            report=final_report,
            quality_record=quality_record,
            write_result=write_result,
            fetched_count=fetched_count,
            parsed_count=len(parsed_klines),
            closed_count=len(closed_klines),
            filtered_unclosed_count=filtered_unclosed_count,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    except Exception as exc:  # noqa: BLE001 - collection failures must be recorded and alerted.
        LOGGER.exception("4h Kline incremental collection failed trace_id=%s", request.trace_id)
        return _handle_task_failure(
            request,
            db_session=db_session,
            collector_repository=active_collector_repository,
            event_log=event_log,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
            fetched_count=fetched_count,
            parsed_klines=parsed_klines,
            closed_klines=closed_klines,
            filtered_unclosed_count=filtered_unclosed_count,
            report=final_report,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    finally:
        if lock_acquired:
            try:
                active_lock.release_lock(key=lock_key, owner=request.trace_id)
            except RedisError:
                LOGGER.exception("Failed to release Kline write lock key=%s trace_id=%s", lock_key, request.trace_id)


def validate_incremental_collect_request(request: IncrementalKlineCollectRequest) -> None:
    """Validate collector parameters before external access or writes."""

    if not request.symbol.strip():
        raise KlineCollectParameterError("symbol must not be empty")
    if request.interval_value != KLINE_4H_INTERVAL_VALUE:
        raise KlineCollectParameterError("interval must be 4h")
    if request.trigger_source not in ALLOWED_TRIGGER_SOURCES:
        raise KlineCollectParameterError("trigger_source must be cli or scheduler")
    if request.limit <= 0:
        raise KlineCollectParameterError("limit must be greater than 0")
    if request.max_limit <= 0:
        raise KlineCollectParameterError("max_limit must be greater than 0")
    if request.limit > request.max_limit:
        raise KlineCollectParameterError("limit must not exceed max_limit")
    if request.lock_ttl_seconds <= 0:
        raise KlineCollectParameterError("lock_ttl_seconds must be greater than 0")
    if not request.dry_run and not request.confirm_write:
        raise KlineCollectParameterError("confirm_write is required when dry_run is false")


def _try_acquire_lock(
    request: IncrementalKlineCollectRequest,
    *,
    db_session: Any,
    task_lock: Any,
    collector_repository: Any,
    lock_key: str,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> bool | IncrementalKlineCollectResult:
    try:
        acquired = task_lock.acquire_lock(
            key=lock_key,
            owner=request.trace_id,
            ttl_seconds=request.lock_ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - Redis failures must block formal writes.
        return _handle_task_failure(
            request,
            db_session=db_session,
            collector_repository=collector_repository,
            event_log=None,
            error_code="redis_lock_error",
            error_message=str(exc),
            fetched_count=0,
            parsed_klines=(),
            closed_klines=(),
            filtered_unclosed_count=0,
            report=None,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    if acquired:
        return True

    event_log = collector_repository.create_skipped_event(
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
        reason=f"task lock already held: {lock_key}",
        details={"lock_key": lock_key, "fetch_mode": "recent_closed_klines"},
    )
    _commit_if_possible(db_session)
    return IncrementalKlineCollectResult(
        status=KlineCollectStatus.SKIPPED,
        exit_code=EXIT_QUALITY_BLOCKED,
        trace_id=request.trace_id,
        message=f"Skipped because task lock is already held: {lock_key}",
        requested_count=request.requested_count,
        event_log_id=record_id(event_log),
        details={"lock_key": lock_key},
    )


def _create_running_event(
    db_session: Any,
    collector_repository: Any,
    request: IncrementalKlineCollectRequest,
    *,
    lock_key: str,
) -> Any:
    event_log = collector_repository.create_running_event(
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
        details={"lock_key": lock_key, "fetch_mode": "recent_closed_klines", "dry_run": request.dry_run},
    )
    _commit_if_possible(db_session)
    return event_log


def _handle_quality_blocked(
    request: IncrementalKlineCollectRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any,
    report: KlineQualityReport,
    quality_record: Any,
    fetched_count: int,
    parsed_count: int,
    closed_count: int,
    filtered_unclosed_count: int,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> IncrementalKlineCollectResult:
    event_log = collector_repository.mark_blocked(
        db_session,
        event_log,
        **event_values_from_collect_report(
            request,
            report,
            fetched_count=fetched_count,
            parsed_count=parsed_count,
            closed_count=closed_count,
            filtered_unclosed_count=filtered_unclosed_count,
            quality_check_id=record_id(quality_record),
        ),
        error_code="quality_blocked",
        error_message=report.first_issue.message if report.first_issue else "quality blocked",
        report_json=report.to_dict(),
    )
    _commit_if_possible(db_session)
    result = result_from_collect_report(
        request,
        report,
        status=KlineCollectStatus.BLOCKED,
        exit_code=EXIT_QUALITY_BLOCKED,
        message="Incremental Kline collection blocked by quality check",
        event_log_id=record_id(event_log),
        quality_check_id=record_id(quality_record),
        fetched_count=fetched_count,
        parsed_count=parsed_count,
        closed_count=closed_count,
        filtered_unclosed_count=filtered_unclosed_count,
    )
    return send_collect_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=report,
    )


def _write_when_needed(
    request: IncrementalKlineCollectRequest,
    *,
    db_session: Any,
    report: KlineQualityReport,
    kline_repository: Any | None,
) -> Any | None:
    if request.dry_run or not report.writable_klines:
        return None
    return persist_backfill_klines(db_session, report.writable_klines, repository=kline_repository)


def _handle_success(
    request: IncrementalKlineCollectRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any,
    report: KlineQualityReport,
    quality_record: Any,
    write_result: Any | None,
    fetched_count: int,
    parsed_count: int,
    closed_count: int,
    filtered_unclosed_count: int,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> IncrementalKlineCollectResult:
    inserted_count = int(getattr(write_result, "inserted_count", 0) or 0)
    skipped_existing_count = len(report.existing_open_time_ms) + int(
        getattr(write_result, "skipped_count", 0) or 0
    )
    success_event_values = event_values_from_collect_report(
        request,
        report,
        fetched_count=fetched_count,
        parsed_count=parsed_count,
        closed_count=closed_count,
        filtered_unclosed_count=filtered_unclosed_count,
        quality_check_id=record_id(quality_record),
    )
    success_event_values.update(
        {
            "inserted_count": inserted_count,
            "skipped_count": skipped_existing_count,
            "report_json": report.to_dict(),
            "details": {"dry_run": request.dry_run, "fetch_mode": "recent_closed_klines"},
        }
    )
    event_log = collector_repository.mark_success(db_session, event_log, **success_event_values)
    _commit_if_possible(db_session)
    result = result_from_collect_report(
        request,
        report,
        status=KlineCollectStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        message="Incremental Kline collection completed",
        event_log_id=record_id(event_log),
        quality_check_id=record_id(quality_record),
        fetched_count=fetched_count,
        parsed_count=parsed_count,
        closed_count=closed_count,
        filtered_unclosed_count=filtered_unclosed_count,
        inserted_count=inserted_count,
        skipped_existing_count=skipped_existing_count,
    )
    result = replace(
        result,
        details={
            **dict(result.details),
            "dry_run": request.dry_run,
            "formal_write_performed": (not request.dry_run and inserted_count > 0),
        },
    )
    if request.notify_success:
        return send_collect_success_alert_and_adjust_exit_code(
            request,
            result,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    return result


def _handle_task_failure(
    request: IncrementalKlineCollectRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any | None,
    error_code: str,
    error_message: str,
    fetched_count: int,
    parsed_klines: Sequence[MarketKlineDTO],
    closed_klines: Sequence[MarketKlineDTO],
    filtered_unclosed_count: int,
    report: KlineQualityReport | None,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> IncrementalKlineCollectResult:
    _rollback_if_possible(db_session)
    event_log_record_failed = False
    event_log_error_message: str | None = None
    try:
        event_log = mark_collect_existing_or_pre_execution_failure(
            db_session,
            collector_repository,
            request,
            event_log,
            error_code=error_code,
            error_message=error_message,
            fetched_count=fetched_count,
            parsed_klines=parsed_klines,
            closed_klines=closed_klines,
            filtered_unclosed_count=filtered_unclosed_count,
            report=report,
        )
        _commit_if_possible(db_session)
    except Exception as event_log_exc:  # noqa: BLE001 - alert even when event logging fails.
        event_log_record_failed = True
        event_log_error_message = str(event_log_exc)
        LOGGER.exception(
            "collector_event_log failure while handling incremental collection failure trace_id=%s",
            request.trace_id,
        )
        _rollback_if_possible(db_session)
    exit_code = EXIT_PERSIST_FAILED if error_code == "KlineBackfillPersistError" else EXIT_TASK_FAILED
    result = build_failed_collect_result(
        request,
        event_log=event_log,
        exit_code=exit_code,
        message=error_message,
        error_code=error_code,
        fetched_count=fetched_count,
        parsed_count=len(parsed_klines),
        closed_count=len(closed_klines),
    )
    if event_log_record_failed:
        result = replace(
            result,
            details={
                **dict(result.details),
                "event_log_record_failed": True,
                "event_log_record_error": event_log_error_message or "",
                "formal_write_performed": False,
            },
        )
    return send_collect_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=report,
    )


def _record_quality_report(db_session: Any, report: KlineQualityReport, *, repository: Any | None) -> Any:
    active_repository = repository or _default_data_quality_repository()
    return active_repository.create_quality_check_record(db_session, report)


def _commit_if_possible(db_session: Any) -> None:
    if hasattr(db_session, "commit"):
        db_session.commit()


def _rollback_if_possible(db_session: Any) -> None:
    if hasattr(db_session, "rollback"):
        db_session.rollback()


def _default_binance_client() -> Any:
    from app.exchange.binance.rest_client import BinanceRestClient

    return BinanceRestClient()


def _default_collector_event_repository() -> Any:
    from app.storage.mysql.repositories.collector_event_log_repository import CollectorEventLogRepository

    return CollectorEventLogRepository()


def _default_data_quality_repository() -> Any:
    from app.storage.mysql.repositories.data_quality_check_repository import DataQualityCheckRepository

    return DataQualityCheckRepository()


__all__ = [
    "format_incremental_collect_result_lines",
    "run_incremental_4h_collection",
    "validate_incremental_collect_request",
]
