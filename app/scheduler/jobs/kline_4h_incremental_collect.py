"""Scheduler job wrapper for phase-09 4h Kline incremental collection.

This file belongs to `app/scheduler/jobs`. It is called by the phase-12
scheduler runner and directly invokes
`app/market_data/collector/kline_4h_collector_service.py::run_incremental_4h_collection`.
It does not call scripts, request Binance directly, write repositories directly,
send Hermes directly, read/write Redis directly, call DeepSeek, repair Klines,
generate advice, or perform trading. All business effects are delegated to the
phase-09 service.
"""

from __future__ import annotations

from typing import Any, Callable

from app.core.config import AppSettings, get_settings
from app.market_data.collector.types import (
    EXIT_SUCCESS,
    IncrementalKlineCollectRequest,
    IncrementalKlineCollectResult,
    KlineCollectStatus,
)
from app.market_data.kline_constants import TRIGGER_SOURCE_SCHEDULER


ServiceRunner = Callable[..., IncrementalKlineCollectResult]


def run_kline_4h_incremental_collect_job(
    *,
    db_session: Any | None = None,
    settings: AppSettings | None = None,
    service_runner: ServiceRunner | None = None,
) -> IncrementalKlineCollectResult:
    """Run one scheduler-triggered 09 incremental collection job.

    Parameters: optional caller-owned database session, optional settings, and
    optional service runner for tests.
    Return value: the phase-09 service result, or a skipped result when disabled.
    Failure scenarios: service failures are represented in the returned result;
    session setup failures propagate to the runner wrapper.
    External service access and data effects are delegated only to phase-09.
    """

    active_settings = settings or get_settings()
    request = IncrementalKlineCollectRequest(
        symbol=active_settings.kline_4h_incremental_collect_symbol.strip().upper(),
        interval_value=active_settings.kline_4h_incremental_collect_interval,
        trigger_source=TRIGGER_SOURCE_SCHEDULER,
        limit=active_settings.kline_4h_incremental_collect_limit,
        confirm_write=True,
        dry_run=False,
        notify_success=False,
    )
    if not active_settings.kline_4h_incremental_collect_enabled:
        return IncrementalKlineCollectResult(
            status=KlineCollectStatus.SKIPPED,
            exit_code=EXIT_SUCCESS,
            trace_id=request.trace_id,
            message="4h incremental collection scheduler job is disabled",
            requested_count=request.requested_count,
            details={"trigger_source": TRIGGER_SOURCE_SCHEDULER},
        )

    runner = service_runner or _default_service_runner()
    if db_session is not None:
        return runner(request, db_session=db_session)

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=False) as scoped_session:
        return runner(request, db_session=scoped_session)


def _default_service_runner() -> ServiceRunner:
    from app.market_data.collector.kline_4h_collector_service import run_incremental_4h_collection

    return run_incremental_4h_collection


__all__ = ["run_kline_4h_incremental_collect_job"]
