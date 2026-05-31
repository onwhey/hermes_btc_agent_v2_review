"""Thin scheduler job wrapper for stage-25 strategy pipeline orchestration.

This file belongs to `app/scheduler/jobs`. It is called only by
`app/scheduler/runner.py` after the phase-09 4h Kline incremental collector has
returned success and `STRATEGY_PIPELINE_SCHEDULER_ENABLED=true`.

Call chain:
app/scheduler/runner.py::SchedulerRunner._run_strategy_pipeline_post_collect_if_needed
    -> app/scheduler/jobs/strategy_pipeline_job.py::run_strategy_pipeline_after_collect_job
    -> app/strategy_pipeline/service.py::run_strategy_pipeline

The wrapper opens a MySQL session and builds a bounded pipeline request. It does
not request Binance, write formal Kline tables, write Redis directly, send
Hermes directly, call a large model directly, read accounts or positions,
generate orders, or perform trading. Real model calls and Hermes sends remain
guarded by downstream 25/20/21 configuration and request flags.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from app.core.config import AppSettings, get_settings
from app.core.time_utils import UTC, now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_SCHEDULER
from app.scheduler.config import SchedulerRuntimeConfig, build_scheduler_runtime_config
from app.storage.mysql import session as mysql_session
from app.strategy_pipeline.service import run_strategy_pipeline
from app.strategy_pipeline.types import StrategyPipelineRequest, StrategyPipelineResult


def run_strategy_pipeline_after_collect_job(
    *,
    upstream_job_name: str,
    upstream_result: Any,
    upstream_slot_time_utc: datetime,
    kline_slot_utc: datetime,
    current_time_utc: datetime | None = None,
    settings: AppSettings | None = None,
    config: SchedulerRuntimeConfig | None = None,
    service: Any | None = None,
) -> StrategyPipelineResult:
    """Run the stage-25 pipeline after one successful 4h collector job.

    Parameters: upstream collector metadata and the explicit `kline_slot_utc`
    extracted by the runner from the 09 result.
    Return value: `StrategyPipelineResult`.
    Failure scenarios: invalid request, Redis lock conflict, or downstream
    service failures are returned by the stage-25 service.
    External effects: may write pipeline and downstream stage tables through the
    app service. It opens MySQL but never writes directly in this wrapper. It
    does not write Redis except through 25's lock manager and does not send
    Hermes directly.
    """

    _ensure_utc(current_time_utc or now_utc())
    _ensure_utc(upstream_slot_time_utc)
    active_settings = settings or get_settings()
    active_config = config or build_scheduler_runtime_config(active_settings)
    active_slot = _ensure_utc(kline_slot_utc)
    trace_id = str(getattr(upstream_result, "trace_id", "") or uuid4().hex)
    request = StrategyPipelineRequest(
        symbol=active_config.strategy_signal_symbol,
        base_interval=active_config.strategy_signal_base_interval,
        higher_interval=active_config.strategy_signal_higher_interval,
        kline_slot_utc=active_slot,
        trigger_source=TRIGGER_SOURCE_SCHEDULER,
        dry_run=False,
        confirm_write=True,
        use_real_model=False,
        confirm_real_model_cost=False,
        send_real_hermes=False,
        retry_failed_stage17=False,
        created_by="scheduler_strategy_pipeline",
        trace_id=trace_id,
    )

    with mysql_session.session_scope(settings=active_settings, commit_on_success=False) as db_session:
        if service is not None and hasattr(service, "run_strategy_pipeline"):
            return service.run_strategy_pipeline(db_session, request=request)
        return run_strategy_pipeline(db_session=db_session, request=request, service=service)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("strategy pipeline scheduler job requires timezone-aware UTC time")
    return value.astimezone(UTC)


__all__ = ["run_strategy_pipeline_after_collect_job"]
