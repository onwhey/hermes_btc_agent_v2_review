"""Stage-20C automatic model-review chain worker.

Call chain:
app/scheduler/runner.py::_run_model_review_chain_worker_after_aggregation_if_needed
    -> app/scheduler/jobs/model_review_chain_worker_job.py::run_model_review_chain_worker_after_aggregation_job
    -> app/model_review_chain/worker.py::run_model_review_chain_worker
    -> app/model_review_aggregation/service.py::run_model_review_aggregation
    -> app/model_review_chain/automation_policy.py::evaluate_automatic_step_policy
    -> app/model_analysis/service.py::run_model_analysis

This file belongs to `app/model_review_chain`. It decides whether a stage-19
review can be reused, and whether a chain should be created or resumed.

It does not let scheduler call stage 19 directly, does not generate final
trading advice, does not create trading signals, does not modify formal Kline
tables, does not read private trading state, and does not execute trading.
It may call a real model only after configuration, budget, whitelist,
frequency, Redis lock, and step-state gates all pass.
"""

from __future__ import annotations

from typing import Any

from app.core.config import AppSettings, get_settings
from app.core.exceptions import RedisError
from app.core.time_utils import now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER
from app.model_analysis.service import ModelAnalysisService, create_default_model_analysis_service
from app.model_analysis.types import (
    MODEL_REVIEW_TRIGGER_SOURCE_WORKER,
    ModelAnalysisRequest,
    ModelAnalysisStatus,
)
from app.model_review_aggregation.schema import (
    ModelReviewAggregationRequest,
    ModelReviewAggregationStatus,
)
from app.model_review_aggregation.service import (
    ModelReviewAggregationService,
    create_default_model_review_aggregation_service,
)
from app.model_review_chain.automation_policy import evaluate_automatic_step_policy
from app.model_review_chain.chain_profile import resolve_chain_profile
from app.model_review_chain.id_utils import build_chain_id, stable_sha256_text
from app.model_review_chain.locks import (
    ModelReviewChainWorkerLockManager,
    ModelReviewWorkerLock,
    build_chain_worker_lock_key,
    build_material_worker_lock_key,
    build_step_worker_lock_key,
)
from app.model_review_chain.payload_builder import (
    apply_step_payload_to_row,
    build_chain_payload_from_row,
    build_initial_chain_payload,
    build_initial_step_payload,
    build_step_input_hash,
    build_step_payload_from_row,
)
from app.model_review_chain.repository import (
    ModelReviewChainRepository,
    create_default_model_review_chain_repository,
)
from app.model_review_chain.result_builder import build_step_result, latest_successful_parent, merge_step_results
from app.model_review_chain.schema import (
    ChainProfile,
    ModelReviewChainStatus,
    ModelReviewChainStepResult,
    ModelReviewChainStepStatus,
)
from app.model_review_chain.state_machine import (
    calculate_chain_state,
    step_is_resumable,
    step_retry_is_available,
    step_status,
)
from app.model_review_chain.worker_safety import (
    build_stale_running_recovery_plan,
    cli_real_model_cost_confirmation_missing,
    retry_available_after_attempt,
    stage19_result_is_temporary_failure,
    temporary_retry_after_utc,
)
from app.model_review_chain.worker_schema import (
    MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
    MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
    MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED,
    ModelReviewChainWorkerRequest,
    ModelReviewChainWorkerResult,
    build_worker_result,
    invalid_worker_request_result,
)
from app.model_review_chain.worker_result_builder import (
    augment_result_with_chain,
    chain_request_adapter,
    chain_result_without_step,
    config_skipped_result,
    dry_run_would_create_or_resume_result,
    dry_run_would_resume_result,
    result_from_blocked_aggregation,
    result_from_completed_or_incomplete_chain,
    result_from_reusable_aggregation,
)

ALLOWED_MODEL_REVIEW_CHAIN_WORKER_TRIGGER_SOURCES = frozenset(
    {TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER, MODEL_REVIEW_TRIGGER_SOURCE_WORKER}
)
_RETRY_AFTER_UNCHANGED = object()


