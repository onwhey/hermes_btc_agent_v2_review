"""Thin scheduler job wrapper for stage-21C strategy advice orchestration.

This file belongs to `app/scheduler/jobs`. It is called by
`app/scheduler/runner.py` after stage 20 has had a chance to create or reuse a
model_review_aggregation_run.

It opens a MySQL session and delegates to
`app/strategy_advice/scheduler_service.py::run_strategy_advice_scheduler`. It
does not call scripts, stage 19, model providers, Binance, Hermes directly, or
any trading/account/order capability.
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
from app.strategy_advice.scheduler_schema import StrategyAdviceSchedulerRequest, StrategyAdviceSchedulerResult
from app.strategy_advice.scheduler_service import run_strategy_advice_scheduler


def run_strategy_advice_scheduler_after_model_review_job(
    *,
    review_aggregation_run_id: str | None = None,
    aggregation_result: Any | None = None,
    worker_result: Any | None = None,
    current_time_utc: datetime | None = None,
    settings: AppSettings | None = None,
    config: SchedulerRuntimeConfig | None = None,
    service: Any | None = None,
) -> StrategyAdviceSchedulerResult:
    """Run 21C after stage 20 without letting scheduler call 19 or Hermes directly."""

    active_settings = settings or get_settings()
    active_config = config or build_scheduler_runtime_config(active_settings)
    _ensure_utc(current_time_utc or now_utc())
    resolved_mrag_id = _resolve_review_aggregation_run_id(
        review_aggregation_run_id=review_aggregation_run_id,
        aggregation_result=aggregation_result,
        worker_result=worker_result,
    )
    request = StrategyAdviceSchedulerRequest(
        review_aggregation_run_id=resolved_mrag_id or None,
        symbol=_resolve_attr("symbol", worker_result, aggregation_result, default=active_config.strategy_signal_symbol),
        base_interval=_resolve_attr(
            "base_interval",
            worker_result,
            aggregation_result,
            default=active_config.strategy_signal_base_interval,
        ),
        higher_interval=_resolve_attr(
            "higher_interval",
            worker_result,
            aggregation_result,
            default=active_config.strategy_signal_higher_interval,
        ),
        trigger_source=TRIGGER_SOURCE_SCHEDULER,
        dry_run=False,
        confirm_write=True,
        created_by="scheduler_strategy_advice_21c",
        trace_id=_resolve_attr("trace_id", worker_result, aggregation_result, default=uuid4().hex),
    )
    with mysql_session.session_scope(settings=active_settings, commit_on_success=False) as db_session:
        return run_strategy_advice_scheduler(db_session=db_session, request=request, service=service)


def _resolve_review_aggregation_run_id(
    *,
    review_aggregation_run_id: str | None,
    aggregation_result: Any | None,
    worker_result: Any | None,
) -> str:
    if review_aggregation_run_id:
        return review_aggregation_run_id
    for candidate in (worker_result, aggregation_result):
        value = str(getattr(candidate, "review_aggregation_run_id", "") or "").strip()
        if value:
            return value
    details = getattr(worker_result, "details", {}) if worker_result is not None else {}
    if isinstance(details, dict):
        return str(details.get("review_aggregation_run_id") or "").strip()
    return ""


def _resolve_attr(name: str, *candidates: Any, default: str) -> str:
    for candidate in candidates:
        value = str(getattr(candidate, name, "") or "").strip()
        if value:
            return value
    return default


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("strategy advice scheduler job requires timezone-aware UTC time")
    return value.astimezone(UTC)


__all__ = ["run_strategy_advice_scheduler_after_model_review_job"]
