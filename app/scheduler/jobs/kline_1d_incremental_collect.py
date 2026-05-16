"""Scheduler job wrapper for stage-14 BTCUSDT 1d incremental collection.

This file belongs to `app/scheduler/jobs`. It is called by
`app/scheduler/runner.py::SchedulerRunner` and directly invokes
`app/market_data/collector/kline_1d_incremental_collector.py::run_incremental_1d_collection`.
It does not call scripts, request Binance directly, write repositories directly,
send Hermes directly, read/write Redis directly, call DeepSeek, generate advice,
repair Klines, or perform trading. All business effects are delegated to the
stage-14 1d collector service.
"""

from __future__ import annotations

from typing import Any, Callable

from app.core.config import AppSettings, get_settings
from app.market_data.collector.kline_1d_incremental_types import (
    EXIT_SUCCESS,
    IncrementalKline1dCollectRequest,
    IncrementalKline1dCollectResult,
    KlineCollectStatus,
)
from app.market_data.kline_constants import TRIGGER_SOURCE_SCHEDULER


ServiceRunner = Callable[..., IncrementalKline1dCollectResult]


def run_kline_1d_incremental_collect_job(
    *,
    db_session: Any | None = None,
    settings: AppSettings | None = None,
    service_runner: ServiceRunner | None = None,
) -> IncrementalKline1dCollectResult:
    """Run one scheduler-triggered 1d incremental collection job.

    Parameters are injectable for tests. The return value is the service result,
    or a skipped result when the job is disabled. Session setup failures
    propagate to the runner wrapper. External service access and data effects
    are delegated only to the stage-14 collector service.
    """

    active_settings = settings or get_settings()
    request = IncrementalKline1dCollectRequest(
        symbol=active_settings.kline_1d_incremental_collect_symbol.strip().upper(),
        interval_value=active_settings.kline_1d_incremental_collect_interval,
        trigger_source=TRIGGER_SOURCE_SCHEDULER,
        dry_run=False,
        confirm_write=True,
        notify_success=False,
        max_closed_count=active_settings.kline_1d_incremental_collect_max_closed_count,
        lock_ttl_seconds=active_settings.kline_1d_incremental_collect_lock_ttl_seconds,
    )
    if not active_settings.kline_1d_incremental_collect_enabled:
        return IncrementalKline1dCollectResult(
            status=KlineCollectStatus.SKIPPED,
            exit_code=EXIT_SUCCESS,
            trace_id=request.trace_id,
            message="1d incremental collection scheduler job is disabled",
            details={"trigger_source": TRIGGER_SOURCE_SCHEDULER},
        )

    runner = service_runner or _default_service_runner()
    if db_session is not None:
        return runner(request, db_session=db_session)

    from app.storage.mysql.session import session_scope

    with session_scope(commit_on_success=False) as scoped_session:
        return runner(request, db_session=scoped_session)


def _default_service_runner() -> ServiceRunner:
    from app.market_data.collector.kline_1d_incremental_collector import run_incremental_1d_collection

    return run_incremental_1d_collection


__all__ = ["run_kline_1d_incremental_collect_job"]
