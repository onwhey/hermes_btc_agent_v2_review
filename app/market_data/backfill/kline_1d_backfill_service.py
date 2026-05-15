"""Manual BTCUSDT 1d Kline backfill service.

Call chain:
scripts/backfill_1d_klines.py::main
    -> app/market_data/backfill/kline_1d_backfill_service.py::run_manual_1d_backfill
    -> app/core/task_lock.py::RedisTaskLock.acquire_lock
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_server_time
    -> app/exchange/binance/rest_client.py::BinanceRestClient.get_klines
    -> app/market_data/kline_parser.py::parse_binance_klines
    -> app/market_data/backfill/kline_1d_quality.py::check_1d_backfill_quality
    -> app/storage/mysql/repositories/market_kline_1d_repository.py::bulk_upsert

This file belongs to `app/market_data/backfill`.
It orchestrates only user-triggered manual 1d Kline backfill from Binance REST.
It may request Binance public Klines, read/write MySQL event and 1d Kline tables,
use Redis only for the Kline write task lock, and send fixed-template Hermes
alerts on blocked/failed outcomes. It does not implement scheduler jobs,
WebSocket price monitoring, Redis price cache, DeepSeek calls, strategy advice,
automatic repair, overwrite/delete of formal Klines, or trading execution.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from app.core.exceptions import RedisError
from app.core.logger import get_logger
from app.core.task_lock import RedisTaskLock, build_kline_write_lock_key
from app.market_data.backfill.exceptions import KlineBackfillError, KlineBackfillParameterError
from app.market_data.backfill.kline_1d_alerts import (
    send_1d_failure_alert_and_adjust_exit_code,
    send_1d_success_alert_and_adjust_exit_code,
)
from app.market_data.backfill.kline_1d_persistence import persist_1d_backfill_klines
from app.market_data.backfill.kline_1d_pipeline import (
    extract_server_time_ms,
    fetch_raw_1d_klines_for_backfill,
    parse_1d_backfill_klines,
    validate_1d_backfill_request,
)
from app.market_data.backfill.kline_1d_quality import (
    Kline1dBackfillQualityOutcome,
    check_1d_backfill_quality,
    event_values_from_1d_quality_outcome,
)
from app.market_data.backfill.kline_1d_types import (
    BACKFILL_1D_EVENT_TYPE,
    EXIT_PARAMETER_ERROR,
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_BLOCKED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    Kline1dBackfillStatus,
    ManualKline1dBackfillRequest,
    ManualKline1dBackfillResult,
    format_manual_1d_backfill_result_lines,
)
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.types import KlineQualityReport

LOGGER = get_logger("market_data.backfill.kline_1d")


def run_manual_1d_backfill(
    request: ManualKline1dBackfillRequest,
    *,
    db_session: Any,
    binance_client: Any | None = None,
    task_lock: Any | None = None,
    kline_repository: Any | None = None,
    data_quality_repository: Any | None = None,
    collector_event_repository: Any | None = None,
    alert_sender: Any | None = None,
    alert_repository: Any | None = None,
) -> ManualKline1dBackfillResult:
    """Run one all-or-nothing manual 1d Kline backfill.

    Parameters: request plus caller-owned session and injectable dependencies.
    Return value: structured result with status, counts, alert status, and exit code.
    Failure scenarios: parameter errors, lock failures, quality blocks, persistence
    errors, and unexpected task failures are converted to explicit non-zero results.
    External services: may call Binance, Redis task lock, MySQL, and Hermes only
    through injected/default dependencies.
    Data impact: formal writes target only `market_kline_1d` after quality checks pass.
    """

    try:
        validate_1d_backfill_request(request)
    except KlineBackfillParameterError as exc:
        return ManualKline1dBackfillResult(
            status=Kline1dBackfillStatus.FAILED,
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
    final_outcome: Kline1dBackfillQualityOutcome | None = None

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
        if isinstance(lock_result, ManualKline1dBackfillResult):
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
        raw_klines = fetch_raw_1d_klines_for_backfill(active_binance_client, request)
        fetched_count = len(raw_klines)
        parsed_klines = tuple(
            parse_1d_backfill_klines(
                raw_klines,
                symbol=request.symbol,
                interval_value=request.interval_value,
                trigger_source=request.trigger_source,
            )
        )
        final_outcome = check_1d_backfill_quality(
            db_session,
            parsed_klines,
            request=request,
            server_time_ms=server_time_ms,
            repository=kline_repository,
        )
        quality_record = _record_quality_report(
            db_session,
            final_outcome.report,
            repository=data_quality_repository,
        )

        if not final_outcome.report.passed:
            return _handle_quality_blocked(
                request,
                db_session=db_session,
                collector_repository=active_collector_repository,
                event_log=event_log,
                outcome=final_outcome,
                quality_record=quality_record,
                fetched_count=fetched_count,
                alert_sender=alert_sender,
                alert_repository=alert_repository,
            )

        write_result = _write_when_needed(
            request,
            db_session=db_session,
            outcome=final_outcome,
            kline_repository=kline_repository,
        )
        return _handle_success(
            request,
            db_session=db_session,
            collector_repository=active_collector_repository,
            event_log=event_log,
            outcome=final_outcome,
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
            outcome=final_outcome,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    except Exception as exc:  # noqa: BLE001 - task failures must be recorded and alerted.
        LOGGER.exception("Manual 1d Kline backfill failed trace_id=%s", request.trace_id)
        return _handle_task_failure(
            request,
            db_session=db_session,
            collector_repository=active_collector_repository,
            event_log=event_log,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
            fetched_count=fetched_count,
            parsed_klines=parsed_klines,
            outcome=final_outcome,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    finally:
        if lock_acquired:
            try:
                active_lock.release_lock(key=lock_key, owner=request.trace_id)
            except RedisError:
                LOGGER.exception("Failed to release 1d Kline write lock key=%s trace_id=%s", lock_key, request.trace_id)


def _try_acquire_lock(
    request: ManualKline1dBackfillRequest,
    *,
    db_session: Any,
    task_lock: Any,
    collector_repository: Any,
    lock_key: str,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> bool | ManualKline1dBackfillResult:
    try:
        acquired = task_lock.acquire_lock(
            key=lock_key,
            owner=request.trace_id,
            ttl_seconds=request.lock_ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - Redis failures must block formal writes.
        event_log = _record_pre_execution_failure(
            db_session,
            collector_repository,
            request,
            error_code="redis_lock_error",
            error_message=str(exc),
        )
        _commit_if_possible(db_session)
        result = _build_failed_result(
            request,
            event_log=event_log,
            exit_code=EXIT_TASK_FAILED,
            message=f"Redis task lock error: {exc}",
            error_code="redis_lock_error",
        )
        return _maybe_send_1d_failure_alert_and_adjust_exit_code(
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
        event_type=BACKFILL_1D_EVENT_TYPE,
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
    return ManualKline1dBackfillResult(
        status=Kline1dBackfillStatus.SKIPPED,
        exit_code=EXIT_QUALITY_BLOCKED,
        trace_id=request.trace_id,
        message=f"Skipped because task lock is already held: {lock_key}",
        requested_count=request.requested_count,
        event_log_id=_record_id(event_log),
        details={"lock_key": lock_key, "formal_write_performed": False},
    )


def _create_running_event(
    db_session: Any,
    collector_repository: Any,
    request: ManualKline1dBackfillRequest,
    *,
    lock_key: str,
) -> Any:
    event_log = collector_repository.create_running_event(
        db_session,
        event_type=BACKFILL_1D_EVENT_TYPE,
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
    request: ManualKline1dBackfillRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any,
    outcome: Kline1dBackfillQualityOutcome,
    quality_record: Any,
    fetched_count: int,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> ManualKline1dBackfillResult:
    report = outcome.report
    event_log = collector_repository.mark_blocked(
        db_session,
        event_log,
        **event_values_from_1d_quality_outcome(
            request,
            outcome,
            fetched_count=fetched_count,
            quality_check_id=_record_id(quality_record),
        ),
        error_code="quality_blocked",
        error_message=report.first_issue.message if report.first_issue else "quality blocked",
        report_json=report.to_dict(),
    )
    _commit_if_possible(db_session)
    result = _result_from_outcome(
        request,
        outcome,
        status=Kline1dBackfillStatus.BLOCKED,
        exit_code=EXIT_QUALITY_BLOCKED,
        message="Manual 1d Kline backfill blocked by quality check",
        event_log_id=_record_id(event_log),
        quality_check_id=_record_id(quality_record),
        fetched_count=fetched_count,
    )
    return _maybe_send_1d_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=report,
    )


def _write_when_needed(
    request: ManualKline1dBackfillRequest,
    *,
    db_session: Any,
    outcome: Kline1dBackfillQualityOutcome,
    kline_repository: Any | None,
) -> Any | None:
    if request.dry_run or not outcome.report.writable_klines:
        return None
    return persist_1d_backfill_klines(
        db_session,
        outcome.report.writable_klines,
        repository=kline_repository,
    )


def _handle_success(
    request: ManualKline1dBackfillRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any,
    outcome: Kline1dBackfillQualityOutcome,
    quality_record: Any,
    write_result: Any | None,
    fetched_count: int,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> ManualKline1dBackfillResult:
    inserted_count = int(getattr(write_result, "inserted_count", 0) or 0)
    skipped_existing_count = int(
        getattr(write_result, "skipped_count", len(outcome.report.existing_open_time_ms)) or 0
    )
    success_event_values = event_values_from_1d_quality_outcome(
        request,
        outcome,
        fetched_count=fetched_count,
        quality_check_id=_record_id(quality_record),
    )
    success_event_values.update(
        {
            "inserted_count": inserted_count,
            "skipped_count": skipped_existing_count,
            "report_json": outcome.report.to_dict(),
            "details": {
                "dry_run": request.dry_run,
                "filtered_unclosed_count": outcome.filtered_unclosed_count,
            },
        }
    )
    event_log = collector_repository.mark_success(db_session, event_log, **success_event_values)
    _commit_if_possible(db_session)

    result = _result_from_outcome(
        request,
        outcome,
        status=Kline1dBackfillStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        message="Manual 1d Kline backfill completed",
        event_log_id=_record_id(event_log),
        quality_check_id=_record_id(quality_record),
        fetched_count=fetched_count,
        inserted_count=inserted_count,
        skipped_existing_count=skipped_existing_count,
    )
    result = replace(
        result,
        details={
            **dict(result.details),
            "dry_run": request.dry_run,
            "formal_write_performed": (not request.dry_run and inserted_count > 0),
            "filtered_unclosed_count": outcome.filtered_unclosed_count,
        },
    )
    if request.notify_success:
        return send_1d_success_alert_and_adjust_exit_code(
            request,
            result,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    return result


def _handle_task_failure(
    request: ManualKline1dBackfillRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any | None,
    error_code: str,
    error_message: str,
    fetched_count: int,
    parsed_klines: Sequence[MarketKlineDTO],
    outcome: Kline1dBackfillQualityOutcome | None,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> ManualKline1dBackfillResult:
    _rollback_if_possible(db_session)
    event_log_record_failed = False
    event_log_error_message: str | None = None
    try:
        event_log = _mark_existing_or_pre_execution_failure(
            db_session,
            collector_repository,
            request,
            event_log,
            error_code=error_code,
            error_message=error_message,
            fetched_count=fetched_count,
            parsed_klines=parsed_klines,
            outcome=outcome,
        )
        _commit_if_possible(db_session)
    except Exception as event_log_exc:  # noqa: BLE001 - alert even when event logging fails.
        event_log_record_failed = True
        event_log_error_message = str(event_log_exc)
        LOGGER.exception(
            "collector_event_log failure while handling manual 1d backfill failure trace_id=%s",
            request.trace_id,
        )
        _rollback_if_possible(db_session)

    exit_code = EXIT_PERSIST_FAILED if error_code == "KlineBackfillPersistError" else EXIT_TASK_FAILED
    result = _build_failed_result(
        request,
        event_log=event_log,
        exit_code=exit_code,
        message=error_message,
        error_code=error_code,
        fetched_count=fetched_count,
        parsed_count=len(parsed_klines),
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
    return _maybe_send_1d_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=outcome.report if outcome is not None else None,
    )


def _maybe_send_1d_failure_alert_and_adjust_exit_code(
    request: ManualKline1dBackfillRequest,
    result: ManualKline1dBackfillResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    report: KlineQualityReport | None = None,
) -> ManualKline1dBackfillResult:
    """Send failure alerts only for real-write attempts.

    Dry-run mode must not submit a real Hermes alert because it is an operator
    preview. Real write attempts still send blocked/failed alerts through the
    fixed template path.
    """

    if request.dry_run:
        return replace(
            result,
            details={
                **dict(result.details),
                "alert_skipped_reason": "dry_run",
            },
        )
    return send_1d_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=report,
    )


def _record_pre_execution_failure(
    db_session: Any,
    collector_repository: Any,
    request: ManualKline1dBackfillRequest,
    *,
    error_code: str,
    error_message: str,
) -> Any:
    event = collector_repository.create_running_event(
        db_session,
        event_type=BACKFILL_1D_EVENT_TYPE,
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


def _mark_existing_or_pre_execution_failure(
    db_session: Any,
    collector_repository: Any,
    request: ManualKline1dBackfillRequest,
    event_log: Any | None,
    *,
    error_code: str,
    error_message: str,
    fetched_count: int,
    parsed_klines: Sequence[MarketKlineDTO],
    outcome: Kline1dBackfillQualityOutcome | None,
) -> Any:
    if event_log is None:
        return _record_pre_execution_failure(
            db_session,
            collector_repository,
            request,
            error_code=error_code,
            error_message=error_message,
        )
    values: dict[str, Any] = {
        "fetched_count": fetched_count,
        "parsed_count": len(parsed_klines),
        "closed_count": outcome.closed_count if outcome is not None else len(parsed_klines),
        "filtered_unclosed_count": outcome.filtered_unclosed_count if outcome is not None else 0,
        "error_code": error_code,
        "error_message": error_message,
    }
    if outcome is not None:
        values.update(
            event_values_from_1d_quality_outcome(
                request,
                outcome,
                fetched_count=fetched_count,
            )
        )
        values["report_json"] = outcome.report.to_dict()
    return collector_repository.mark_failed(db_session, event_log, **values)


def _record_quality_report(
    db_session: Any,
    report: KlineQualityReport,
    *,
    repository: Any | None,
) -> Any:
    active_repository = repository or _default_data_quality_repository()
    return active_repository.create_quality_check_record(db_session, report)


def _result_from_outcome(
    request: ManualKline1dBackfillRequest,
    outcome: Kline1dBackfillQualityOutcome,
    *,
    status: Kline1dBackfillStatus,
    exit_code: int,
    message: str,
    event_log_id: int | None,
    quality_check_id: int | None,
    fetched_count: int,
    inserted_count: int = 0,
    skipped_existing_count: int | None = None,
) -> ManualKline1dBackfillResult:
    report = outcome.report
    first_issue = report.first_issue
    return ManualKline1dBackfillResult(
        status=status,
        exit_code=exit_code,
        trace_id=request.trace_id,
        message=message,
        requested_count=request.requested_count,
        fetched_count=fetched_count,
        parsed_count=outcome.parsed_count,
        closed_count=outcome.closed_count,
        filtered_unclosed_count=outcome.filtered_unclosed_count,
        writable_count=len(report.writable_klines),
        inserted_count=inserted_count,
        skipped_existing_count=(
            len(report.existing_open_time_ms)
            if skipped_existing_count is None
            else skipped_existing_count
        ),
        issue_count=report.issue_count,
        first_issue_type=first_issue.issue_type.value if first_issue else None,
        first_issue_message=first_issue.message if first_issue else None,
        event_log_id=event_log_id,
        quality_check_id=quality_check_id,
        details={"formal_write_performed": False},
    )


def _build_failed_result(
    request: ManualKline1dBackfillRequest,
    *,
    event_log: Any | None,
    exit_code: int,
    message: str,
    error_code: str,
    fetched_count: int = 0,
    parsed_count: int = 0,
) -> ManualKline1dBackfillResult:
    return ManualKline1dBackfillResult(
        status=Kline1dBackfillStatus.FAILED,
        exit_code=exit_code,
        trace_id=request.trace_id,
        message=message,
        requested_count=request.requested_count,
        fetched_count=fetched_count,
        parsed_count=parsed_count,
        closed_count=parsed_count,
        event_log_id=_record_id(event_log),
        details={"error_code": error_code, "formal_write_performed": False},
    )


def _record_id(record: Any | None) -> int | None:
    value = getattr(record, "id", None)
    return int(value) if value is not None else None


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


__all__ = [
    "format_manual_1d_backfill_result_lines",
    "run_manual_1d_backfill",
]
