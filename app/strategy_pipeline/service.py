"""Stage-25A manual strategy pipeline orchestration service.

This file coordinates existing 17/16, 23F, 18, 20C/20A, and 21C services for a
manual 25A run. It does not implement strategies, prompts, advice logic, Hermes
rendering, exchange/account access, order endpoints, or automatic trading.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.core.config import AppSettings, get_settings
from app.core.exceptions import RedisError
from app.core.time_utils import now_utc, utc_datetime_to_timestamp_ms
from app.market_data.kline_constants import KLINE_4H_INTERVAL_MS, KLINE_4H_INTERVAL_VALUE, TRIGGER_SOURCE_CLI
from app.model_review_aggregation.schema import ModelReviewAggregationRequest, ModelReviewAggregationStatus
from app.model_review_chain.worker_schema import (
    MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
    MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
    ModelReviewChainWorkerRequest,
)
from app.scheduler.slot_state import KLINE_4H_INCREMENTAL_JOB_NAME
from app.scheduler.strategy_signal_scheduler_types import StrategySignalSchedulerRequest, StrategySignalSchedulerStatus
from app.strategy.aggregation.types import StrategyAggregationRequest, StrategyAggregationStatus
from app.strategy_advice.scheduler_schema import StrategyAdviceSchedulerRequest, StrategyAdviceSchedulerStatus
from app.strategy_pipeline.evidence_stage import run_or_reuse_stage23f_for_pipeline
from app.strategy_pipeline.locks import (
    StrategyPipelineLock,
    StrategyPipelineLockManager,
    build_strategy_pipeline_lock_key,
)
from app.strategy_pipeline.model_review_flags import infer_real_model_called_from_worker_result
from app.strategy_pipeline.repository import (
    StrategyPipelineRepository,
    create_default_strategy_pipeline_repository,
)
from app.strategy_pipeline.types import (
    PIPELINE_STEP_PREFLIGHT,
    PIPELINE_STEP_STAGE17_16,
    PIPELINE_STEP_STAGE18,
    PIPELINE_STEP_STAGE20,
    PIPELINE_STEP_STAGE21,
    PIPELINE_STEP_STAGE23F,
    StrategyPipelineEventPayload,
    StrategyPipelineRequest,
    StrategyPipelineResult,
    StrategyPipelineStatus,
    build_pipeline_run_id,
    exit_code_for_status,
    status_value,
)
from app.strategy_pipeline.stage_services import (
    create_pipeline_stage17_service,
    create_pipeline_stage18_service,
    create_pipeline_stage23f_service,
    create_pipeline_stage20_worker,
    create_pipeline_stage20a_service,
    create_pipeline_stage21_service,
)
from app.strategy_pipeline.utils import (
    PipelineState,
    commit_if_possible,
    compact_object,
    require_slot,
    rollback_if_possible,
    text_or_none,
)


class StrategyPipelineService:
    """Coordinate one manual strategy pipeline run without owning stage logic."""

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        repository: StrategyPipelineRepository | Any | None = None,
        lock_manager: StrategyPipelineLockManager | Any | None = None,
        stage17_service: Any | None = None,
        stage23f_service: Any | None = None,
        stage18_service: Any | None = None,
        stage20_worker: Any | None = None,
        stage20a_service: Any | None = None,
        stage21_service: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repository = repository or create_default_strategy_pipeline_repository()
        self._lock_manager = lock_manager or StrategyPipelineLockManager()
        self._stage17_service = stage17_service
        self._stage23f_service = stage23f_service
        self._stage18_service = stage18_service
        self._stage20_worker = stage20_worker
        self._stage20a_service = stage20a_service
        self._stage21_service = stage21_service

    def run_strategy_pipeline(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineRequest,
    ) -> StrategyPipelineResult:
        """Run the manual 25A pipeline against caller-owned DB session.

        Dry-run performs validation and Kline-slot resolution only. Confirm-write
        may write stage tables through existing downstream services, write one
        pipeline event log, and acquire a Redis lock. It never bypasses downstream
        model/Hermes/cost gates.
        """

        pipeline_run_id = build_pipeline_run_id(
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            trace_id=request.trace_id,
        )
        state = PipelineState.from_request(request, pipeline_run_id=pipeline_run_id)

        invalid = self._validate_request(request, state=state)
        if invalid is not None:
            return invalid

        slot_result = self._resolve_kline_slot(db_session, request=request, state=state)
        if slot_result is not None:
            return slot_result

        if request.dry_run:
            return self._build_result(
                request=request,
                state=state,
                status=StrategyPipelineStatus.DRY_RUN,
                message="Dry-run resolved the pipeline target. No database writes, model calls, or Hermes sends were triggered.",
            )

        lock = self._build_lock(request=request, state=state)
        acquired_lock = self._acquire_pipeline_lock(request=request, state=state, lock=lock)
        if not acquired_lock.acquired:
            return self._build_result(
                request=request,
                state=state,
                status=StrategyPipelineStatus.SKIPPED,
                current_step=PIPELINE_STEP_PREFLIGHT,
                lock_key=acquired_lock.key,
                message="Another strategy pipeline owns the same symbol/interval/Kline-slot lock.",
                error_code="pipeline_lock_already_held",
            )

        event_row = None
        try:
            event_row = self._write_pipeline_event(
                db_session,
                request=request,
                state=state,
                status=StrategyPipelineStatus.PARTIAL_SUCCESS,
                current_step=PIPELINE_STEP_PREFLIGHT,
                finished=False,
            )
            commit_if_possible(db_session)

            result = self._run_confirmed_pipeline(db_session, request=request, state=state, event_row=event_row)
            return result
        except Exception as exc:  # noqa: BLE001 - pipeline must convert boundary failures.
            rollback_if_possible(db_session)
            result = self._build_result(
                request=request,
                state=state,
                status=StrategyPipelineStatus.FAILED,
                current_step=state.current_step or PIPELINE_STEP_PREFLIGHT,
                lock_key=acquired_lock.key,
                message="Strategy pipeline failed before completing all stages.",
                error_code="strategy_pipeline_exception",
                error_message=str(exc),
            )
            if event_row is not None:
                self._update_pipeline_event(db_session, event_row=event_row, request=request, result=result)
                commit_if_possible(db_session)
            return result
        finally:
            self._release_pipeline_lock(acquired_lock)

    def _run_confirmed_pipeline(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineRequest,
        state: PipelineState,
        event_row: Any,
    ) -> StrategyPipelineResult:
        stage17 = self._run_stage17_16(db_session, request=request, state=state)
        if stage17.status not in (StrategySignalSchedulerStatus.SUCCESS, StrategySignalSchedulerStatus.PARTIAL_SUCCESS):
            return self._finish_from_stage_failure(
                db_session,
                event_row=event_row,
                request=request,
                state=state,
                current_step=PIPELINE_STEP_STAGE17_16,
                message=stage17.message,
                error_code=status_value(stage17.status),
                error_message=stage17.error_message,
            )
        state.strategy_signal_run_id = stage17.run_id
        state.details["stage17_result"] = compact_object(stage17)
        self._update_event_progress(db_session, event_row=event_row, request=request, state=state)

        evidence_outcome = run_or_reuse_stage23f_for_pipeline(
            db_session,
            request=request,
            state=state,
            settings=self._settings,
            repository=self._repository,
            evidence_service=self._stage23f_service or create_pipeline_stage23f_service(),
        )
        if not evidence_outcome.should_continue:
            result = self._build_result(
                request=request,
                state=state,
                status=evidence_outcome.status or StrategyPipelineStatus.BLOCKED,
                current_step=PIPELINE_STEP_STAGE23F,
                message=evidence_outcome.message,
                error_code=evidence_outcome.error_code,
                error_message=evidence_outcome.error_message,
            )
            return self._finish_result(db_session, event_row=event_row, request=request, result=result)
        self._update_event_progress(db_session, event_row=event_row, request=request, state=state)

        stage18 = self._run_stage18(db_session, request=request, state=state)
        if stage18.status not in (StrategyAggregationStatus.SUCCESS, StrategyAggregationStatus.PARTIAL_SUCCESS):
            return self._finish_from_stage_failure(
                db_session,
                event_row=event_row,
                request=request,
                state=state,
                current_step=PIPELINE_STEP_STAGE18,
                message=stage18.message,
                error_code=stage18.error_code or status_value(stage18.status),
                error_message=stage18.error_message,
            )
        state.material_pack_id = stage18.material_pack_id
        state.details["stage18_result"] = compact_object(stage18)
        self._update_event_progress(db_session, event_row=event_row, request=request, state=state)

        model_result = self._run_stage20(db_session, request=request, state=state)
        if model_result is not None:
            return self._finish_result(db_session, event_row=event_row, request=request, result=model_result)
        self._update_event_progress(db_session, event_row=event_row, request=request, state=state)

        advice_result = self._run_stage21(db_session, request=request, state=state)
        return self._finish_result(db_session, event_row=event_row, request=request, result=advice_result)

    def _run_stage17_16(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineRequest,
        state: PipelineState,
    ) -> Any:
        state.current_step = PIPELINE_STEP_STAGE17_16
        slot = require_slot(state.kline_slot_utc)
        service = self._stage17_service or create_pipeline_stage17_service(settings=self._settings, request=request)
        scheduler_request = StrategySignalSchedulerRequest(
            upstream_job_name=KLINE_4H_INCREMENTAL_JOB_NAME,
            current_time_utc=now_utc(),
            upstream_slot_time_utc=slot + timedelta(milliseconds=KLINE_4H_INTERVAL_MS),
            symbol=request.symbol,
            base_interval_value=request.base_interval,
            higher_interval_value=request.higher_interval,
            trigger_source=request.trigger_source,
            upstream_trace_id=request.trace_id,
            upstream_latest_base_open_time_ms=utc_datetime_to_timestamp_ms(slot),
            trace_id=request.trace_id,
        )
        if hasattr(service, "run_after_collector_success"):
            return service.run_after_collector_success(db_session, request=scheduler_request)
        return service(db_session=db_session, request=scheduler_request)

    def _run_stage18(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineRequest,
        state: PipelineState,
    ) -> Any:
        state.current_step = PIPELINE_STEP_STAGE18
        service = self._stage18_service or create_pipeline_stage18_service(settings=self._settings)
        aggregation_request = StrategyAggregationRequest(
            strategy_signal_run_id=str(state.strategy_signal_run_id or ""),
            symbol=request.symbol,
            base_interval_value=request.base_interval,
            higher_interval_value=request.higher_interval,
            trigger_source=request.trigger_source,
            dry_run=False,
            confirm_write=True,
            created_by=request.created_by,
            trace_id=request.trace_id,
        )
        if hasattr(service, "run_strategy_aggregation"):
            return service.run_strategy_aggregation(db_session, request=aggregation_request)
        return service(db_session=db_session, request=aggregation_request)

    def _run_stage20(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineRequest,
        state: PipelineState,
    ) -> StrategyPipelineResult | None:
        state.current_step = PIPELINE_STEP_STAGE20
        if not state.material_pack_id:
            return self._build_result(
                request=request,
                state=state,
                status=StrategyPipelineStatus.BLOCKED,
                current_step=PIPELINE_STEP_STAGE20,
                message="Stage 20 cannot run because stage 18 did not return a material_pack_id.",
                error_code="material_pack_missing",
            )
        worker = self._stage20_worker or create_pipeline_stage20_worker(settings=self._settings, request=request)
        worker_request = ModelReviewChainWorkerRequest(
            material_pack_id=state.material_pack_id,
            trigger_source=request.trigger_source,
            dry_run=False,
            confirm_write=True,
            confirm_real_model_cost=bool(
                request.use_real_model
                and request.confirm_real_model_cost
                and self._settings.strategy_pipeline_real_model_enabled
            ),
            created_by=request.created_by,
            trace_id=request.trace_id,
        )
        worker_result = (
            worker.run_model_review_chain_worker(db_session, request=worker_request)
            if hasattr(worker, "run_model_review_chain_worker")
            else worker(db_session=db_session, request=worker_request)
        )
        state.model_review_invoked = bool(getattr(worker_result, "model_review_invoked", False))
        state.model_review_reused = bool(getattr(worker_result, "model_review_reused", False))
        state.real_model_called = infer_real_model_called_from_worker_result(worker_result)
        state.model_analysis_run_id = getattr(worker_result, "reused_model_analysis_run_id", None)
        state.details["stage20c_result"] = compact_object(worker_result)

        if getattr(worker_result, "status", "") in (
            MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
            MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
        ):
            return self._build_result(
                request=request,
                state=state,
                status=StrategyPipelineStatus.BLOCKED,
                current_step=PIPELINE_STEP_STAGE20,
                message=str(getattr(worker_result, "summary_text", "") or "Stage 20C did not produce a usable review."),
                error_code=getattr(worker_result, "error_code", None) or status_value(getattr(worker_result, "status", "")),
                error_message=getattr(worker_result, "error_message", None),
            )

        aggregation_service = self._stage20a_service or create_pipeline_stage20a_service(settings=self._settings)
        aggregation_request = ModelReviewAggregationRequest(
            material_pack_id=state.material_pack_id,
            trigger_source=request.trigger_source,
            dry_run=False,
            confirm_write=True,
            created_by=request.created_by,
            trace_id=request.trace_id,
        )
        aggregation_result = (
            aggregation_service.run_model_review_aggregation(db_session, request=aggregation_request)
            if hasattr(aggregation_service, "run_model_review_aggregation")
            else aggregation_service(db_session=db_session, request=aggregation_request)
        )
        state.review_aggregation_run_id = getattr(aggregation_result, "review_aggregation_run_id", None)
        state.model_review_invoked = state.model_review_invoked or bool(
            getattr(aggregation_result, "model_review_invoked", False)
        )
        state.model_review_reused = state.model_review_reused or bool(
            getattr(aggregation_result, "model_review_reused", False)
        )
        state.model_analysis_run_id = (
            state.model_analysis_run_id
            or getattr(aggregation_result, "reused_model_analysis_run_id", None)
        )
        state.details["stage20a_result"] = compact_object(aggregation_result)

        if getattr(aggregation_result, "status", None) != ModelReviewAggregationStatus.SUCCESS:
            return self._build_result(
                request=request,
                state=state,
                status=StrategyPipelineStatus.BLOCKED,
                current_step=PIPELINE_STEP_STAGE20,
                message=str(getattr(aggregation_result, "summary_text", "") or "Stage 20A did not produce MRAG."),
                error_code=getattr(aggregation_result, "error_code", None)
                or status_value(getattr(aggregation_result, "status", "")),
                error_message=getattr(aggregation_result, "error_message", None),
            )
        return None

    def _run_stage21(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineRequest,
        state: PipelineState,
    ) -> StrategyPipelineResult:
        state.current_step = PIPELINE_STEP_STAGE21
        if not state.review_aggregation_run_id:
            return self._build_result(
                request=request,
                state=state,
                status=StrategyPipelineStatus.BLOCKED,
                current_step=PIPELINE_STEP_STAGE21,
                message="Stage 21 cannot run because stage 20A did not return a review_aggregation_run_id.",
                error_code="review_aggregation_run_missing",
            )
        service = self._stage21_service or create_pipeline_stage21_service(settings=self._settings, request=request)
        advice_request = StrategyAdviceSchedulerRequest(
            review_aggregation_run_id=state.review_aggregation_run_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            trigger_source=request.trigger_source,
            dry_run=False,
            confirm_write=True,
            created_by=request.created_by,
            trace_id=request.trace_id,
        )
        advice_result = (
            service.run_strategy_advice_scheduler(db_session, request=advice_request)
            if hasattr(service, "run_strategy_advice_scheduler")
            else service(db_session=db_session, request=advice_request)
        )
        state.review_id = getattr(advice_result, "lifecycle_review_id", None)
        state.notification_status = getattr(advice_result, "notification_status", None)
        state.hermes_real_sent = bool(
            getattr(advice_result, "send_real_alert", False)
            and status_value(getattr(advice_result, "notification_status", "")) in {"success", "sent", "submitted_to_hermes"}
        )
        stage21_details = dict(getattr(advice_result, "details", {}) or {})
        stage21a = stage21_details.get("stage21a_result") if isinstance(stage21_details, dict) else None
        if isinstance(stage21a, dict):
            state.advice_id = state.advice_id or text_or_none(stage21a.get("advice_id"))
        state.details["stage21_result"] = compact_object(advice_result)

        if getattr(advice_result, "status", None) != StrategyAdviceSchedulerStatus.SUCCESS:
            return self._build_result(
                request=request,
                state=state,
                status=StrategyPipelineStatus.FAILED,
                current_step=PIPELINE_STEP_STAGE21,
                message=str(getattr(advice_result, "summary_text", "") or "Stage 21 did not complete successfully."),
                error_code=getattr(advice_result, "error_code", None) or status_value(getattr(advice_result, "status", "")),
                error_message=getattr(advice_result, "error_message", None),
            )
        return self._build_result(
            request=request,
            state=state,
            status=StrategyPipelineStatus.SUCCESS,
            current_step=PIPELINE_STEP_STAGE21,
            message="Strategy pipeline completed all manual 25A stages.",
        )

    def _validate_request(
        self,
        request: StrategyPipelineRequest,
        *,
        state: PipelineState,
    ) -> StrategyPipelineResult | None:
        problems: list[str] = []
        if not self._settings.strategy_pipeline_enabled:
            problems.append("STRATEGY_PIPELINE_ENABLED=false")
        if request.trigger_source != TRIGGER_SOURCE_CLI:
            problems.append("25A manual pipeline currently supports only trigger_source=cli")
        if request.dry_run and request.confirm_write:
            problems.append("dry_run and confirm_write cannot both be true")
        if not request.dry_run and not request.confirm_write:
            problems.append("non-dry-run strategy pipeline requires confirm_write")
        if not request.symbol.strip():
            problems.append("symbol is required")
        if request.base_interval != KLINE_4H_INTERVAL_VALUE:
            problems.append("25A currently supports base_interval=4h")
        if not request.higher_interval.strip():
            problems.append("higher_interval is required")
        if request.use_real_model and not request.confirm_real_model_cost:
            problems.append("real model use requires --confirm-real-model-cost")
        if not problems:
            return None
        return self._build_result(
            request=request,
            state=state,
            status=StrategyPipelineStatus.BLOCKED,
            current_step=PIPELINE_STEP_PREFLIGHT,
            message="Strategy pipeline request is blocked by configuration or invalid parameters.",
            error_code="invalid_strategy_pipeline_request",
            error_message="; ".join(problems),
        )

    def _resolve_kline_slot(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineRequest,
        state: PipelineState,
    ) -> StrategyPipelineResult | None:
        if request.kline_slot_utc is not None:
            state.kline_slot_utc = require_slot(request.kline_slot_utc)
            state.kline_slot_source = "cli_argument"
            return None
        value = self._repository.resolve_latest_base_kline_slot_utc(
            db_session,
            symbol=request.symbol,
            base_interval=request.base_interval,
        )
        if value is None:
            return self._build_result(
                request=request,
                state=state,
                status=StrategyPipelineStatus.BLOCKED,
                current_step=PIPELINE_STEP_PREFLIGHT,
                message="Pipeline could not uniquely determine a base Kline slot; pass --kline-slot-utc.",
                error_code="kline_slot_not_found",
            )
        state.kline_slot_utc = require_slot(value)
        state.kline_slot_source = "latest_closed_kline"
        return None

    def _build_lock(self, *, request: StrategyPipelineRequest, state: PipelineState) -> StrategyPipelineLock:
        slot = require_slot(state.kline_slot_utc)
        lock_key = build_strategy_pipeline_lock_key(
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=slot,
        )
        state.lock_key = lock_key
        return StrategyPipelineLock(
            key=lock_key,
            owner=f"strategy_pipeline:{state.pipeline_run_id}",
            ttl_seconds=int(self._settings.strategy_pipeline_lock_ttl_seconds),
        )

    def _acquire_pipeline_lock(
        self,
        *,
        request: StrategyPipelineRequest,
        state: PipelineState,
        lock: StrategyPipelineLock,
    ) -> StrategyPipelineLock:
        try:
            return self._lock_manager.acquire_strategy_pipeline_lock(lock=lock)
        except RedisError as exc:
            state.details["pipeline_lock_error"] = str(exc)
            return StrategyPipelineLock(key=lock.key, owner=lock.owner, ttl_seconds=lock.ttl_seconds, acquired=False)

    def _release_pipeline_lock(self, lock: StrategyPipelineLock) -> None:
        if not lock.acquired:
            return
        try:
            self._lock_manager.release_strategy_pipeline_lock(lock=lock)
        except RedisError:
            return

    def _write_pipeline_event(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineRequest,
        state: PipelineState,
        status: StrategyPipelineStatus,
        current_step: str | None,
        finished: bool,
    ) -> Any:
        payload = self._event_payload(request=request, state=state, status=status, current_step=current_step)
        row = self._repository.create_pipeline_event_log(db_session, payload=payload)
        if finished:
            self._repository.update_pipeline_event_log(db_session, row=row, payload=payload, finished=True)
        return row

    def _update_event_progress(
        self,
        db_session: Any,
        *,
        event_row: Any,
        request: StrategyPipelineRequest,
        state: PipelineState,
    ) -> None:
        payload = self._event_payload(
            request=request,
            state=state,
            status=StrategyPipelineStatus.PARTIAL_SUCCESS,
            current_step=state.current_step,
        )
        self._repository.update_pipeline_event_log(db_session, row=event_row, payload=payload, finished=False)
        commit_if_possible(db_session)

    def _update_pipeline_event(
        self,
        db_session: Any,
        *,
        event_row: Any,
        request: StrategyPipelineRequest,
        result: StrategyPipelineResult,
    ) -> None:
        payload = StrategyPipelineEventPayload(
            pipeline_run_id=result.pipeline_run_id,
            symbol=result.symbol,
            base_interval=result.base_interval,
            higher_interval=result.higher_interval,
            kline_slot_utc=result.kline_slot_utc,
            kline_slot_source=result.kline_slot_source,
            trigger_source=request.trigger_source,
            status=result.status.value,
            current_step=result.current_step,
            strategy_signal_run_id=result.strategy_signal_run_id,
            strategy_evidence_aggregation_id=result.strategy_evidence_aggregation_id,
            material_pack_id=result.material_pack_id,
            model_analysis_run_id=result.model_analysis_run_id,
            review_aggregation_run_id=result.review_aggregation_run_id,
            advice_id=result.advice_id,
            review_id=result.review_id,
            notification_status=result.notification_status,
            model_review_invoked=result.model_review_invoked,
            model_review_reused=result.model_review_reused,
            real_model_called=result.real_model_called,
            hermes_real_sent=result.hermes_real_sent,
            error_code=result.error_code,
            error_message=result.error_message,
            trace_id=result.trace_id,
            details=result.details,
        )
        self._repository.update_pipeline_event_log(db_session, row=event_row, payload=payload, finished=True)

    def _finish_from_stage_failure(
        self,
        db_session: Any,
        *,
        event_row: Any,
        request: StrategyPipelineRequest,
        state: PipelineState,
        current_step: str,
        message: str,
        error_code: str | None,
        error_message: str | None = None,
    ) -> StrategyPipelineResult:
        result = self._build_result(
            request=request,
            state=state,
            status=StrategyPipelineStatus.BLOCKED,
            current_step=current_step,
            message=message,
            error_code=error_code,
            error_message=error_message,
        )
        return self._finish_result(db_session, event_row=event_row, request=request, result=result)

    def _finish_result(
        self,
        db_session: Any,
        *,
        event_row: Any,
        request: StrategyPipelineRequest,
        result: StrategyPipelineResult,
    ) -> StrategyPipelineResult:
        self._update_pipeline_event(db_session, event_row=event_row, request=request, result=result)
        commit_if_possible(db_session)
        return result

    def _build_result(
        self,
        *,
        request: StrategyPipelineRequest,
        state: PipelineState,
        status: StrategyPipelineStatus,
        message: str,
        current_step: str | None = None,
        lock_key: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> StrategyPipelineResult:
        return StrategyPipelineResult(
            status=status,
            exit_code=exit_code_for_status(status),
            pipeline_run_id=state.pipeline_run_id,
            trace_id=request.trace_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=state.kline_slot_utc,
            kline_slot_source=state.kline_slot_source,
            strategy_signal_run_id=state.strategy_signal_run_id,
            strategy_evidence_aggregation_id=state.strategy_evidence_aggregation_id,
            material_pack_id=state.material_pack_id,
            model_analysis_run_id=state.model_analysis_run_id,
            review_aggregation_run_id=state.review_aggregation_run_id,
            advice_id=state.advice_id,
            review_id=state.review_id,
            notification_status=state.notification_status,
            model_review_invoked=state.model_review_invoked,
            model_review_reused=state.model_review_reused,
            real_model_called=state.real_model_called,
            hermes_real_sent=state.hermes_real_sent,
            is_final_trading_advice=False,
            is_trading_signal=False,
            is_executable=False,
            auto_trading_allowed=False,
            current_step=current_step or state.current_step,
            lock_key=lock_key or state.lock_key,
            message=message,
            error_code=error_code,
            error_message=error_message,
            details=dict(state.details),
        )

    def _event_payload(
        self,
        *,
        request: StrategyPipelineRequest,
        state: PipelineState,
        status: StrategyPipelineStatus,
        current_step: str | None,
    ) -> StrategyPipelineEventPayload:
        return StrategyPipelineEventPayload(
            pipeline_run_id=state.pipeline_run_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=state.kline_slot_utc,
            kline_slot_source=state.kline_slot_source,
            trigger_source=request.trigger_source,
            status=status.value,
            current_step=current_step,
            strategy_signal_run_id=state.strategy_signal_run_id,
            strategy_evidence_aggregation_id=state.strategy_evidence_aggregation_id,
            material_pack_id=state.material_pack_id,
            model_analysis_run_id=state.model_analysis_run_id,
            review_aggregation_run_id=state.review_aggregation_run_id,
            advice_id=state.advice_id,
            review_id=state.review_id,
            notification_status=state.notification_status,
            model_review_invoked=state.model_review_invoked,
            model_review_reused=state.model_review_reused,
            real_model_called=state.real_model_called,
            hermes_real_sent=state.hermes_real_sent,
            error_code=None,
            error_message=None,
            trace_id=request.trace_id,
            details=dict(state.details),
        )

def run_strategy_pipeline(
    *,
    db_session: Any,
    request: StrategyPipelineRequest,
    service: StrategyPipelineService | None = None,
) -> StrategyPipelineResult:
    """Convenience app-service function used by the 25A CLI and tests."""

    active_service = service or create_default_strategy_pipeline_service()
    return active_service.run_strategy_pipeline(db_session, request=request)


def create_default_strategy_pipeline_service() -> StrategyPipelineService:
    """Create the default stage-25A manual pipeline service."""

    return StrategyPipelineService()


__all__ = [
    "StrategyPipelineService",
    "create_default_strategy_pipeline_service",
    "run_strategy_pipeline",
]