class ModelReviewChainWorker:
    """Coordinate one safe stage-20C worker tick.

    Parameters: settings, repositories, services, and lock manager are
    injectable for tests. Dry-run reads only; confirmed writes may update
    chain/step rows and call stage 19 only after every policy gate passes.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        repository: ModelReviewChainRepository | Any | None = None,
        aggregation_service: ModelReviewAggregationService | Any | None = None,
        model_analysis_service: ModelAnalysisService | Any | None = None,
        lock_manager: ModelReviewChainWorkerLockManager | Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repository = repository or create_default_model_review_chain_repository()
        self._aggregation_service = aggregation_service or create_default_model_review_aggregation_service()
        self._model_analysis_service = model_analysis_service or create_default_model_analysis_service()
        self._lock_manager = lock_manager or ModelReviewChainWorkerLockManager()

    def run_model_review_chain_worker(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainWorkerRequest,
    ) -> ModelReviewChainWorkerResult:
        """Run one worker tick for a material pack, chain, or scan target."""

        invalid = self._validate_worker_request(request)
        if invalid is not None:
            return invalid
        if not self._settings.model_review_scheduler_enabled:
            return config_skipped_result(
                request=request,
                error_code="scheduler_model_review_disabled",
                reason="MODEL_REVIEW_SCHEDULER_ENABLED=false blocks the 20C worker.",
            )
        if not self._settings.model_review_auto_run_enabled:
            return config_skipped_result(
                request=request,
                error_code="auto_run_disabled",
                reason="MODEL_REVIEW_AUTO_RUN_ENABLED=false blocks automatic model review.",
            )
        if request.chain_id:
            return self._resume_existing_chain(db_session, request=request, chain_id=request.chain_id)
        if request.material_pack_id.strip():
            return self._run_for_material_pack(db_session, request=request)
        return self._scan_and_resume_one_chain(db_session, request=request)

    def _validate_worker_request(
        self,
        request: ModelReviewChainWorkerRequest,
    ) -> ModelReviewChainWorkerResult | None:
        problems: list[str] = []
        if request.trigger_source not in ALLOWED_MODEL_REVIEW_CHAIN_WORKER_TRIGGER_SOURCES:
            problems.append("trigger_source supports only cli, scheduler, or worker for stage 20C worker")
        if request.dry_run and request.confirm_write:
            problems.append("dry_run and confirm_write cannot both be true")
        if not request.dry_run and not request.confirm_write:
            problems.append("non-dry-run 20C worker requires confirm_write")
        if request.limit <= 0:
            problems.append("limit must be greater than 0")
        if request.max_retry_count < 0:
            problems.append("max_retry_count must be zero or greater")
        if not problems:
            return None
        return invalid_worker_request_result(request=request, message="; ".join(problems))

    def _scan_and_resume_one_chain(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainWorkerRequest,
    ) -> ModelReviewChainWorkerResult:
        try:
            chains = self._repository.list_unfinished_chain_runs(db_session, limit=request.limit)
        except Exception as exc:  # noqa: BLE001 - worker converts database failures.
            _rollback_if_possible(db_session)
            return build_worker_result(
                status=MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
                trace_id=request.trace_id,
                model_review_skip_reason="本轮未调用大模型；20C worker 扫描未完成 chain 失败。",
                model_review_block_reason="chain_scan_failed",
                summary_text="本轮未调用大模型；20C worker 扫描未完成 chain 失败。",
                error_code="chain_scan_failed",
                error_message=str(exc),
            )
        if not chains:
            return build_worker_result(
                status=MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED,
                trace_id=request.trace_id,
                model_review_skip_reason="本轮未调用大模型；没有待恢复的 model_review_chain。",
                summary_text="本轮未调用大模型；没有待恢复的 model_review_chain。",
                details={"scan_limit": request.limit, "scanned_chain_count": 0},
            )
        chain_id = str(getattr(chains[0], "chain_id", "") or "")
        return self._resume_existing_chain(db_session, request=request, chain_id=chain_id)

    def _run_for_material_pack(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainWorkerRequest,
    ) -> ModelReviewChainWorkerResult:
        aggregation_result = self._run_aggregation_probe(db_session, request=request)
        if aggregation_result.status == ModelReviewAggregationStatus.SUCCESS:
            return result_from_reusable_aggregation(request=request, aggregation_result=aggregation_result)
        if aggregation_result.error_code == "material_pack_not_found":
            return result_from_blocked_aggregation(request=request, aggregation_result=aggregation_result)
        if not self._settings.model_review_real_model_enabled:
            return result_from_blocked_aggregation(
                request=request,
                aggregation_result=aggregation_result,
                override_error_code=aggregation_result.error_code or "real_model_disabled",
                override_block_reason=(
                    aggregation_result.model_review_block_reason
                    or "MODEL_REVIEW_REAL_MODEL_ENABLED=false blocks automatic real model calls."
                ),
            )
        if request.dry_run:
            return dry_run_would_create_or_resume_result(
                request=request,
                aggregation_result=aggregation_result,
                chain_id=None,
            )

        material_lock = self._acquire_lock_or_skipped(
            request=request,
            key=build_material_worker_lock_key(material_pack_id=request.material_pack_id),
        )
        if isinstance(material_lock, ModelReviewChainWorkerResult):
            return material_lock
        try:
            try:
                chain_row = self._repository.get_latest_chain_run_for_material_pack(
                    db_session,
                    material_pack_id=request.material_pack_id,
                )
            except Exception as exc:  # noqa: BLE001
                _rollback_if_possible(db_session)
                return build_worker_result(
                    status=MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
                    trace_id=request.trace_id,
                    material_pack_id=request.material_pack_id,
                    model_review_skip_reason="本轮未调用大模型；查询 material_pack 的 chain 失败。",
                    model_review_block_reason="chain_lookup_failed",
                    summary_text="本轮未调用大模型；查询 material_pack 的 chain 失败。",
                    error_code="chain_lookup_failed",
                    error_message=str(exc),
                )
            if chain_row is None:
                created = self._create_pending_chain_for_material(db_session, request=request)
                if isinstance(created, ModelReviewChainWorkerResult):
                    return created
                chain_row = created
            chain_id = str(getattr(chain_row, "chain_id", "") or "")
            return self._resume_existing_chain(db_session, request=request, chain_id=chain_id)
        finally:
            self._release_lock_safely(material_lock)

    def _run_aggregation_probe(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainWorkerRequest,
    ) -> Any:
        aggregation_request = ModelReviewAggregationRequest(
            material_pack_id=request.material_pack_id,
            trigger_source=request.trigger_source,
            dry_run=True,
            confirm_write=False,
            created_by=request.created_by,
            trace_id=request.trace_id,
        )
        if hasattr(self._aggregation_service, "run_model_review_aggregation"):
            return self._aggregation_service.run_model_review_aggregation(db_session, request=aggregation_request)
        return self._aggregation_service(db_session=db_session, request=aggregation_request)

    def _create_pending_chain_for_material(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainWorkerRequest,
    ) -> Any | ModelReviewChainWorkerResult:
        profile = resolve_chain_profile(request.chain_key)
        chain_id = build_chain_id(
            material_pack_id=request.material_pack_id,
            chain_key=request.chain_key,
            trace_id=request.trace_id,
        )
        if profile is None:
            return build_worker_result(
                status=MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
                trace_id=request.trace_id,
                material_pack_id=request.material_pack_id,
                chain_id=chain_id,
                model_review_skip_reason="本轮未调用大模型；chain_key 不支持。",
                model_review_block_reason=f"Unsupported chain_key: {request.chain_key}",
                summary_text="本轮未调用大模型；chain_key 不支持。",
                error_code="unknown_chain_key",
                    error_message=f"Unsupported chain_key: {request.chain_key}",
                )
        try:
            material_pack = self._repository.get_material_pack_by_id(
                db_session,
                material_pack_id=request.material_pack_id,
            )
            if material_pack is None:
                return build_worker_result(
                    status=MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
                    trace_id=request.trace_id,
                    material_pack_id=request.material_pack_id,
                    chain_id=chain_id,
                    model_review_skip_reason="本轮未调用大模型；analysis_material_pack 不存在。",
                    model_review_block_reason="analysis_material_pack not found",
                    summary_text="本轮未调用大模型；analysis_material_pack 不存在。",
                    error_code="material_pack_not_found",
                )
            chain_row = self._repository.create_model_review_chain_run(
                db_session,
                    payload=build_initial_chain_payload(
                    request=chain_request_adapter(request),
                    profile=profile,
                    chain_id=chain_id,
                    material_pack=material_pack,
                ),
            )
            previous_step_id: str | None = None
            for step_definition in profile.steps:
                step_payload = build_initial_step_payload(
                    chain_id=chain_id,
                    definition=step_definition,
                    parent_step_id=previous_step_id,
                    max_retry_count=request.max_retry_count,
                )
                self._repository.create_model_review_chain_step(db_session, payload=step_payload)
                previous_step_id = step_payload.chain_step_id
            return chain_row
        except Exception as exc:  # noqa: BLE001 - worker reports persistence failures.
            _rollback_if_possible(db_session)
            return build_worker_result(
                status=MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
                trace_id=request.trace_id,
                material_pack_id=request.material_pack_id,
                chain_id=chain_id,
                model_review_skip_reason="本轮未调用大模型；创建自动 model_review_chain 失败。",
                model_review_block_reason="chain_create_failed",
                summary_text="本轮未调用大模型；创建自动 model_review_chain 失败。",
                error_code="chain_create_failed",
                error_message=str(exc),
            )

    def _resume_existing_chain(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainWorkerRequest,
        chain_id: str,
    ) -> ModelReviewChainWorkerResult:
        try:
            chain_row = self._repository.get_chain_run_by_chain_id(db_session, chain_id=chain_id)
            if chain_row is None:
                return build_worker_result(
                    status=MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
                    trace_id=request.trace_id,
                    chain_id=chain_id,
                    model_review_skip_reason="本轮未调用大模型；model_review_chain 不存在。",
                    model_review_block_reason="chain_not_found",
                    summary_text="本轮未调用大模型；model_review_chain 不存在。",
                    error_code="chain_not_found",
                )
            step_rows = self._repository.list_chain_steps(db_session, chain_id=chain_id)
        except Exception as exc:  # noqa: BLE001 - worker converts database failures.
            _rollback_if_possible(db_session)
            return build_worker_result(
                status=MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
                trace_id=request.trace_id,
                chain_id=chain_id,
                model_review_skip_reason="本轮未调用大模型；读取 model_review_chain 失败。",
                model_review_block_reason="chain_lookup_failed",
                summary_text="本轮未调用大模型；读取 model_review_chain 失败。",
                error_code="chain_lookup_failed",
                error_message=str(exc),
            )
        if not step_rows:
            return chain_result_without_step(request=request, chain_row=chain_row, error_code="chain_steps_not_found")
        if request.dry_run:
            return dry_run_would_resume_result(request=request, chain_row=chain_row, step_rows=tuple(step_rows))

        chain_lock = self._acquire_lock_or_skipped(
            request=request,
            key=build_chain_worker_lock_key(chain_id=chain_id),
            chain_id=chain_id,
            material_pack_id=str(getattr(chain_row, "material_pack_id", "") or ""),
        )
        if isinstance(chain_lock, ModelReviewChainWorkerResult):
            return chain_lock
        try:
            return self._advance_chain_steps(
                db_session,
                request=request,
                chain_row=chain_row,
                step_rows=tuple(step_rows),
                profile=resolve_chain_profile(str(getattr(chain_row, "chain_key", "") or "")),
            )
        finally:
            self._release_lock_safely(chain_lock)

    def _advance_chain_steps(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainWorkerRequest,
        chain_row: Any,
        step_rows: tuple[Any, ...],
        profile: ChainProfile | None,
    ) -> ModelReviewChainWorkerResult:
        if profile is None:
            return chain_result_without_step(request=request, chain_row=chain_row, error_code="unknown_chain_key")
        stale_running_result = self._normalize_stale_running_steps(
            db_session,
            request=request,
            chain_row=chain_row,
            step_rows=step_rows,
        )
        if stale_running_result is not None:
            return stale_running_result
        invoked_keys: list[str] = []
        invoked_roles: list[str] = []
        result_steps: list[ModelReviewChainStepResult] = []
        parent_model_analysis_run_id = latest_successful_parent(step_rows)
        try:
            for step_row in step_rows:
                current_status = step_status(step_row)
                if current_status == ModelReviewChainStepStatus.SUCCESS:
                    parent_model_analysis_run_id = str(getattr(step_row, "model_analysis_run_id", "") or "")
                    result_steps.append(build_step_result(step_row, skipped_due_to_success_resume=True))
                    continue
                if not step_is_resumable(step_row):
                    result_steps.append(build_step_result(step_row))
                    break
                if not step_retry_is_available(step_row):
                    result_steps.append(build_step_result(step_row, retry_blocked=True))
                    break
                step_result = self._advance_one_step(
                    db_session,
                    request=request,
                    chain_row=chain_row,
                    step_row=step_row,
                    parent_model_analysis_run_id=parent_model_analysis_run_id,
                    invoked_keys=invoked_keys,
                    invoked_roles=invoked_roles,
                )
                result_steps.append(build_step_result(step_row))
                if isinstance(step_result, ModelReviewChainWorkerResult):
                    state = calculate_chain_state(step_rows, total_steps=len(step_rows))
                    self._persist_chain_state(db_session, chain_row=chain_row, state=state)
                    return augment_result_with_chain(
                        step_result,
                        chain_row=chain_row,
                        state_status=state.status.value,
                        steps=merge_step_results(step_rows, result_steps),
                        invoked_keys=tuple(invoked_keys),
                        invoked_roles=tuple(invoked_roles),
                    )
                parent_model_analysis_run_id = str(getattr(step_row, "model_analysis_run_id", "") or "")
                if step_status(step_row) != ModelReviewChainStepStatus.SUCCESS:
                    break
            state = calculate_chain_state(step_rows, total_steps=len(step_rows))
            self._persist_chain_state(db_session, chain_row=chain_row, state=state)
            _commit_if_possible(db_session)
            return result_from_completed_or_incomplete_chain(
                request=request,
                chain_row=chain_row,
                state_status=state.status.value,
                steps=merge_step_results(step_rows, result_steps),
                invoked_keys=tuple(invoked_keys),
                invoked_roles=tuple(invoked_roles),
            )
        except Exception as exc:  # noqa: BLE001 - worker converts failures.
            _rollback_if_possible(db_session)
            return build_worker_result(
                status=MODEL_REVIEW_CHAIN_WORKER_STATUS_FAILED,
                trace_id=request.trace_id,
                material_pack_id=str(getattr(chain_row, "material_pack_id", "") or "") or None,
                chain_id=str(getattr(chain_row, "chain_id", "") or "") or None,
                aggregation_run_id=str(getattr(chain_row, "aggregation_run_id", "") or "") or None,
                strategy_signal_run_id=str(getattr(chain_row, "strategy_signal_run_id", "") or "") or None,
                snapshot_id=str(getattr(chain_row, "snapshot_id", "") or "") or None,
                model_review_skip_reason="本轮未调用大模型；20C worker 推进 chain 失败。",
                model_review_block_reason="chain_worker_failed",
                model_review_chain_status=ModelReviewChainStatus.FAILED.value,
                summary_text="本轮未调用大模型；20C worker 推进 chain 失败。",
                error_code="chain_worker_failed",
                error_message=str(exc),
            )

    def _normalize_stale_running_steps(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainWorkerRequest,
        chain_row: Any,
        step_rows: tuple[Any, ...],
    ) -> ModelReviewChainWorkerResult | None:
        """Convert stale RUNNING steps into recoverable timeout states.

        A worker process can crash after setting a step to RUNNING. This method
        is intentionally called before any new provider call so that a stale
        RUNNING row does not permanently block the chain or trigger duplicate
        costs in the same tick.
        """

        plan = build_stale_running_recovery_plan(
            step_rows=step_rows,
            current_time_utc=now_utc(),
            timeout_seconds=int(self._settings.model_review_step_running_timeout_seconds),
        )
        if not plan.changed:
            return None
        for update in plan.updates:
            step_row = update.step_row
            self._apply_step_state(
                db_session,
                step_row=step_row,
                status=update.status,
                attempt_no=update.attempt_no,
                parent_model_analysis_run_id=(
                    str(getattr(step_row, "parent_model_analysis_run_id", "") or "") or None
                ),
                model_analysis_run_id=str(getattr(step_row, "model_analysis_run_id", "") or "") or None,
                started_at_utc=getattr(step_row, "started_at_utc", None),
                finished_at_utc=update.finished_at_utc,
                step_input_hash=str(getattr(step_row, "step_input_hash", "") or "") or None,
                step_output_hash=str(getattr(step_row, "step_output_hash", "") or "") or None,
                error_code=update.error_code,
                error_message=update.error_message,
                retry_after_utc=update.retry_after_utc,
            )
        state = calculate_chain_state(step_rows, total_steps=len(step_rows))
        self._persist_chain_state(db_session, chain_row=chain_row, state=state)
        _commit_if_possible(db_session)
        result_status = MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED if plan.retryable_timeout_found else MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED
        return build_worker_result(
            status=result_status,
            trace_id=request.trace_id,
            material_pack_id=str(getattr(chain_row, "material_pack_id", "") or "") or None,
            chain_id=str(getattr(chain_row, "chain_id", "") or "") or None,
            aggregation_run_id=str(getattr(chain_row, "aggregation_run_id", "") or "") or None,
            strategy_signal_run_id=str(getattr(chain_row, "strategy_signal_run_id", "") or "") or None,
            snapshot_id=str(getattr(chain_row, "snapshot_id", "") or "") or None,
            model_review_skip_reason="本轮未调用大模型；worker 先恢复超时 RUNNING step，后续 tick 可继续 resume。",
            model_review_block_reason="step_running_timeout",
            model_review_chain_status=state.status.value,
            model_review_basis="running_step_timeout_recovery",
            summary_text="本轮未调用大模型；worker 已将超时 RUNNING step 标记为 timeout/failed，避免 chain 永久卡在 running。",
            error_code="step_running_timeout_recovered",
            error_message="Stale RUNNING step was normalized before any stage-19 call.",
            steps=tuple(build_step_result(step_row) for step_row in step_rows),
            details={"timeout_seconds": plan.timeout_seconds},
        )

    def _advance_one_step(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainWorkerRequest,
        chain_row: Any,
        step_row: Any,
        parent_model_analysis_run_id: str | None,
        invoked_keys: list[str],
        invoked_roles: list[str],
    ) -> None | ModelReviewChainWorkerResult:
        chain_id = str(getattr(chain_row, "chain_id", "") or "")
        step_no = int(getattr(step_row, "step_no", 0) or 0)
        step_lock = self._acquire_lock_or_skipped(
            request=request,
            key=build_step_worker_lock_key(chain_id=chain_id, step_no=step_no),
            chain_id=chain_id,
            material_pack_id=str(getattr(chain_row, "material_pack_id", "") or ""),
        )
        if isinstance(step_lock, ModelReviewChainWorkerResult):
            if step_lock.error_code == "worker_lock_already_held":
                self._mark_step_retry_waiting(
                    db_session,
                    step_row=step_row,
                    parent_model_analysis_run_id=parent_model_analysis_run_id,
                    error_code="worker_lock_already_held",
                    error_message="A worker lock is already held for this step; retry later.",
                    retry_after_utc=temporary_retry_after_utc(now_utc()),
                )
                _commit_if_possible(db_session)
            return step_lock
        try:
            policy = evaluate_automatic_step_policy(
                settings=self._settings,
                repository=self._repository,
                db_session=db_session,
                model_key=str(getattr(step_row, "model_key", "") or ""),
                current_time_utc=now_utc(),
            )
            if not policy.allowed:
                next_step_status = (
                    ModelReviewChainStepStatus.RETRY_WAITING
                    if policy.is_temporary
                    else ModelReviewChainStepStatus.BLOCKED
                )
                self._apply_step_state(
                    db_session,
                    step_row=step_row,
                    status=next_step_status,
                    attempt_no=int(getattr(step_row, "attempt_no", 0) or 0),
                    parent_model_analysis_run_id=parent_model_analysis_run_id,
                    model_analysis_run_id=str(getattr(step_row, "model_analysis_run_id", "") or "") or None,
                    started_at_utc=getattr(step_row, "started_at_utc", None),
                    finished_at_utc=now_utc(),
                    step_input_hash=str(getattr(step_row, "step_input_hash", "") or "") or None,
                    step_output_hash=str(getattr(step_row, "step_output_hash", "") or "") or None,
                    error_code=policy.error_code,
                    error_message=policy.message,
                    retry_after_utc=policy.retry_after_utc,
                )
                _commit_if_possible(db_session)
                return build_worker_result(
                    status=MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
                    trace_id=request.trace_id,
                    material_pack_id=str(getattr(chain_row, "material_pack_id", "") or "") or None,
                    chain_id=chain_id,
                    aggregation_run_id=str(getattr(chain_row, "aggregation_run_id", "") or "") or None,
                    strategy_signal_run_id=str(getattr(chain_row, "strategy_signal_run_id", "") or "") or None,
                    snapshot_id=str(getattr(chain_row, "snapshot_id", "") or "") or None,
                    model_review_skip_reason="本轮未调用大模型；自动模型调用被配置、预算、白名单或频率策略阻断。",
                    model_review_block_reason=policy.message,
                    model_review_chain_status=ModelReviewChainStatus.BLOCKED.value,
                    model_review_basis="automatic_policy_blocked",
                    summary_text="本轮未调用大模型；自动模型调用被配置、预算、白名单或频率策略阻断。",
                    error_code=policy.error_code,
                    error_message=policy.message,
                    details={
                        "estimated_cost_usd": str(policy.estimated_cost_usd),
                        "spent_today_usd": str(policy.spent_today_usd),
                        "daily_budget_usd": str(policy.daily_budget_usd),
                        "current_4h_run_count": policy.current_4h_run_count,
                        "max_runs_per_4h": policy.max_runs_per_4h,
                        "temporary_block": policy.is_temporary,
                        "retry_after_utc": (
                            policy.retry_after_utc.isoformat()
                            if policy.retry_after_utc
                            else None
                        ),
                    },
                )

            if cli_real_model_cost_confirmation_missing(request):
                retry_after = temporary_retry_after_utc(now_utc())
                self._mark_step_retry_waiting(
                    db_session,
                    step_row=step_row,
                    parent_model_analysis_run_id=parent_model_analysis_run_id,
                    error_code="cli_real_model_cost_not_confirmed",
                    error_message=(
                        "CLI-triggered 20C worker requires --confirm-real-model-cost before "
                        "it may ask stage 19 to call a real model."
                    ),
                    retry_after_utc=retry_after,
                )
                _commit_if_possible(db_session)
                return build_worker_result(
                    status=MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
                    trace_id=request.trace_id,
                    material_pack_id=str(getattr(chain_row, "material_pack_id", "") or "") or None,
                    chain_id=chain_id,
                    aggregation_run_id=str(getattr(chain_row, "aggregation_run_id", "") or "") or None,
                    strategy_signal_run_id=str(getattr(chain_row, "strategy_signal_run_id", "") or "") or None,
                    snapshot_id=str(getattr(chain_row, "snapshot_id", "") or "") or None,
                    model_review_skip_reason=(
                        "本轮未调用大模型；CLI worker 缺少 --confirm-real-model-cost。"
                    ),
                    model_review_block_reason=(
                        "CLI trigger_source requires --confirm-real-model-cost for real model cost confirmation."
                    ),
                    model_review_chain_status=ModelReviewChainStatus.BLOCKED.value,
                    model_review_basis="cli_cost_confirmation_blocked",
                    summary_text="本轮未调用大模型；CLI worker 缺少 --confirm-real-model-cost。",
                    error_code="cli_real_model_cost_not_confirmed",
                    error_message=(
                        "CLI-triggered worker did not pass --confirm-real-model-cost; "
                        "stage 19 was not called."
                    ),
                    details={"retry_after_utc": retry_after.isoformat()},
                )

            attempt_no = int(getattr(step_row, "attempt_no", 0) or 0) + 1
            input_hash = build_step_input_hash(
                chain_row=chain_row,
                step_row=step_row,
                parent_model_analysis_run_id=parent_model_analysis_run_id,
                attempt_no=attempt_no,
            )
            self._apply_step_state(
                db_session,
                step_row=step_row,
                status=ModelReviewChainStepStatus.RUNNING,
                attempt_no=attempt_no,
                parent_model_analysis_run_id=parent_model_analysis_run_id,
                model_analysis_run_id=None,
                started_at_utc=now_utc(),
                finished_at_utc=None,
                step_input_hash=input_hash,
                step_output_hash=None,
                error_code=None,
                error_message=None,
                retry_after_utc=None,
            )
            _commit_if_possible(db_session)
            model_result = self._call_stage19_for_step(
                db_session,
                request=request,
                chain_row=chain_row,
                step_row=step_row,
                parent_model_analysis_run_id=parent_model_analysis_run_id,
            )
            invoked_keys.append(str(getattr(step_row, "model_key", "") or ""))
            invoked_roles.append(str(getattr(step_row, "model_role", "") or ""))
            self._apply_stage19_result_to_step(
                db_session,
                step_row=step_row,
                stage19_result=model_result,
                attempt_no=attempt_no,
                input_hash=input_hash,
                parent_model_analysis_run_id=parent_model_analysis_run_id,
            )
            _commit_if_possible(db_session)
            return None
        finally:
            self._release_lock_safely(step_lock)

    def _call_stage19_for_step(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainWorkerRequest,
        chain_row: Any,
        step_row: Any,
        parent_model_analysis_run_id: str | None,
    ) -> Any:
        stage19_request = ModelAnalysisRequest(
            material_pack_id=str(getattr(chain_row, "material_pack_id", "") or ""),
            trigger_source=MODEL_REVIEW_TRIGGER_SOURCE_WORKER,
            dry_run=False,
            confirm_write=True,
            created_by="model_review_chain_worker",
            trace_id=request.trace_id,
            use_real_model=True,
            model_key=str(getattr(step_row, "model_key", "") or ""),
            confirm_real_model_cost=(
                request.trigger_source != TRIGGER_SOURCE_CLI or request.confirm_real_model_cost
            ),
            chain_id=str(getattr(chain_row, "chain_id", "") or ""),
            chain_step=int(getattr(step_row, "step_no", 0) or 0),
            parent_model_analysis_run_id=parent_model_analysis_run_id,
            model_role=str(getattr(step_row, "model_role", "") or ""),
            analysis_mode="relay_chain",
        )
        if hasattr(self._model_analysis_service, "run_model_analysis"):
            return self._model_analysis_service.run_model_analysis(db_session, request=stage19_request)
        return self._model_analysis_service(db_session=db_session, request=stage19_request)

    def _apply_stage19_result_to_step(
        self,
        db_session: Any,
        *,
        step_row: Any,
        stage19_result: Any,
        attempt_no: int,
        input_hash: str,
        parent_model_analysis_run_id: str | None,
    ) -> None:
        status = getattr(stage19_result, "status", ModelAnalysisStatus.FAILED)
        if not isinstance(status, ModelAnalysisStatus):
            status = ModelAnalysisStatus(str(status))
        if status in {ModelAnalysisStatus.SUCCESS, ModelAnalysisStatus.PARTIAL_SUCCESS, ModelAnalysisStatus.SKIPPED}:
            step_status_value = ModelReviewChainStepStatus.SUCCESS
            error_code = None
            error_message = None
        elif status == ModelAnalysisStatus.BLOCKED:
            error_code = getattr(stage19_result, "error_code", None) or "model_analysis_blocked"
            error_message = getattr(stage19_result, "error_message", None) or getattr(stage19_result, "message", "")
            step_status_value = (
                ModelReviewChainStepStatus.RETRY_WAITING
                if stage19_result_is_temporary_failure(stage19_result)
                and retry_available_after_attempt(
                    attempt_no=attempt_no,
                    max_retry_count=int(getattr(step_row, "max_retry_count", 0) or 0),
                )
                else ModelReviewChainStepStatus.BLOCKED
            )
        else:
            error_code = getattr(stage19_result, "error_code", None) or "model_analysis_failed"
            error_message = getattr(stage19_result, "error_message", None) or getattr(stage19_result, "message", "")
            if stage19_result_is_temporary_failure(stage19_result) and retry_available_after_attempt(
                attempt_no=attempt_no,
                max_retry_count=int(getattr(step_row, "max_retry_count", 0) or 0),
            ):
                step_status_value = ModelReviewChainStepStatus.RETRY_WAITING
            else:
                step_status_value = (
                    ModelReviewChainStepStatus.TIMEOUT
                    if "timeout" in str(error_code).lower()
                    else ModelReviewChainStepStatus.FAILED
                )
        run_id = str(getattr(stage19_result, "model_analysis_run_id", "") or "")
        output_hash = stable_sha256_text(
            {
                "model_analysis_run_id": run_id,
                "status": status.value,
                "error_code": error_code,
            }
        )
        self._apply_step_state(
            db_session,
            step_row=step_row,
            status=step_status_value,
            attempt_no=attempt_no,
            parent_model_analysis_run_id=parent_model_analysis_run_id,
            model_analysis_run_id=run_id or None,
            started_at_utc=getattr(step_row, "started_at_utc", None),
            finished_at_utc=now_utc(),
            step_input_hash=input_hash,
            step_output_hash=output_hash,
            error_code=error_code,
            error_message=error_message,
            retry_after_utc=(
                temporary_retry_after_utc(now_utc())
                if step_status_value == ModelReviewChainStepStatus.RETRY_WAITING
                else None
            ),
        )

    def _mark_step_retry_waiting(
        self,
        db_session: Any,
        *,
        step_row: Any,
        parent_model_analysis_run_id: str | None,
        error_code: str,
        error_message: str,
        retry_after_utc: Any,
    ) -> None:
        """Mark one recoverable worker-side block as retry_waiting."""

        self._apply_step_state(
            db_session,
            step_row=step_row,
            status=ModelReviewChainStepStatus.RETRY_WAITING,
            attempt_no=int(getattr(step_row, "attempt_no", 0) or 0),
            parent_model_analysis_run_id=parent_model_analysis_run_id,
            model_analysis_run_id=str(getattr(step_row, "model_analysis_run_id", "") or "") or None,
            started_at_utc=getattr(step_row, "started_at_utc", None),
            finished_at_utc=now_utc(),
            step_input_hash=str(getattr(step_row, "step_input_hash", "") or "") or None,
            step_output_hash=str(getattr(step_row, "step_output_hash", "") or "") or None,
            error_code=error_code,
            error_message=error_message,
            retry_after_utc=retry_after_utc,
        )

    def _apply_step_state(
        self,
        db_session: Any,
        *,
        step_row: Any,
        status: ModelReviewChainStepStatus,
        attempt_no: int,
        parent_model_analysis_run_id: str | None,
        model_analysis_run_id: str | None,
        started_at_utc: Any,
        finished_at_utc: Any,
        step_input_hash: str | None,
        step_output_hash: str | None,
        error_code: str | None,
        error_message: str | None,
        retry_after_utc: Any = _RETRY_AFTER_UNCHANGED,
    ) -> None:
        payload_kwargs: dict[str, Any] = {}
        if retry_after_utc is not _RETRY_AFTER_UNCHANGED:
            payload_kwargs["retry_after_utc"] = retry_after_utc
        payload = build_step_payload_from_row(
            step_row,
            status=status,
            attempt_no=attempt_no,
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
            parent_model_analysis_run_id=parent_model_analysis_run_id,
            model_analysis_run_id=model_analysis_run_id,
            step_input_hash=step_input_hash,
            step_output_hash=step_output_hash,
            error_code=error_code,
            error_message=error_message,
            **payload_kwargs,
        )
        self._repository.update_model_review_chain_step(db_session, step_row, payload=payload)
        if not hasattr(step_row, "updated_at_utc"):
            apply_step_payload_to_row(step_row, payload)

    def _persist_chain_state(self, db_session: Any, *, chain_row: Any, state: Any) -> None:
        payload = build_chain_payload_from_row(chain_row, state=state)
        self._repository.update_model_review_chain_run(db_session, chain_row, payload=payload)

    def _acquire_lock_or_skipped(
        self,
        *,
        request: ModelReviewChainWorkerRequest,
        key: str,
        chain_id: str | None = None,
        material_pack_id: str | None = None,
    ) -> ModelReviewWorkerLock | ModelReviewChainWorkerResult:
        owner = f"model_review_chain_worker:{request.trace_id}"
        try:
            lock = self._lock_manager.acquire_worker_lock(
                key=key,
                owner=owner,
                ttl_seconds=max(60, int(self._settings.scheduler_running_lock_ttl_seconds)),
            )
        except RedisError as exc:
            return build_worker_result(
                status=MODEL_REVIEW_CHAIN_WORKER_STATUS_BLOCKED,
                trace_id=request.trace_id,
                material_pack_id=material_pack_id or request.material_pack_id or None,
                chain_id=chain_id or request.chain_id,
                model_review_skip_reason="本轮未调用大模型；20C worker 获取 Redis 锁失败。",
                model_review_block_reason=str(exc),
                summary_text="本轮未调用大模型；20C worker 获取 Redis 锁失败。",
                error_code="worker_lock_failed",
                error_message=str(exc),
            )
        if not lock.acquired:
            return build_worker_result(
                status=MODEL_REVIEW_CHAIN_WORKER_STATUS_SKIPPED,
                trace_id=request.trace_id,
                material_pack_id=material_pack_id or request.material_pack_id or None,
                chain_id=chain_id or request.chain_id,
                model_review_skip_reason="本轮未调用大模型；已有 worker 持有同一 material/chain/step 锁。",
                model_review_block_reason="worker_lock_already_held",
                summary_text="本轮未调用大模型；已有 worker 持有同一 material/chain/step 锁。",
                error_code="worker_lock_already_held",
            )
        return lock

    def _release_lock_safely(self, lock: ModelReviewWorkerLock) -> None:
        try:
            self._lock_manager.release_worker_lock(lock)
        except RedisError:
            return


def run_model_review_chain_worker(
    *,
    db_session: Any,
    request: ModelReviewChainWorkerRequest,
    worker: ModelReviewChainWorker | None = None,
) -> ModelReviewChainWorkerResult:
    """Run the default stage-20C worker service."""

    active_worker = worker or ModelReviewChainWorker()
    return active_worker.run_model_review_chain_worker(db_session, request=request)


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = [
    "ALLOWED_MODEL_REVIEW_CHAIN_WORKER_TRIGGER_SOURCES",
    "ModelReviewChainWorker",
    "run_model_review_chain_worker",
]
