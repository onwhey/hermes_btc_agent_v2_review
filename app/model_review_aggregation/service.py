"""Stage-20A model review aggregation and reuse decision service.

Call chain:
scripts/run_model_review_aggregation.py::main
    -> app/model_review_aggregation/service.py::run_model_review_aggregation
    -> app/model_review_aggregation/repository.py::get_material_pack_by_id
    -> app/model_review_aggregation/repository.py::list_model_analysis_runs_for_material_pack
    -> app/model_review_aggregation/repository.py::list_success_model_review_candidates
    -> app/model_review_aggregation/fingerprint.py::build_material_fingerprint
    -> app/model_review_aggregation/fingerprint.py::calculate_reuse_base_bars
    -> app/model_review_aggregation/repository.py::create_model_review_aggregation_run
       (confirm-write only)

This file belongs to `app/model_review_aggregation`. It reads stage-18
analysis material packs and stage-19 model review rows, decides whether an
existing review can be used or reused, and emits a compact stage-20A summary.

It does not call model providers, does not request large model providers, does
not generate final trading advice, does not connect scheduler jobs, does not
read/write Redis, does not send Hermes, does not modify formal Kline tables,
does not read private trading state, and does not perform trading.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from app.core.config import AppSettings, get_settings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_review_aggregation.candidate_rules import (
    SUCCESS_MODEL_RUN_STATUSES,
    candidate_boundary_fields_are_false,
    candidate_material_pack_id,
    candidate_metadata_is_compatible,
    count_model_run_statuses,
    filter_exact_material_candidates,
)
from app.model_review_aggregation.fingerprint import (
    MaterialFingerprint,
    base_interval_to_ms,
    build_material_fingerprint,
    calculate_reuse_base_bars,
)
from app.model_review_aggregation.id_utils import build_model_review_aggregation_run_id
from app.model_review_aggregation.repository import (
    ModelReviewAggregationRepository,
    create_default_model_review_aggregation_repository,
)
from app.model_review_aggregation.result_builder import (
    NO_MODEL_CALL_TEXT,
    build_expired_blocked_result,
    build_failed_lookup_result,
    build_material_missing_result,
    build_no_result_blocked_result,
    build_persistence_payload,
    build_success_result_from_candidate,
    validate_request,
)
from app.model_review_aggregation.schema import (
    EXIT_FAILED,
    ModelReviewAggregationRequest,
    ModelReviewAggregationResult,
    ModelReviewAggregationStatus,
)

ALLOWED_MODEL_REVIEW_AGGREGATION_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI})


class ModelReviewAggregationService:
    """Coordinate one deterministic stage-20A aggregation attempt.

    Parameters: settings and repository are injectable for tests.
    Return value: service instance.
    Failure scenarios: invalid parameters, missing material pack, missing or
    expired model reviews, incompatible fingerprints, and persistence failures
    are converted into structured results.
    External effects: dry-run reads only; confirm-write writes one compact
    `model_review_aggregation_run` row when the material pack exists.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        repository: ModelReviewAggregationRepository | Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repository = repository or create_default_model_review_aggregation_repository()

    def run_model_review_aggregation(
        self,
        db_session: Any,
        *,
        request: ModelReviewAggregationRequest,
    ) -> ModelReviewAggregationResult:
        """Run stage-20A aggregation over existing stage-19 review rows.

        Parameters: caller-owned MySQL session and CLI-only request.
        Return value: compact result for CLI/tests and optional persistence.
        Failure scenarios: bad request, database read failure, no usable model
        review, expired review, or write failure.
        External effects: never calls a model provider; confirm-write writes
        only the stage-20A aggregation row and then commits if available.
        """

        trace_id = request.trace_id
        review_aggregation_run_id = build_model_review_aggregation_run_id(
            request.material_pack_id,
            trace_id=trace_id,
        )
        invalid_result = validate_request(
            request,
            review_aggregation_run_id=review_aggregation_run_id,
            trace_id=trace_id,
            settings=self._settings,
            allowed_trigger_sources=ALLOWED_MODEL_REVIEW_AGGREGATION_TRIGGER_SOURCES,
        )
        if invalid_result is not None:
            return invalid_result

        material_pack = self._load_material_pack_or_return_failure(
            db_session,
            request=request,
            review_aggregation_run_id=review_aggregation_run_id,
            trace_id=trace_id,
        )
        if isinstance(material_pack, ModelReviewAggregationResult):
            return material_pack
        if material_pack is None:
            return build_material_missing_result(
                request=request,
                review_aggregation_run_id=review_aggregation_run_id,
                trace_id=trace_id,
                settings=self._settings,
            )

        loaded = self._load_stage19_inputs_or_return_failure(
            db_session,
            request=request,
            review_aggregation_run_id=review_aggregation_run_id,
            trace_id=trace_id,
            material_pack=material_pack,
        )
        if isinstance(loaded, ModelReviewAggregationResult):
            return loaded
        model_runs, candidates = loaded
        current_fingerprint = build_material_fingerprint(material_pack)
        run_counts = count_model_run_statuses(model_runs)
        exact_candidates = filter_exact_material_candidates(candidates, request.material_pack_id)
        accepted_candidate = self._select_current_material_review(
            exact_candidates,
            current_fingerprint=current_fingerprint,
        )
        reuse_candidate = None
        expired_candidate = None
        if accepted_candidate is None:
            reuse_candidate, expired_candidate = self._select_reuse_or_expired_review(
                candidates,
                current_material_pack=material_pack,
                current_fingerprint=current_fingerprint,
            )

        result = self._build_result_from_decision(
            request=request,
            review_aggregation_run_id=review_aggregation_run_id,
            trace_id=trace_id,
            material_pack=material_pack,
            model_runs=model_runs,
            input_model_result_count=len(exact_candidates),
            run_counts=run_counts,
            accepted_candidate=accepted_candidate,
            reuse_candidate=reuse_candidate,
            expired_candidate=expired_candidate,
        )
        if request.confirm_write:
            return self._persist_result_or_failed(
                db_session,
                request=request,
                material_pack=material_pack,
                result=result,
                model_runs=model_runs,
                input_model_result_count=len(exact_candidates),
            )
        return result

    def _load_material_pack_or_return_failure(
        self,
        db_session: Any,
        *,
        request: ModelReviewAggregationRequest,
        review_aggregation_run_id: str,
        trace_id: str,
    ) -> Any | ModelReviewAggregationResult | None:
        try:
            return self._repository.get_material_pack_by_id(
                db_session,
                material_pack_id=request.material_pack_id,
            )
        except Exception as exc:  # noqa: BLE001 - service converts database failures.
            _rollback_if_possible(db_session)
            return build_failed_lookup_result(
                request=request,
                review_aggregation_run_id=review_aggregation_run_id,
                trace_id=trace_id,
                error_message=str(exc),
            )

    def _load_stage19_inputs_or_return_failure(
        self,
        db_session: Any,
        *,
        request: ModelReviewAggregationRequest,
        review_aggregation_run_id: str,
        trace_id: str,
        material_pack: Any,
    ) -> tuple[Sequence[Any], Sequence[Any]] | ModelReviewAggregationResult:
        try:
            model_runs = self._repository.list_model_analysis_runs_for_material_pack(
                db_session,
                material_pack_id=request.material_pack_id,
            )
            candidates = self._repository.list_success_model_review_candidates(
                db_session,
                symbol=str(getattr(material_pack, "symbol", "") or ""),
                base_interval=str(getattr(material_pack, "base_interval", "") or ""),
                higher_interval=str(getattr(material_pack, "higher_interval", "") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - service converts database failures.
            _rollback_if_possible(db_session)
            return build_failed_lookup_result(
                request=request,
                review_aggregation_run_id=review_aggregation_run_id,
                trace_id=trace_id,
                error_message=str(exc),
                material_pack=material_pack,
            )
        return tuple(model_runs), tuple(candidates)

    def _select_current_material_review(
        self,
        candidates: Sequence[Any],
        *,
        current_fingerprint: MaterialFingerprint,
    ) -> Any | None:
        """Return latest usable stage-19 result for the same material pack."""

        for candidate in candidates:
            if self._candidate_is_compatible(
                candidate,
                current_fingerprint=current_fingerprint,
                require_same_material_hash=True,
            ):
                return candidate
        return None

    def _select_reuse_or_expired_review(
        self,
        candidates: Sequence[Any],
        *,
        current_material_pack: Any,
        current_fingerprint: MaterialFingerprint,
    ) -> tuple[Any | None, Any | None]:
        """Return one reusable old result or the latest compatible expired row.

        Reuse is deliberately conservative: same symbol/base/higher interval,
        matching compact material summary/risk/structure/volatility/direction
        fingerprint, compatible model-review metadata, and no more than the
        configured number of base interval bars since the previous material.
        """

        interval_ms = base_interval_to_ms(str(getattr(current_material_pack, "base_interval", "") or ""))
        max_base_bars = int(self._settings.model_review_reuse_max_base_bars)
        expired_candidate = None
        for candidate in candidates:
            if candidate_material_pack_id(candidate) == getattr(current_material_pack, "material_pack_id", ""):
                continue
            if not self._candidate_is_compatible(
                candidate,
                current_fingerprint=current_fingerprint,
                require_same_material_hash=True,
            ):
                continue
            previous_fingerprint = build_material_fingerprint(candidate.material_pack)
            reuse_base_bars = calculate_reuse_base_bars(
                current_open_time_ms=current_fingerprint.base_open_time_end_ms,
                previous_open_time_ms=previous_fingerprint.base_open_time_end_ms,
                interval_ms=interval_ms,
            )
            if reuse_base_bars is None:
                continue
            setattr(candidate, "reuse_base_bars", reuse_base_bars)
            if reuse_base_bars <= max_base_bars:
                return candidate, expired_candidate
            if expired_candidate is None:
                expired_candidate = candidate
        return None, expired_candidate

    def _candidate_is_compatible(
        self,
        candidate: Any,
        *,
        current_fingerprint: MaterialFingerprint,
        require_same_material_hash: bool,
    ) -> bool:
        run = candidate.model_analysis_run
        if str(getattr(run, "status", "") or "") not in SUCCESS_MODEL_RUN_STATUSES:
            return False
        if not candidate_boundary_fields_are_false(run):
            return False
        if not candidate_metadata_is_compatible(run, settings=self._settings):
            return False
        if require_same_material_hash:
            previous_fingerprint = build_material_fingerprint(candidate.material_pack)
            if previous_fingerprint.fingerprint != current_fingerprint.fingerprint:
                return False
        return True

    def _build_result_from_decision(
        self,
        *,
        request: ModelReviewAggregationRequest,
        review_aggregation_run_id: str,
        trace_id: str,
        material_pack: Any,
        model_runs: Sequence[Any],
        input_model_result_count: int,
        run_counts: Any,
        accepted_candidate: Any | None,
        reuse_candidate: Any | None,
        expired_candidate: Any | None,
    ) -> ModelReviewAggregationResult:
        if accepted_candidate is not None:
            return build_success_result_from_candidate(
                request=request,
                review_aggregation_run_id=review_aggregation_run_id,
                material_pack=material_pack,
                candidate=accepted_candidate,
                model_runs=model_runs,
                input_model_result_count=input_model_result_count,
                run_counts=run_counts,
                trace_id=trace_id,
                reused=False,
                reuse_base_bars=0,
                reuse_status="current_material_pack_result",
                settings=self._settings,
            )
        if reuse_candidate is not None:
            return build_success_result_from_candidate(
                request=request,
                review_aggregation_run_id=review_aggregation_run_id,
                material_pack=material_pack,
                candidate=reuse_candidate,
                model_runs=model_runs,
                input_model_result_count=input_model_result_count,
                run_counts=run_counts,
                trace_id=trace_id,
                reused=True,
                reuse_base_bars=int(getattr(reuse_candidate, "reuse_base_bars", 0) or 0),
                reuse_status="reused_within_base_bar_ttl",
                settings=self._settings,
            )
        if expired_candidate is not None:
            return build_expired_blocked_result(
                request=request,
                review_aggregation_run_id=review_aggregation_run_id,
                material_pack=material_pack,
                candidate=expired_candidate,
                model_runs=model_runs,
                input_model_result_count=input_model_result_count,
                run_counts=run_counts,
                trace_id=trace_id,
                settings=self._settings,
            )
        return build_no_result_blocked_result(
            request=request,
            review_aggregation_run_id=review_aggregation_run_id,
            material_pack=material_pack,
            model_runs=model_runs,
            input_model_result_count=input_model_result_count,
            run_counts=run_counts,
            trace_id=trace_id,
            reason_code="no_model_review_result",
            settings=self._settings,
        )

    def _persist_result_or_failed(
        self,
        db_session: Any,
        *,
        request: ModelReviewAggregationRequest,
        material_pack: Any,
        result: ModelReviewAggregationResult,
        model_runs: Sequence[Any],
        input_model_result_count: int,
    ) -> ModelReviewAggregationResult:
        """Persist one compact stage-20A row or convert write failure."""

        try:
            payload = build_persistence_payload(
                request=request,
                material_pack=material_pack,
                result=result,
                model_runs=model_runs,
                input_model_result_count=input_model_result_count,
            )
            self._repository.create_model_review_aggregation_run(db_session, payload=payload)
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001 - persistence failure is reported to the caller.
            _rollback_if_possible(db_session)
            failed_message = f"Stage-20A aggregation persistence failed: {exc}"
            return replace(
                result,
                status=ModelReviewAggregationStatus.FAILED,
                exit_code=EXIT_FAILED,
                error_code="model_review_aggregation_persistence_failed",
                error_message=failed_message,
                summary_text=f"{NO_MODEL_CALL_TEXT} {failed_message}",
            )
        return result


def run_model_review_aggregation(
    *,
    db_session: Any,
    request: ModelReviewAggregationRequest,
    service: ModelReviewAggregationService | None = None,
) -> ModelReviewAggregationResult:
    """Convenience app-service function used by CLI and tests."""

    active_service = service or create_default_model_review_aggregation_service()
    return active_service.run_model_review_aggregation(db_session, request=request)


def create_default_model_review_aggregation_service() -> ModelReviewAggregationService:
    """Create the default deterministic stage-20A aggregation service."""

    return ModelReviewAggregationService()


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


__all__ = [
    "ALLOWED_MODEL_REVIEW_AGGREGATION_TRIGGER_SOURCES",
    "ModelReviewAggregationService",
    "create_default_model_review_aggregation_service",
    "run_model_review_aggregation",
]
