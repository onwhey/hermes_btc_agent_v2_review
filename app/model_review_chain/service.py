"""Stage-20B model review chain state-machine service.

Call chain:
scripts/run_model_review_chain.py::main
    -> app/model_review_chain/service.py::run_model_review_chain
    -> app/model_review_chain/repository.py::get_material_pack_by_id
       (create mode)
    -> app/model_review_chain/repository.py::create_model_review_chain_run
       (confirm-write create mode)
    -> app/model_review_chain/repository.py::create_model_review_chain_step
       (confirm-write create mode)
    -> app/model_review_chain/repository.py::create_mock_model_analysis_run
       (confirm-write mock step attempts only)
    -> app/model_review_chain/repository.py::update_model_review_chain_step
    -> app/model_review_chain/repository.py::update_model_review_chain_run

Resume call chain:
scripts/run_model_review_chain.py::main
    -> app/model_review_chain/service.py::run_model_review_chain
    -> app/model_review_chain/repository.py::get_chain_run_by_chain_id
    -> app/model_review_chain/repository.py::list_chain_steps
    -> app/model_review_chain/repository.py::create_mock_model_analysis_run
       (only for resumable non-success steps)

This file belongs to `app/model_review_chain`. It creates and resumes a mock
multi-step review chain, writes compact chain/step state, and records mock step
attempts in `model_analysis_run` for traceability.

It does not call real model providers, does not request DeepSeek/GPT/Claude,
does not create `model_analysis_result`, does not connect scheduler jobs, does
not generate final trading advice, does not read/write Redis, does not send
Hermes, does not modify formal Kline tables, does not read private trading
state, and does not perform trading.
"""

from __future__ import annotations

from typing import Any

from app.core.time_utils import now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_analysis.types import ModelAnalysisStatus
from app.model_review_chain.chain_profile import resolve_chain_profile
from app.model_review_chain.id_utils import (
    build_chain_id,
    build_chain_model_analysis_run_id,
    stable_sha256_text,
)
from app.model_review_chain.payload_builder import (
    apply_step_payload_to_row,
    build_chain_payload_from_row,
    build_initial_chain_payload,
    build_initial_step_payload,
    build_mock_model_analysis_payload,
    build_step_input_hash,
    build_step_payload_from_row,
    build_transient_chain_rows,
    clone_row,
)
from app.model_review_chain.repository import (
    ModelReviewChainRepository,
    create_default_model_review_chain_repository,
)
from app.model_review_chain.result_builder import (
    build_blocked_result,
    build_failed_result,
    build_result_from_chain_rows,
    build_step_result,
    latest_successful_parent,
    merge_step_results,
    validate_chain_request,
)
from app.model_review_chain.schema import (
    ChainProfile,
    ModelReviewChainRequest,
    ModelReviewChainResult,
    ModelReviewChainStepResult,
    ModelReviewChainStepStatus,
)
from app.model_review_chain.state_machine import (
    calculate_chain_state,
    step_is_resumable,
    step_retry_is_available,
    step_status,
)

ALLOWED_MODEL_REVIEW_CHAIN_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI})


