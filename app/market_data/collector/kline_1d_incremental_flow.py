"""Flow helpers for BTCUSDT 1d incremental Kline collection.

This file belongs to `app/market_data/collector`. It owns event-log updates,
result construction, formal 1d persistence delegation, and fixed-template alert
dispatch decisions for the 1d incremental collector. It does not request
Binance, create scheduler jobs, write Redis except through caller-owned locks,
call DeepSeek, repair Klines, or trade. It is called only by
`kline_1d_incremental_collector.py::run_incremental_1d_collection`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from app.market_data.backfill.kline_1d_persistence import persist_1d_backfill_klines
from app.market_data.collector.kline_1d_incremental_alerts import (
    send_incremental_1d_failure_alert_and_adjust_exit_code,
    send_incremental_1d_success_alert_and_adjust_exit_code,
)
from app.market_data.collector.kline_1d_incremental_quality import (
    Kline1dIncrementalQualityOutcome,
    event_values_from_incremental_1d_quality_outcome,
)
from app.market_data.collector.kline_1d_incremental_types import (
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_BLOCKED,
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    IncrementalKline1dCollectRequest,
    IncrementalKline1dCollectResult,
    KLINE_1D_INCREMENTAL_EVENT_TYPE,
    KlineCollectStatus,
)
from app.market_data.kline_constants import KLINE_1D_INTERVAL_MS
from app.market_data.kline_dto import MarketKlineDTO
from app.market_data.kline_quality.types import KlineQualityReport


def try_acquire_incremental_1d_lock(
    request: IncrementalKline1dCollectRequest,
    *,
    db_session: Any,
    task_lock: Any,
    collector_repository: Any,
    lock_key: str,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> bool | IncrementalKline1dCollectResult:
    """Acquire the symbol+interval write lock or return a skipped/failed result."""

    try:
        acquired = task_lock.acquire_lock(
            key=lock_key,
            owner=request.trace_id,
            ttl_seconds=request.lock_ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - Redis failures must block formal writes.
        event_log = record_incremental_1d_pre_execution_failure(
            db_session,
            collector_repository,
            request,
            error_code="redis_lock_error",
            error_message=str(exc),
        )
        commit_if_possible(db_session)
        result = build_failed_incremental_1d_result(
            request,
            event_log=event_log,
            exit_code=EXIT_TASK_FAILED,
            message=f"Redis task lock error: {exc}",
            error_code="redis_lock_error",
        )
        return maybe_send_incremental_1d_failure_alert_and_adjust_exit_code(
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
        event_type=KLINE_1D_INCREMENTAL_EVENT_TYPE,
        symbol=request.symbol,
        interval_value=request.interval_value,
        trigger_source=request.trigger_source,
        data_source=request.data_source,
        requested_start_open_time_ms=0,
        requested_end_open_time_ms=0,
        requested_count=0,
        trace_id=request.trace_id,
        reason=f"task lock already held: {lock_key}",
        details={"lock_key": lock_key, "fetch_mode": "overlap_latest_db_to_expected_latest_closed_1d"},
    )
    commit_if_possible(db_session)
    return IncrementalKline1dCollectResult(
        status=KlineCollectStatus.SKIPPED,
        exit_code=EXIT_QUALITY_BLOCKED,
        trace_id=request.trace_id,
        message=f"Skipped because task lock is already held: {lock_key}",
        event_log_id=record_id(event_log),
        details={"lock_key": lock_key, "formal_write_performed": False},
    )


def create_incremental_1d_running_event(
    db_session: Any,
    collector_repository: Any,
    request: IncrementalKline1dCollectRequest,
    *,
    lock_key: str,
    requested_start_open_time_ms: int,
    requested_end_open_time_ms: int,
    requested_count: int,
) -> Any:
    """Create a running `kline_1d_incremental` collector_event_log record."""

    event_log = collector_repository.create_running_event(
        db_session,
        event_type=KLINE_1D_INCREMENTAL_EVENT_TYPE,
        symbol=request.symbol,
        interval_value=request.interval_value,
        trigger_source=request.trigger_source,
        data_source=request.data_source,
        requested_start_open_time_ms=requested_start_open_time_ms,
        requested_end_open_time_ms=requested_end_open_time_ms,
        requested_count=requested_count,
        trace_id=request.trace_id,
        details={
            "lock_key": lock_key,
            "dry_run": request.dry_run,
            "fetch_mode": "overlap_latest_db_to_expected_latest_closed_1d",
        },
    )
    commit_if_possible(db_session)
    return event_log


def handle_incremental_1d_pre_fetch_blocked(
    request: IncrementalKline1dCollectRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any,
    message: str,
    issue_type: str,
    alert_sender: Any | None,
    alert_repository: Any | None,
    details: dict[str, Any] | None = None,
) -> IncrementalKline1dCollectResult:
    """Mark a pre-fetch blocked state, such as empty 1d table or future latest row."""

    event_log = collector_repository.mark_blocked(
        db_session,
        event_log,
        issue_count=1,
        first_issue_type=issue_type,
        first_issue_message=message,
        error_code=issue_type,
        error_message=message,
        details={**dict(details or {}), "dry_run": request.dry_run},
    )
    commit_if_possible(db_session)
    result = IncrementalKline1dCollectResult(
        status=KlineCollectStatus.BLOCKED,
        exit_code=EXIT_QUALITY_BLOCKED,
        trace_id=request.trace_id,
        message=message,
        issue_count=1,
        first_issue_type=issue_type,
        first_issue_message=message,
        event_log_id=record_id(event_log),
        details={**dict(details or {}), "dry_run": request.dry_run, "formal_write_performed": False},
    )
    return maybe_send_incremental_1d_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
    )


def handle_incremental_1d_noop_success(
    request: IncrementalKline1dCollectRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any,
    latest_open_time_ms: int,
    expected_latest_open_time_ms: int,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> IncrementalKline1dCollectResult:
    """Mark success when the database already has the latest closed 1d Kline."""

    values = {
        "fetched_count": 0,
        "parsed_count": 0,
        "closed_count": 0,
        "filtered_unclosed_count": 0,
        "inserted_count": 0,
        "skipped_count": 0,
        "issue_count": 0,
        "actual_start_open_time_ms": latest_open_time_ms,
        "actual_end_open_time_ms": expected_latest_open_time_ms,
        "details": {
            "dry_run": request.dry_run,
            "formal_write_performed": False,
            "latest_open_time_ms": latest_open_time_ms,
            "expected_latest_open_time_ms": expected_latest_open_time_ms,
            "fetch_mode": "already_up_to_date_no_rest_klines_requested",
        },
    }
    event_log = collector_repository.mark_success(db_session, event_log, **values)
    commit_if_possible(db_session)
    result = IncrementalKline1dCollectResult(
        status=KlineCollectStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        trace_id=request.trace_id,
        message="1d Kline already up to date",
        event_log_id=record_id(event_log),
        details=values["details"],
    )
    if request.notify_success:
        return send_incremental_1d_success_alert_and_adjust_exit_code(
            request,
            result,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    return result


def handle_incremental_1d_quality_blocked(
    request: IncrementalKline1dCollectRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any,
    outcome: Kline1dIncrementalQualityOutcome,
    quality_record: Any,
    fetched_count: int,
    requested_start_open_time_ms: int,
    requested_end_open_time_ms: int,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> IncrementalKline1dCollectResult:
    """Persist a blocked quality report and send the fixed failure alert."""

    report = outcome.report
    event_log = collector_repository.mark_blocked(
        db_session,
        event_log,
        **event_values_from_incremental_1d_quality_outcome(
            request,
            outcome,
            start_open_time_ms=requested_start_open_time_ms,
            end_open_time_ms=requested_end_open_time_ms,
            fetched_count=fetched_count,
            quality_check_id=record_id(quality_record),
        ),
        error_code="quality_blocked",
        error_message=report.first_issue.message if report.first_issue else "quality blocked",
        report_json=report.to_dict(),
    )
    commit_if_possible(db_session)
    result = result_from_incremental_1d_outcome(
        request,
        outcome,
        status=KlineCollectStatus.BLOCKED,
        exit_code=EXIT_QUALITY_BLOCKED,
        message="Incremental 1d Kline collection blocked by quality check",
        event_log_id=record_id(event_log),
        quality_check_id=record_id(quality_record),
        fetched_count=fetched_count,
        requested_start_open_time_ms=requested_start_open_time_ms,
        requested_end_open_time_ms=requested_end_open_time_ms,
    )
    return maybe_send_incremental_1d_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=report,
    )


def persist_incremental_1d_klines_when_needed(
    request: IncrementalKline1dCollectRequest,
    *,
    db_session: Any,
    outcome: Kline1dIncrementalQualityOutcome,
    kline_repository: Any | None,
) -> Any | None:
    """Persist writable 1d Klines only for confirmed non-dry-run collections."""

    if request.dry_run or not outcome.report.writable_klines:
        return None
    return persist_1d_backfill_klines(
        db_session,
        outcome.report.writable_klines,
        repository=kline_repository,
    )


def handle_incremental_1d_success(
    request: IncrementalKline1dCollectRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any,
    outcome: Kline1dIncrementalQualityOutcome,
    quality_record: Any,
    write_result: Any | None,
    fetched_count: int,
    requested_start_open_time_ms: int,
    requested_end_open_time_ms: int,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> IncrementalKline1dCollectResult:
    """Mark success and optionally send the compact success alert."""

    inserted_count = int(getattr(write_result, "inserted_count", 0) or 0)
    skipped_existing_count = len(outcome.report.existing_open_time_ms) + int(
        getattr(write_result, "skipped_count", 0) or 0
    )
    success_event_values = event_values_from_incremental_1d_quality_outcome(
        request,
        outcome,
        start_open_time_ms=requested_start_open_time_ms,
        end_open_time_ms=requested_end_open_time_ms,
        fetched_count=fetched_count,
        quality_check_id=record_id(quality_record),
    )
    success_event_values.update(
        {
            "inserted_count": inserted_count,
            "skipped_count": skipped_existing_count,
            "report_json": outcome.report.to_dict(),
            "details": {
                "dry_run": request.dry_run,
                "formal_write_performed": (not request.dry_run and inserted_count > 0),
                "filtered_unclosed_count": outcome.filtered_unclosed_count,
                "requested_start_open_time_ms": requested_start_open_time_ms,
                "requested_end_open_time_ms": requested_end_open_time_ms,
            },
        }
    )
    event_log = collector_repository.mark_success(db_session, event_log, **success_event_values)
    commit_if_possible(db_session)
    result = result_from_incremental_1d_outcome(
        request,
        outcome,
        status=KlineCollectStatus.SUCCESS,
        exit_code=EXIT_SUCCESS,
        message="Incremental 1d Kline collection completed",
        event_log_id=record_id(event_log),
        quality_check_id=record_id(quality_record),
        fetched_count=fetched_count,
        inserted_count=inserted_count,
        skipped_existing_count=skipped_existing_count,
        requested_start_open_time_ms=requested_start_open_time_ms,
        requested_end_open_time_ms=requested_end_open_time_ms,
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
        return send_incremental_1d_success_alert_and_adjust_exit_code(
            request,
            result,
            db_session=db_session,
            alert_sender=alert_sender,
            alert_repository=alert_repository,
        )
    return result


def handle_incremental_1d_task_failure(
    request: IncrementalKline1dCollectRequest,
    *,
    db_session: Any,
    collector_repository: Any,
    event_log: Any | None,
    error_code: str,
    error_message: str,
    fetched_count: int,
    parsed_klines: Sequence[MarketKlineDTO],
    outcome: Kline1dIncrementalQualityOutcome | None,
    requested_start_open_time_ms: int,
    requested_end_open_time_ms: int,
    requested_count: int,
    alert_sender: Any | None,
    alert_repository: Any | None,
) -> IncrementalKline1dCollectResult:
    """Record task failure, roll back caller session, and send the fixed alert."""

    rollback_if_possible(db_session)
    event_log_record_failed = False
    event_log_error_message: str | None = None
    try:
        event_log = mark_incremental_1d_existing_or_pre_execution_failure(
            db_session,
            collector_repository,
            request,
            event_log,
            error_code=error_code,
            error_message=error_message,
            fetched_count=fetched_count,
            parsed_klines=parsed_klines,
            outcome=outcome,
            requested_start_open_time_ms=requested_start_open_time_ms,
            requested_end_open_time_ms=requested_end_open_time_ms,
            requested_count=requested_count,
        )
        commit_if_possible(db_session)
    except Exception as event_log_exc:  # noqa: BLE001 - alert even when event logging fails.
        event_log_record_failed = True
        event_log_error_message = str(event_log_exc)
        rollback_if_possible(db_session)

    exit_code = EXIT_PERSIST_FAILED if error_code == "KlineBackfillPersistError" else EXIT_TASK_FAILED
    result = build_failed_incremental_1d_result(
        request,
        event_log=event_log,
        exit_code=exit_code,
        message=error_message,
        error_code=error_code,
        requested_count=requested_count,
        fetched_count=fetched_count,
        parsed_count=len(parsed_klines),
        closed_count=outcome.closed_count if outcome is not None else len(parsed_klines),
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
    return maybe_send_incremental_1d_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=outcome.report if outcome is not None else None,
    )


def maybe_send_incremental_1d_failure_alert_and_adjust_exit_code(
    request: IncrementalKline1dCollectRequest,
    result: IncrementalKline1dCollectResult,
    *,
    db_session: Any,
    alert_sender: Any | None,
    alert_repository: Any | None,
    report: KlineQualityReport | None = None,
) -> IncrementalKline1dCollectResult:
    """Send failure alerts only for real-write attempts."""

    if request.dry_run:
        return replace(
            result,
            details={**dict(result.details), "alert_skipped_reason": "dry_run"},
        )
    return send_incremental_1d_failure_alert_and_adjust_exit_code(
        request,
        result,
        db_session=db_session,
        alert_sender=alert_sender,
        alert_repository=alert_repository,
        report=report,
    )


def record_incremental_1d_pre_execution_failure(
    db_session: Any,
    collector_repository: Any,
    request: IncrementalKline1dCollectRequest,
    *,
    error_code: str,
    error_message: str,
) -> Any:
    """Create and fail an event before Binance or formal writes."""

    event = collector_repository.create_running_event(
        db_session,
        event_type=KLINE_1D_INCREMENTAL_EVENT_TYPE,
        symbol=request.symbol,
        interval_value=request.interval_value,
        trigger_source=request.trigger_source,
        data_source=request.data_source,
        requested_start_open_time_ms=0,
        requested_end_open_time_ms=0,
        requested_count=0,
        trace_id=request.trace_id,
        details={"stage": "pre_execution"},
    )
    return collector_repository.mark_failed(
        db_session,
        event,
        error_code=error_code,
        error_message=error_message,
    )


def mark_incremental_1d_existing_or_pre_execution_failure(
    db_session: Any,
    collector_repository: Any,
    request: IncrementalKline1dCollectRequest,
    event_log: Any | None,
    *,
    error_code: str,
    error_message: str,
    fetched_count: int,
    parsed_klines: Sequence[MarketKlineDTO],
    outcome: Kline1dIncrementalQualityOutcome | None,
    requested_start_open_time_ms: int,
    requested_end_open_time_ms: int,
    requested_count: int,
) -> Any:
    """Fail an existing event or create a pre-execution failed event."""

    if event_log is None:
        return record_incremental_1d_pre_execution_failure(
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
            event_values_from_incremental_1d_quality_outcome(
                request,
                outcome,
                start_open_time_ms=requested_start_open_time_ms,
                end_open_time_ms=requested_end_open_time_ms,
                fetched_count=fetched_count,
            )
        )
        values["report_json"] = outcome.report.to_dict()
    else:
        values["details"] = {
            "requested_start_open_time_ms": requested_start_open_time_ms,
            "requested_end_open_time_ms": requested_end_open_time_ms,
            "requested_count": requested_count,
            "formal_write_performed": False,
        }
    return collector_repository.mark_failed(db_session, event_log, **values)


def record_incremental_1d_quality_report(db_session: Any, report: KlineQualityReport, *, repository: Any | None) -> Any:
    """Persist one data_quality_check record through the caller-supplied repository."""

    active_repository = repository or default_data_quality_repository()
    return active_repository.create_quality_check_record(db_session, report)


def result_from_incremental_1d_outcome(
    request: IncrementalKline1dCollectRequest,
    outcome: Kline1dIncrementalQualityOutcome,
    *,
    status: KlineCollectStatus,
    exit_code: int,
    message: str,
    event_log_id: int | None,
    quality_check_id: int | None,
    fetched_count: int,
    requested_start_open_time_ms: int,
    requested_end_open_time_ms: int,
    inserted_count: int = 0,
    skipped_existing_count: int | None = None,
) -> IncrementalKline1dCollectResult:
    """Build a result from a 1d incremental quality outcome."""

    report = outcome.report
    first_issue = report.first_issue
    requested_count = ((requested_end_open_time_ms - requested_start_open_time_ms) // KLINE_1D_INTERVAL_MS) + 1
    return IncrementalKline1dCollectResult(
        status=status,
        exit_code=exit_code,
        trace_id=request.trace_id,
        message=message,
        requested_count=requested_count,
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
        details={
            "requested_start_open_time_ms": requested_start_open_time_ms,
            "requested_end_open_time_ms": requested_end_open_time_ms,
            "formal_write_performed": False,
        },
    )


def build_failed_incremental_1d_result(
    request: IncrementalKline1dCollectRequest,
    *,
    event_log: Any | None,
    exit_code: int,
    message: str,
    error_code: str,
    requested_count: int = 0,
    fetched_count: int = 0,
    parsed_count: int = 0,
    closed_count: int = 0,
) -> IncrementalKline1dCollectResult:
    """Build a failed result outside the normal quality-report flow."""

    return IncrementalKline1dCollectResult(
        status=KlineCollectStatus.FAILED,
        exit_code=exit_code,
        trace_id=request.trace_id,
        message=message,
        requested_count=requested_count,
        fetched_count=fetched_count,
        parsed_count=parsed_count,
        closed_count=closed_count,
        filtered_unclosed_count=max(0, parsed_count - closed_count),
        event_log_id=record_id(event_log),
        details={"error_code": error_code, "formal_write_performed": False},
    )


def record_id(record: Any | None) -> int | None:
    """Return an integer id from ORM rows or fake test rows."""

    value = getattr(record, "id", None)
    return int(value) if value is not None else None


def commit_if_possible(db_session: Any) -> None:
    """Commit a caller-owned session if it exposes `commit`."""

    if hasattr(db_session, "commit"):
        db_session.commit()


def rollback_if_possible(db_session: Any) -> None:
    """Roll back a caller-owned session if it exposes `rollback`."""

    if hasattr(db_session, "rollback"):
        db_session.rollback()


def default_collector_event_repository() -> Any:
    """Create the default collector_event_log repository."""

    from app.storage.mysql.repositories.collector_event_log_repository import CollectorEventLogRepository

    return CollectorEventLogRepository()


def default_data_quality_repository() -> Any:
    """Create the default data_quality_check repository."""

    from app.storage.mysql.repositories.data_quality_check_repository import DataQualityCheckRepository

    return DataQualityCheckRepository()


def default_kline_1d_repository() -> Any:
    """Create the default market_kline_1d repository."""

    from app.storage.mysql.repositories.market_kline_1d_repository import MarketKline1dRepository

    return MarketKline1dRepository()
