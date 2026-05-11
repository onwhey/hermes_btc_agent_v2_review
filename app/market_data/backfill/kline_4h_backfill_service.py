"""Manual BTCUSDT 4h Kline backfill service.

Call chain:
scripts/backfill_4h_klines.py::main
    -> app/market_data/backfill/kline_4h_backfill_service.py::run_manual_4h_backfill
    -> app/core/task_lock.py::RedisTaskLock.acquire_lock
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
    -> app/market_data/kline_parser.py::parse_binance_klines
    -> app/market_data/kline_quality/batch_checker.py::check_kline_batch_before_persist
    -> app/market_data/backfill/quality.py::check_backfill_quality
    -> app/storage/mysql/repositories/market_kline_4h_repository.py::bulk_upsert

This file belongs to `app/market_data/backfill`.
It orchestrates only user-triggered manual 4h Kline backfill from Binance REST.
It may request Binance public Klines, read/write MySQL event and Kline tables,
use Redis only for the Kline write task lock, and send fixed-template Hermes
alerts on blocked/failed outcomes. It does not implement scheduler jobs,
WebSocket price monitoring, Redis price cache, DeepSeek calls, strategy advice,
automatic repair, overwrite/delete of formal Klines, or trading execution.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.core.exceptions import RedisError
from app.core.logger import get_logger
from app.core.task_lock import RedisTaskLock, build_kline_write_lock_key
from app.market_data.backfill.alerts import (
    send_failure_alert_and_adjust_exit_code,
    send_success_alert_and_adjust_exit_code,
)
from app.market_data.backfill.exceptions import KlineBackfillError, KlineBackfillParameterError
from app.market_data.backfill.event_records import (
    mark_existing_or_pre_execution_failure,
    record_pre_execution_failure,
)
from app.market_data.backfill.persistence import persist_backfill_klines
from app.market_data.backfill.pipeline import (
    extract_server_time_ms,
    fetch_raw_klines_for_backfill,
    parse_backfill_klines,
    validate_backfill_request,
)
from app.market_data.backfill.quality import (
    check_backfill_quality,
    event_values_from_quality_report,
)
from app.market_data.backfill.results import (
    build_failed_result,
    format_manual_backfill_result_lines,
    record_id,
    result_from_report,
)
from app.market_data.backfill.types import (
    BACKFILL_EVENT_TYPE,
    EXIT_PARAMETER_ERROR,
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_BLOCKED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    KlineBackfillStatus,
    ManualKlineBackfillRequest,
    ManualKlineBackfillResult,
)
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.types import KlineQualityReport

LOGGER = get_logger("market_data.backfill.kline_4h")


def run_manual_4h_backfill(
    request: ManualKlineBackfillRequest,
    *,
    db_session: Any,
    binance_client: Any | None = None,
    task_lock: Any | None = None,
    kline_repository: Any | None = None,
    data_quality_repository: Any | None = None,
    collector_event_repository: Any | None = None,
    alert_sender: Any | None = None,
    alert_repository: Any | None = None,
) -> ManualKlineBackfillResult:
    """Run one all-or-nothing manual 4h Kline backfill.

    Parameters: request plus caller-owned session and injectable dependencies.
    Return value: structured result with status, counts, alert status, and exit code.
    Failure scenarios: parameter errors, lock failures, quality blocks, persistence
    errors, and unexpected task failures are converted to explicit non-zero results.
    External services: may call Binance, Redis task lock, MySQL, and Hermes only
    through injected/default dependencies.
    Data impact: formal Kline writes happen only after quality checks pass.
    """

    try:
        validate_backfill_request(request)
    except KlineBackfillParameterError as exc:
        return ManualKlineBackfillResult(
            status=KlineBackfillStatus.FAILED,
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
        if isinstance(lock_result, ManualKlineBackfillResult):
            return lock_result
        lock_acquired = True

        event_log = _create_running_event(
            db_session,
            active_collector_repository,
            request,
            lock_key=lock_key,
        )

        active_binance_client = binance_client or _default_binance_client()
        server_time_ms = extract_server_time_ms(active_binance_client.get_server_time())
        raw_klines = fetch_raw_klines_for_backfill(active_binance_client, request)
        fetched_count = len(raw_klines)
        parsed_klines = tuple(
            parse_backfill_klines(
                raw_klines,
                symbol=request.symbol,
                interval_value=request.interval_value,
                trigger_source=request.trigger_source,
            )
        )
        final_report = check_backfill_quality(
            db_session,
            parsed_klines,
            request=request,
            server_time_ms=server_time_ms,
            repository=kline_repository,
        )
        quality_record = _record_quality_report(
            db_session,
            final_report,
            repository=data_quality_repository,
        )

        if not final_report.passed:
            return _handle_quality_blocked(
                request,
                db_session=db_session,
                collector_repository=active_collector_repository,
                event_log=event_log,
                report=final_report,
                quality_record=quality_record,
                fetched_count=fetched_count,
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
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    except KlineBackfillError as exc:
        return _handle_task_failure(
            request,
            db_session=db_session,
            collector_repository=active_collector_repository,
            event_log=event_log,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
            fetched_count=fetched_count,
            parsed_klines=parsed_klines,
            report=final_report,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    except Exception as exc:  # noqa: BLE001 - task failures must be recorded and alerted.
        LOGGER.exception("Manual 4h Kline backfill failed trace_id=%s", request.trace_id)
        return _handle_task_failure(
            request,
            db_session=db_session,
            collector_repository=active_collector_repository,
            event_log=event_log,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
            fetched_count=fetched_count,
            parsed_klines=parsed_klines,
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


def _try_acquire_lock(
    request: ManualKlineBackfillRequest,
    *,
    db_session: Any,
    task_lock: Any,
    collector_repository: Any,
    lock_key: str,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> bool | ManualKlineBackfillResult:
    try:
        acquired = task_lock.acquire_lock(
            key=lock_key,
            owner=request.trace_id,
            ttl_seconds=request.lock_ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - Redis failures must block formal writes.
        event_log = record_pre_execution_failure(
            db_session,
            collector_repository,
            request,
            error_code="redis_lock_error",
            error_message=str(exc),
        )
        _commit_if_possible(db_session)
        result = build_failed_result(
            request,
            event_log=event_log,
            exit_code=EXIT_TASK_FAILED,
            message=f"Redis task lock error: {exc}",
            error_code="redis_lock_error",
        )
        return send_failure_alert_and_adjust_exit_code(
            request,
            result,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    if acquired:
        return True

    event_log = collector_repository.create_skipped_event(
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
        reason=f"task lock already held: {lock_key}",
        details={"lock_key": lock_key},
    )
    _commit_if_possible(db_session)
    return ManualKlineBackfillResult(
        status=KlineBackfillStatus.SKIPPED,
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
    request: ManualKlineBackfillRequest,
    *,
    lock_key: str,
) -> Any:
    event_log = collector_repository.create_running_event(
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
        details={"lock_key": lock_key, "dry_run": request.dry_run},
    )
    _commit_if_possible(db_session)
    return event_log


def _handle_quality_blocked(
    request: ManualKlineBackfillRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any,
    report: KlineQualityReport,
    quality_record: Any,
    fetched_count: int,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> ManualKlineBackfillResult:
    event_log = collector_repository.mark_blocked(
        db_session,
        event_log,
        **event_values_from_quality_report(
            request,
            report,
            fetched_count=fetched_count,
            quality_check_id=record_id(quality_record),
        ),
        error_code="quality_blocked",
        error_message=report.first_issue.message if report.first_issue else "quality blocked",
        report_json=report.to_dict(),
    )
    _commit_if_possible(db_session)
    result = result_from_report(
        request,
        report,
        status=KlineBackfillStatus.BLOCKED,
        exit_code=EXIT_QUALITY_BLOCKED,
        message="Manual Kline backfill blocked by quality check",
        event_log_id=record_id(event_log),
        quality_check_id=record_id(quality_record),
        fetched_count=fetched_count,
    )
    return send_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=report,
    )


def _write_when_needed(
    request: ManualKlineBackfillRequest,
    *,
    db_session: Any,
    report: KlineQualityReport,
    kline_repository: Any | None,
) -> Any | None:
    if request.dry_run or not report.writable_klines:
        return None
    return persist_backfill_klines(
        db_session,
        report.writable_klines,
        repository=kline_repository,
    )


def _handle_success(
    request: ManualKlineBackfillRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any,
    report: KlineQualityReport,
    quality_record: Any,
    write_result: Any | None,
    fetched_count: int,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> ManualKlineBackfillResult:
    inserted_count = int(getattr(write_result, "inserted_count", 0) or 0)
    skipped_existing_count = int(
        getattr(write_result, "skipped_count", len(report.existing_open_time_ms)) or 0
    )
    success_event_values = event_values_from_quality_report(
        request,
        report,
        fetched_count=fetched_count,
            quality_check_id=record_id(quality_record),
    )
    success_event_values.update(
        {
            "inserted_count": inserted_count,
            "skipped_count": skipped_existing_count,
            "report_json": report.to_dict(),
            "details": {"dry_run": request.dry_run},
        }
    )
    event_log = collector_repository.mark_success(db_session, event_log, **success_event_values)
    _commit_if_possible(db_session)

    result = result_from_report(
        request,
        report,
        status=KlineBackfillStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        message="Manual Kline backfill completed",
        event_log_id=record_id(event_log),
        quality_check_id=record_id(quality_record),
        fetched_count=fetched_count,
        inserted_count=inserted_count,
        skipped_existing_count=skipped_existing_count,
    )
    if request.notify_success:
        return send_success_alert_and_adjust_exit_code(
            request,
            result,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    return result


def _handle_task_failure(
    request: ManualKlineBackfillRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any | None,
    error_code: str,
    error_message: str,
    fetched_count: int,
    parsed_klines: Sequence[MarketKlineDTO],
    report: KlineQualityReport | None,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> ManualKlineBackfillResult:
    _rollback_if_possible(db_session)
    event_log = mark_existing_or_pre_execution_failure(
        db_session,
        collector_repository,
        request,
        event_log,
        error_code=error_code,
        error_message=error_message,
        fetched_count=fetched_count,
        parsed_klines=parsed_klines,
        report=report,
    )
    _commit_if_possible(db_session)
    exit_code = EXIT_PERSIST_FAILED if error_code == "KlineBackfillPersistError" else EXIT_TASK_FAILED
    result = build_failed_result(
        request,
        event_log=event_log,
        exit_code=exit_code,
        message=error_message,
        error_code=error_code,
        fetched_count=fetched_count,
        parsed_count=len(parsed_klines),
    )
    return send_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=report,
    )


def _record_quality_report(
    db_session: Any,
    report: KlineQualityReport,
    *,
    repository: Any | None,
) -> Any:
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
    from app.storage.mysql.repositories.collector_event_log_repository import (
        CollectorEventLogRepository,
    )

    return CollectorEventLogRepository()


def _default_data_quality_repository() -> Any:
    from app.storage.mysql.repositories.data_quality_check_repository import (
        DataQualityCheckRepository,
    )

    return DataQualityCheckRepository()
