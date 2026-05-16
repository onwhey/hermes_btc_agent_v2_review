"""Scheduler job wrapper for stage-14 BTCUSDT 1d daily integrity checks.

This file belongs to `app/scheduler/jobs`. It is called by
`app/scheduler/runner.py::SchedulerRunner` and directly invokes
`app/market_data/kline_integrity/kline_1d_integrity_service.py::run_daily_1d_kline_integrity_check`.
It does not call scripts, request Binance, write repositories directly, send
Hermes directly, read/write Redis directly, call DeepSeek, repair Klines,
backfill Klines, generate advice, or perform trading. All business effects are
delegated to the read-only stage-14 1d integrity service.
"""

from __future__ import annotations

from typing import Any, Callable

from app.core.config import AppSettings, get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_SCHEDULER
from app.market_data.kline_integrity.kline_1d_integrity_types import (
    EXIT_SUCCESS,
    DailyKline1dIntegrityCheckRequest,
    DailyKline1dIntegrityCheckResult,
    DailyKline1dIntegrityStatus,
)


ServiceRunner = Callable[..., DailyKline1dIntegrityCheckResult]


def run_kline_1d_integrity_check_job(
    *,
    db_session: Any | None = None,
    settings: AppSettings | None = None,
    service_runner: ServiceRunner | None = None,
) -> DailyKline1dIntegrityCheckResult:
    """Run one scheduler-triggered read-only 1d integrity check.

    Parameters: optional caller-owned database session, optional settings, and
    optional service runner for tests.
    Return value: service result, or a skipped result when the job is disabled.
    Failure scenarios: service failures are represented by the returned result.
    External service access and data impact: delegated only to the 1d integrity
    service; this job never calls scripts or Binance.
    """

    active_settings = settings or get_settings()
    request = DailyKline1dIntegrityCheckRequest(
        symbol=active_settings.daily_kline_1d_integrity_symbol.strip().upper(),
        interval_value=active_settings.daily_kline_1d_integrity_interval,
        lookback_count=active_settings.daily_kline_1d_integrity_limit,
        check_trigger=TRIGGER_SOURCE_SCHEDULER,
        notify_success=active_settings.daily_kline_1d_integrity_notify_success,
        lock_ttl_seconds=active_settings.daily_kline_1d_integrity_lock_ttl_seconds,
    )
    if not active_settings.daily_kline_1d_integrity_enabled:
        return DailyKline1dIntegrityCheckResult(
            status=DailyKline1dIntegrityStatus.SKIPPED,
            exit_code=EXIT_SUCCESS,
            trace_id=request.trace_id,
            message="1d daily integrity scheduler job is disabled",
            requested_count=request.requested_count,
            details={"check_trigger": TRIGGER_SOURCE_SCHEDULER, "interval_value": request.interval_value},
        )

    runner = service_runner or _default_service_runner()
    if db_session is not None:
        return runner(request, db_session=db_session)

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=True) as scoped_session:
        return runner(request, db_session=scoped_session)


def _default_service_runner() -> ServiceRunner:
    from app.market_data.kline_integrity.kline_1d_integrity_service import run_daily_1d_kline_integrity_check

    return run_daily_1d_kline_integrity_check


__all__ = ["run_kline_1d_integrity_check_job"]
