"""Thin scheduler job wrapper for stage-18 strategy aggregation.

This file belongs to `app/scheduler/jobs`. It is called only by
`app/scheduler/runner.py` after the stage-17 strategy signal scheduler hook has
returned success or partial_success and `STRATEGY_AGGREGATION_AUTO_RUN_ENABLED`
is true.

It opens a MySQL session and delegates all stage-18 logic to
`app/strategy/aggregation/service.py::run_strategy_aggregation`. It does not
call scripts, does not call the stage-16 StrategySignalService, does not call
the stage-15 snapshot service, does not request Binance, does not write formal
Kline tables, does not write Redis, does not call DeepSeek or any large
language model, does not generate final trading advice, and does not perform
trading.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import AppSettings, get_settings
from app.core.time_utils import UTC, now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_SCHEDULER
from app.scheduler.config import SchedulerRuntimeConfig, build_scheduler_runtime_config
from app.scheduler.strategy_signal_scheduler_types import StrategySignalSchedulerResult
from app.strategy.aggregation.service import run_strategy_aggregation
from app.strategy.aggregation.types import (
    EXIT_SUCCESS,
    StrategyAggregationRequest,
    StrategyAggregationResult,
    StrategyAggregationStatus,
)
from app.storage.mysql import session as mysql_session


def run_strategy_aggregation_after_signal_job(
    *,
    strategy_signal_scheduler_result: StrategySignalSchedulerResult,
    current_time_utc: datetime | None = None,
    settings: AppSettings | None = None,
    config: SchedulerRuntimeConfig | None = None,
    service: Any | None = None,
) -> StrategyAggregationResult:
    """Run stage-18 aggregation after a successful stage-17 result.

    Parameters: stage-17 result, current UTC time for validation, settings,
    scheduler config, and optional injected service.
    Return value: `StrategyAggregationResult`.
    Failure scenarios: missing `run_id` or repository/service failures are
    returned by the stage-18 service when possible.
    External effects: may write stage-18 tables and may send one Hermes
    notification according to .env. It never calls strategy or snapshot scripts.
    """

    active_settings = settings or get_settings()
    active_config = config or build_scheduler_runtime_config(active_settings)
    if not active_config.strategy_aggregation_auto_run_enabled:
        return StrategyAggregationResult(
            status=StrategyAggregationStatus.SKIPPED,
            exit_code=EXIT_SUCCESS,
            aggregation_run_id="",
            material_pack_id=None,
            strategy_signal_run_id=str(getattr(strategy_signal_scheduler_result, "run_id", "") or ""),
            trace_id=str(getattr(strategy_signal_scheduler_result, "trace_id", "") or ""),
            snapshot_id=getattr(strategy_signal_scheduler_result, "snapshot_id", None),
            message="Strategy aggregation auto-run is disabled by configuration.",
        )

    _ensure_utc(current_time_utc or now_utc())
    stage17_status = getattr(strategy_signal_scheduler_result, "status", "")
    if str(getattr(stage17_status, "value", stage17_status)) not in {"success", "partial_success"}:
        return StrategyAggregationResult(
            status=StrategyAggregationStatus.SKIPPED,
            exit_code=EXIT_SUCCESS,
            aggregation_run_id="",
            material_pack_id=None,
            strategy_signal_run_id=str(getattr(strategy_signal_scheduler_result, "run_id", "") or ""),
            trace_id=str(getattr(strategy_signal_scheduler_result, "trace_id", "") or ""),
            snapshot_id=getattr(strategy_signal_scheduler_result, "snapshot_id", None),
            message="Stage-18 scheduler job accepts only stage-17 success or partial_success.",
        )
    run_id = str(getattr(strategy_signal_scheduler_result, "run_id", "") or "")
    if not run_id.strip():
        return StrategyAggregationResult(
            status=StrategyAggregationStatus.SKIPPED,
            exit_code=EXIT_SUCCESS,
            aggregation_run_id="",
            material_pack_id=None,
            strategy_signal_run_id="",
            trace_id=str(getattr(strategy_signal_scheduler_result, "trace_id", "") or ""),
            snapshot_id=getattr(strategy_signal_scheduler_result, "snapshot_id", None),
            message="strategy_signal_run_id missing; stage-18 scheduler job did not open a database session.",
        )
    request = StrategyAggregationRequest(
        strategy_signal_run_id=run_id,
        trigger_source=TRIGGER_SOURCE_SCHEDULER,
        dry_run=False,
        confirm_write=True,
        created_by="strategy_signal_scheduler",
        trace_id=str(getattr(strategy_signal_scheduler_result, "trace_id", "") or ""),
    )

    with mysql_session.session_scope(settings=active_settings, commit_on_success=False) as db_session:
        if service is not None and hasattr(service, "run_strategy_aggregation"):
            return service.run_strategy_aggregation(db_session, request=request)
        return run_strategy_aggregation(db_session=db_session, request=request, service=service)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("strategy aggregation scheduler job requires timezone-aware UTC time")
    return value.astimezone(UTC)


__all__ = ["run_strategy_aggregation_after_signal_job"]