class ModelReviewChainService:
    """Coordinate one deterministic stage-20B chain operation.

    Parameters: repository is injectable for tests.
    Return value: service instance.
    Failure scenarios: invalid parameters, missing material/chain rows, unknown
    chain profile, exhausted retry budget, and persistence failures are
    converted into structured results.
    External effects: dry-run reads only; confirm-write writes stage-20B rows
    and compact mock `model_analysis_run` attempts. It never calls providers.
    """

    def __init__(self, *, repository: ModelReviewChainRepository | Any | None = None) -> None:
        self._repository = repository or create_default_model_review_chain_repository()

    def run_model_review_chain(self, db_session: Any, *, request: ModelReviewChainRequest) -> ModelReviewChainResult:
        """Create or resume one mock model-review chain.

        Parameters: caller-owned MySQL session and CLI-only request.
        Return value: compact chain result for CLI/tests and optional
        persistence.
        Failure scenarios: bad request, missing rows, or database failures.
        External effects: no external service calls; confirm-write commits if
        the caller session exposes `commit`.
        """

        invalid_result = validate_chain_request(
            request,
            allowed_trigger_sources=ALLOWED_MODEL_REVIEW_CHAIN_TRIGGER_SOURCES,
        )
        if invalid_result is not None:
            return invalid_result
        if request.resume:
            return self._resume_existing_chain(db_session, request=request)
        return self._create_new_chain(db_session, request=request)

    def _create_new_chain(self, db_session: Any, *, request: ModelReviewChainRequest) -> ModelReviewChainResult:
        profile = resolve_chain_profile(request.chain_key)
        chain_id = build_chain_id(
            material_pack_id=request.material_pack_id,
            chain_key=request.chain_key,
            trace_id=request.trace_id,
        )
        if profile is None:
            return build_blocked_result(
                request=request,
                chain_id=chain_id,
                error_code="unknown_chain_key",
                error_message=f"Unsupported chain_key: {request.chain_key}",
            )
        try:
            material_pack = self._repository.get_material_pack_by_id(
                db_session,
                material_pack_id=request.material_pack_id,
            )
        except Exception as exc:  # noqa: BLE001 - service converts database failures.
            _rollback_if_possible(db_session)
            return build_failed_result(
                request=request,
                chain_id=chain_id,
                chain_key=profile.chain_key,
                chain_profile_version=profile.chain_profile_version,
                error_code="material_pack_lookup_failed",
                error_message=str(exc),
            )
        if material_pack is None:
            return build_blocked_result(
                request=request,
                chain_id=chain_id,
                chain_key=profile.chain_key,
                chain_profile_version=profile.chain_profile_version,
                error_code="material_pack_not_found",
                error_message="Stage-18 material_pack does not exist.",
            )
        if request.dry_run:
            chain_row, step_rows = build_transient_chain_rows(
                request=request,
                profile=profile,
                chain_id=chain_id,
                material_pack=material_pack,
            )
            return self._execute_chain_steps(
                db_session,
                request=request,
                chain_row=chain_row,
                step_rows=step_rows,
                profile=profile,
                persist=False,
            )
        return self._persist_and_execute_new_chain(
            db_session,
            request=request,
            profile=profile,
            chain_id=chain_id,
            material_pack=material_pack,
        )

    def _persist_and_execute_new_chain(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainRequest,
        profile: ChainProfile,
        chain_id: str,
        material_pack: Any,
    ) -> ModelReviewChainResult:
        try:
            chain_row = self._repository.create_model_review_chain_run(
                db_session,
                payload=build_initial_chain_payload(
                    request=request,
                    profile=profile,
                    chain_id=chain_id,
                    material_pack=material_pack,
                ),
            )
            step_rows = []
            previous_step_id: str | None = None
            for step_definition in profile.steps:
                step_payload = build_initial_step_payload(
                    chain_id=chain_id,
                    definition=step_definition,
                    parent_step_id=previous_step_id,
                    max_retry_count=request.max_retry_count,
                )
                step_row = self._repository.create_model_review_chain_step(db_session, payload=step_payload)
                step_rows.append(step_row)
                previous_step_id = step_payload.chain_step_id
            return self._execute_chain_steps(
                db_session,
                request=request,
                chain_row=chain_row,
                step_rows=tuple(step_rows),
                profile=profile,
                persist=True,
            )
        except Exception as exc:  # noqa: BLE001 - persistence failure is reported to the caller.
            _rollback_if_possible(db_session)
            return build_failed_result(
                request=request,
                chain_id=chain_id,
                chain_key=profile.chain_key,
                chain_profile_version=profile.chain_profile_version,
                error_code="chain_create_failed",
                error_message=str(exc),
            )

    def _resume_existing_chain(self, db_session: Any, *, request: ModelReviewChainRequest) -> ModelReviewChainResult:
        chain_id = str(request.chain_id or "").strip()
        try:
            chain_row = self._repository.get_chain_run_by_chain_id(db_session, chain_id=chain_id)
            if chain_row is None:
                return build_blocked_result(
                    request=request,
                    chain_id=chain_id,
                    error_code="chain_not_found",
                    error_message="Stage-20B chain does not exist.",
                )
            step_rows = self._repository.list_chain_steps(db_session, chain_id=chain_id)
        except Exception as exc:  # noqa: BLE001 - service converts database failures.
            _rollback_if_possible(db_session)
            return build_failed_result(
                request=request,
                chain_id=chain_id,
                error_code="chain_lookup_failed",
                error_message=str(exc),
            )
        profile = resolve_chain_profile(str(getattr(chain_row, "chain_key", "") or ""))
        if profile is None:
            return build_blocked_result(
                request=request,
                chain_id=chain_id,
                error_code="unknown_chain_key",
                error_message=f"Unsupported chain_key on existing chain: {getattr(chain_row, 'chain_key', '')}",
            )
        if not step_rows:
            return build_blocked_result(
                request=request,
                chain_id=chain_id,
                chain_key=profile.chain_key,
                chain_profile_version=profile.chain_profile_version,
                error_code="chain_steps_not_found",
                error_message="Stage-20B chain has no persisted steps.",
            )
        if request.dry_run:
            chain_row = clone_row(chain_row)
            step_rows = tuple(clone_row(step_row) for step_row in step_rows)
        return self._execute_chain_steps(
            db_session,
            request=request,
            chain_row=chain_row,
            step_rows=tuple(step_rows),
            profile=profile,
            persist=request.confirm_write,
        )

    def _execute_chain_steps(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainRequest,
        chain_row: Any,
        step_rows: tuple[Any, ...],
        profile: ChainProfile,
        persist: bool,
    ) -> ModelReviewChainResult:
        mock_execution_count = 0
        result_steps: list[ModelReviewChainStepResult] = []
        parent_model_analysis_run_id: str | None = latest_successful_parent(step_rows)
        try:
            for step_row in step_rows:
                current_status = step_status(step_row)
                if current_status == ModelReviewChainStepStatus.SUCCESS:
                    parent_model_analysis_run_id = str(getattr(step_row, "model_analysis_run_id", "") or "")
                    result_steps.append(build_step_result(step_row, skipped_due_to_success_resume=request.resume))
                    continue
                if request.resume and not step_is_resumable(step_row):
                    result_steps.append(build_step_result(step_row))
                    continue
                if not step_retry_is_available(step_row):
                    result_steps.append(build_step_result(step_row, retry_blocked=True))
                    continue
                mock_execution_count += 1
                parent_model_analysis_run_id = self._execute_one_mock_step(
                    db_session,
                    request=request,
                    chain_row=chain_row,
                    step_row=step_row,
                    parent_model_analysis_run_id=parent_model_analysis_run_id,
                    persist=persist,
                )
                result_steps.append(build_step_result(step_row))
                if step_status(step_row) != ModelReviewChainStepStatus.SUCCESS:
                    break

            state = calculate_chain_state(step_rows, total_steps=len(step_rows))
            if persist:
                self._repository.update_model_review_chain_run(
                    db_session,
                    chain_row,
                    payload=build_chain_payload_from_row(chain_row, state=state),
                )
                _commit_if_possible(db_session)
            all_steps = merge_step_results(step_rows, result_steps)
            return build_result_from_chain_rows(
                request=request,
                chain_row=chain_row,
                profile=profile,
                state=state,
                steps=all_steps,
                mock_execution_count=mock_execution_count,
            )
        except Exception as exc:  # noqa: BLE001 - persistence failure is reported to the caller.
            _rollback_if_possible(db_session)
            return build_failed_result(
                request=request,
                chain_id=str(getattr(chain_row, "chain_id", request.chain_id or "")),
                chain_key=profile.chain_key,
                chain_profile_version=profile.chain_profile_version,
                error_code="chain_execution_failed",
                error_message=str(exc),
            )

    def _execute_one_mock_step(
        self,
        db_session: Any,
        *,
        request: ModelReviewChainRequest,
        chain_row: Any,
        step_row: Any,
        parent_model_analysis_run_id: str | None,
        persist: bool,
    ) -> str | None:
        attempt_no = int(getattr(step_row, "attempt_no", 0) or 0) + 1
        started_at = now_utc()
        input_hash = build_step_input_hash(
            chain_row=chain_row,
            step_row=step_row,
            parent_model_analysis_run_id=parent_model_analysis_run_id,
            attempt_no=attempt_no,
        )
        self._apply_step_state(
            db_session,
            step_row=step_row,
            persist=persist,
            status=ModelReviewChainStepStatus.RUNNING,
            attempt_no=attempt_no,
            started_at_utc=started_at,
            finished_at_utc=None,
            parent_model_analysis_run_id=parent_model_analysis_run_id,
            model_analysis_run_id=None,
            step_input_hash=input_hash,
            step_output_hash=None,
            error_code=None,
            error_message=None,
        )
        step_failed = request.simulate_step_failure == int(getattr(step_row, "step_no", 0) or 0)
        model_status = ModelAnalysisStatus.FAILED if step_failed else ModelAnalysisStatus.SUCCESS
        model_analysis_run_id = build_chain_model_analysis_run_id(
            chain_id=str(getattr(chain_row, "chain_id")),
            step_no=int(getattr(step_row, "step_no")),
            attempt_no=attempt_no,
        )
        output_hash = stable_sha256_text(
            {
                "model_analysis_run_id": model_analysis_run_id,
                "status": model_status.value,
                "step_no": int(getattr(step_row, "step_no")),
            }
        )
        if persist:
            self._repository.create_mock_model_analysis_run(
                db_session,
                payload=build_mock_model_analysis_payload(
                    request=request,
                    chain_row=chain_row,
                    step_row=step_row,
                    attempt_no=attempt_no,
                    model_analysis_run_id=model_analysis_run_id,
                    parent_model_analysis_run_id=parent_model_analysis_run_id,
                    status=model_status,
                    input_hash=input_hash,
                    output_hash=output_hash,
                    simulated_failure=step_failed,
                ),
            )
        self._apply_step_state(
            db_session,
            step_row=step_row,
            persist=persist,
            status=ModelReviewChainStepStatus.FAILED if step_failed else ModelReviewChainStepStatus.SUCCESS,
            attempt_no=attempt_no,
            started_at_utc=started_at,
            finished_at_utc=now_utc(),
            parent_model_analysis_run_id=parent_model_analysis_run_id,
            model_analysis_run_id=model_analysis_run_id,
            step_input_hash=input_hash,
            step_output_hash=output_hash,
            error_code="simulated_step_failure" if step_failed else None,
            error_message="Mock chain step failed by simulation." if step_failed else None,
        )
        return None if step_failed else model_analysis_run_id

    def _apply_step_state(
        self,
        db_session: Any,
        *,
        step_row: Any,
        persist: bool,
        status: ModelReviewChainStepStatus,
        attempt_no: int,
        started_at_utc: Any,
        finished_at_utc: Any,
        parent_model_analysis_run_id: str | None,
        model_analysis_run_id: str | None,
        step_input_hash: str | None,
        step_output_hash: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
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
        )
        if persist:
            self._repository.update_model_review_chain_step(db_session, step_row, payload=payload)
        else:
            apply_step_payload_to_row(step_row, payload)


def run_model_review_chain(db_session: Any, *, request: ModelReviewChainRequest) -> ModelReviewChainResult:
    """Run the default stage-20B chain service."""

    return ModelReviewChainService().run_model_review_chain(db_session, request=request)


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = ["ModelReviewChainService", "run_model_review_chain"]
