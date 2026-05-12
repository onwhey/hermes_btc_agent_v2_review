"""Scheduler job for the phase-11 daily Kline integrity review.

This file belongs to `app/scheduler/jobs`.
It is called by a scheduler runner and directly invokes
`app/market_data/kline_integrity/kline_integrity_service.py::run_daily_kline_integrity_check`.
It does not call scripts, request Binance directly, write repositories directly,
send Hermes directly, read/write Redis, call DeepSeek, repair or backfill Klines,
or perform trading execution. Database and Hermes effects are delegated to the
market-data service.
"""

from __future__ import annotations

from typing import Any, Callable

from app.core.config import AppSettings, get_settings
from app.market_data.kline_integrity.types import (
    EXIT_SUCCESS,
    DailyKlineIntegrityCheckRequest,
    DailyKlineIntegrityCheckResult,
    DailyKlineIntegrityStatus,
)
from app.market_data.kline_quality.types import CHECK_TRIGGER_SOURCE_SCHEDULER


ServiceRunner = Callable[..., DailyKlineIntegrityCheckResult]


def run_daily_kline_integrity_check_job(
    *,
    db_session: Any | None = None,
    settings: AppSettings | None = None,
    service_runner: ServiceRunner | None = None,
) -> DailyKlineIntegrityCheckResult:
    """Run the scheduler job by calling the daily integrity service directly.

    Parameters: optional caller-owned session, optional settings, and optional
    service runner for tests.
    Return value: service result, or a skipped result when the job is disabled.
    Failure scenarios: service failures are represented by the returned result.
    External service access and data impact: delegated only to the service.
    """

    active_settings = settings or get_settings()
    request = DailyKlineIntegrityCheckRequest(
        symbol=active_settings.daily_kline_integrity_symbol.strip().upper(),
        interval_value=active_settings.daily_kline_integrity_interval,
        limit=active_settings.daily_kline_integrity_limit,
        check_trigger_source=CHECK_TRIGGER_SOURCE_SCHEDULER,
        notify_success=active_settings.daily_kline_integrity_notify_success,
    )
    if not active_settings.daily_kline_integrity_enabled:
        return DailyKlineIntegrityCheckResult(
            status=DailyKlineIntegrityStatus.SKIPPED,
            exit_code=EXIT_SUCCESS,
            trace_id=request.trace_id,
            message="Daily Kline integrity scheduler job is disabled",
            requested_count=request.requested_count,
            details={"configured_trigger_source": active_settings.daily_kline_integrity_trigger_source},
        )

    runner = service_runner or _default_service_runner()
    if db_session is not None:
        return runner(request, db_session=db_session)

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=True) as scoped_session:
        return runner(request, db_session=scoped_session)


def _default_service_runner() -> ServiceRunner:
    from app.market_data.kline_integrity.kline_integrity_service import run_daily_kline_integrity_check

    return run_daily_kline_integrity_check


__all__ = ["run_daily_kline_integrity_check_job"]
