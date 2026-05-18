"""Thin scheduler job wrapper for stage-17 strategy signal orchestration.

This file belongs to `app/scheduler/jobs`. It is called only by
`app/scheduler/runner.py` after a 4h or 1d incremental collector job has
returned success. It is not an independent fixed-time scheduler task and it
must not be called through the strategy CLI.

It opens a MySQL session and delegates all stage-17 business logic to
`app/scheduler/strategy_signal_scheduler_service.py::run_strategy_signal_scheduler_after_collect`.
It does not request Binance, write formal Kline tables, write Redis, call
DeepSeek or any large language model, generate final trading advice, or perform
trading.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from app.core.config import AppSettings, get_settings
from app.core.time_utils import UTC, now_utc
from app.scheduler.config import SchedulerRuntimeConfig, build_scheduler_runtime_config
from app.scheduler.strategy_signal_scheduler_service import run_strategy_signal_scheduler_after_collect
from app.scheduler.strategy_signal_scheduler_types import StrategySignalSchedulerRequest, StrategySignalSchedulerResult
from app.storage.mysql import session as mysql_session


def run_strategy_signal_scheduler_after_collect_job(
    *,
    upstream_job_name: str,
    upstream_result: Any,
    current_time_utc: datetime | None = None,
    settings: AppSettings | None = None,
    config: SchedulerRuntimeConfig | None = None,
    service: Any | None = None,
) -> StrategySignalSchedulerResult:
    """Run stage-17 orchestration after a successful collector job.

    Parameters: upstream scheduler job name, collector result, current UTC time,
    and optional injected dependencies.
    Return value: `StrategySignalSchedulerResult`.
    Failure scenarios: repository or strategy service failures are converted by
    the app service when possible.
    External effects: may write the scheduler event table, may call stage 16,
    and may send one Hermes notification according to config. It never calls
    scripts or requests market data.
    """

    active_settings = settings or get_settings()
    active_config = config or build_scheduler_runtime_config(active_settings)
    active_time = _ensure_utc(current_time_utc or now_utc())
    trace_id = str(getattr(upstream_result, "trace_id", "") or uuid4().hex)
    request = StrategySignalSchedulerRequest(
        upstream_job_name=upstream_job_name,
        current_time_utc=active_time,
        symbol=active_config.strategy_signal_symbol,
        base_interval_value=active_config.strategy_signal_base_interval,
        higher_interval_value=active_config.strategy_signal_higher_interval,
        upstream_trace_id=trace_id,
        upstream_collector_event_id=getattr(upstream_result, "event_log_id", None),
        trace_id=trace_id,
    )

    with mysql_session.session_scope(settings=active_settings) as db_session:
        return run_strategy_signal_scheduler_after_collect(
            db_session=db_session,
            request=request,
            service=service,
        )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("strategy signal scheduler job requires timezone-aware UTC time")
    return value.astimezone(UTC)


__all__ = ["run_strategy_signal_scheduler_after_collect_job"]
