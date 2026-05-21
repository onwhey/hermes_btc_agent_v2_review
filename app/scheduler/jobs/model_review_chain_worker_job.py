"""Thin scheduler job wrapper for stage-20C model-review chain worker.

This file belongs to `app/scheduler/jobs`. It is called only by
`app/scheduler/runner.py` after stage-18 strategy aggregation has returned
success or partial_success and model-review scheduler config is enabled.

It opens a MySQL session and delegates to
`app/model_review_chain/worker.py::run_model_review_chain_worker`. It does not
call scripts, does not call stage 19 directly, does not request Binance, does
not modify formal Kline tables, does not send Hermes, does not generate final
trading advice, and does not execute trading.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import AppSettings, get_settings
from app.core.time_utils import UTC, now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_SCHEDULER
from app.model_review_chain.schema import DEFAULT_SCHEDULER_CHAIN_KEY
from app.model_review_chain.worker import run_model_review_chain_worker
from app.model_review_chain.worker_schema import ModelReviewChainWorkerRequest, ModelReviewChainWorkerResult
from app.scheduler.config import SchedulerRuntimeConfig, build_scheduler_runtime_config
from app.storage.mysql import session as mysql_session


def run_model_review_chain_worker_after_aggregation_job(
    *,
    aggregation_result: Any,
    current_time_utc: datetime | None = None,
    settings: AppSettings | None = None,
    config: SchedulerRuntimeConfig | None = None,
    worker: Any | None = None,
) -> ModelReviewChainWorkerResult:
    """Run 20C worker after stage-18 without allowing scheduler to call 19.

    Parameters: stage-18 result, current UTC time for validation, settings,
    scheduler config, and optional injected worker.
    Return value: `ModelReviewChainWorkerResult`.
    Failure scenarios: missing `material_pack_id` returns a skipped worker
    result without opening a database session.
    External effects: may write 20B chain/step rows and may let the 20C worker
    call stage 19 only after all worker gates pass.
    """

    active_settings = settings or get_settings()
    active_config = config or build_scheduler_runtime_config(active_settings)
    _ensure_utc(current_time_utc or now_utc())
    material_pack_id = str(getattr(aggregation_result, "material_pack_id", "") or "").strip()
    if not material_pack_id:
        return ModelReviewChainWorkerResult(
            status="skipped",
            exit_code=0,
            trace_id=str(getattr(aggregation_result, "trace_id", "") or ""),
            material_pack_id=None,
            model_review_skip_reason="本轮未调用大模型；material_pack_id missing.",
            model_review_block_reason="material_pack_id missing",
            summary_text="本轮未调用大模型；material_pack_id missing.",
            error_code="material_pack_id_missing",
        )
    request = ModelReviewChainWorkerRequest(
        material_pack_id=material_pack_id,
        chain_key=DEFAULT_SCHEDULER_CHAIN_KEY,
        trigger_source=TRIGGER_SOURCE_SCHEDULER,
        dry_run=False,
        confirm_write=True,
        created_by="scheduler_model_review_chain_worker",
        trace_id=str(getattr(aggregation_result, "trace_id", "") or ""),
    )
    if not active_config.model_review_scheduler_enabled or not active_config.model_review_auto_run_enabled:
        return ModelReviewChainWorkerResult(
            status="skipped",
            exit_code=0,
            trace_id=request.trace_id,
            material_pack_id=material_pack_id,
            model_review_skip_reason="本轮未调用大模型；20C scheduler worker config disabled.",
            model_review_block_reason="20C scheduler worker config disabled",
            summary_text="本轮未调用大模型；20C scheduler worker config disabled.",
            error_code="model_review_scheduler_worker_disabled",
        )
    with mysql_session.session_scope(settings=active_settings, commit_on_success=False) as db_session:
        return run_model_review_chain_worker(db_session=db_session, request=request, worker=worker)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("model review chain worker scheduler job requires timezone-aware UTC time")
    return value.astimezone(UTC)


__all__ = ["run_model_review_chain_worker_after_aggregation_job"]
