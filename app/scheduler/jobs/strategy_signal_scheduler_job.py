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

from collections.abc import Mapping
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
    upstream_slot_time_utc: datetime,
    current_time_utc: datetime | None = None,
    settings: AppSettings | None = None,
    config: SchedulerRuntimeConfig | None = None,
    service: Any | None = None,
) -> StrategySignalSchedulerResult:
    """Run stage-17 orchestration after a successful collector job.

    Parameters: upstream scheduler job name, collector result, upstream
    scheduler slot UTC time, current UTC run time, and optional injected
    dependencies.
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
    active_slot_time = _ensure_utc(upstream_slot_time_utc)
    trace_id = str(getattr(upstream_result, "trace_id", "") or uuid4().hex)
    request = StrategySignalSchedulerRequest(
        upstream_job_name=upstream_job_name,
        current_time_utc=active_time,
        upstream_slot_time_utc=active_slot_time,
        symbol=active_config.strategy_signal_symbol,
        base_interval_value=active_config.strategy_signal_base_interval,
        higher_interval_value=active_config.strategy_signal_higher_interval,
        upstream_trace_id=trace_id,
        upstream_collector_event_id=getattr(upstream_result, "event_log_id", None),
        upstream_latest_base_open_time_ms=_extract_latest_base_open_time_ms(upstream_result),
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


def _extract_latest_base_open_time_ms(upstream_result: Any) -> int | None:
    """Return an explicit 4h collector target open time when the result has one.

    The first source of truth for stage-17 target binding is a clear collector
    result field. Current collector result objects normally expose only counts
    and event IDs, so this helper also accepts future-compatible detail keys.
    It never queries Binance or formal Kline tables and never repairs data.
    """

    candidate_names = (
        "latest_written_open_time_ms",
        "latest_closed_open_time_ms",
        "latest_base_open_time_ms",
        "actual_end_open_time_ms",
        "end_open_time_ms",
    )
    for name in candidate_names:
        value = getattr(upstream_result, name, None)
        parsed = _parse_open_time_ms(value)
        if parsed is not None:
            return parsed

    details = getattr(upstream_result, "details", None)
    if isinstance(details, Mapping):
        for name in candidate_names:
            parsed = _parse_open_time_ms(details.get(name))
            if parsed is not None:
                return parsed
    return None


def _parse_open_time_ms(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


__all__ = ["run_strategy_signal_scheduler_after_collect_job"]
