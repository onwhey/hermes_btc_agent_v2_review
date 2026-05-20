"""Stage-19 model analysis review-gate service.

Call chain:
scripts/run_model_analysis.py::main
    -> app/model_analysis/service.py::run_model_analysis
    -> app/model_analysis/repository.py::get_material_pack_by_id
    -> app/model_analysis/provider_resolution.py::resolve_provider_for_request
    -> app/model_analysis/prompt_builder.py::build_model_review_prompt
    -> app/model_analysis/repository.py::create_model_analysis_run
       (real confirm-write only, status=running, before external call)
    -> app/model_analysis/providers/mock.py::MockModelReviewProvider.review_material
       or app/model_analysis/providers/deepseek.py::DeepSeekReviewProvider.call_review_model
    -> app/model_analysis/schema_validator.py::validate_model_review_output
    -> app/model_analysis/repository.py::update_model_analysis_run
       or app/model_analysis/repository.py::create_model_analysis_run
    -> app/model_analysis/repository.py::create_model_analysis_result

This file belongs to `app/model_analysis`. It consumes only reviewable
stage-18 `analysis_material_pack` rows, runs either the safe mock provider or a
manually gated real provider, validates schema, optionally writes stage-19
tables, and optionally sends a Chinese Hermes summary.

It does not read market Klines directly, does not modify formal Kline tables,
does not read/write Redis, does not implement strategy classes, does not judge
long/short from Klines, does not generate final trading advice, does not read
private trading state, and does not perform trading.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSendStatus, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.core.time_utils import now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_analysis.artifact_store import ArtifactWriteResult, write_model_provider_artifact
from app.model_analysis.cost_estimator import estimate_provider_call_cost
from app.model_analysis.hermes_formatter import (
    build_model_analysis_artifact_write_failed_visible_body,
    build_model_analysis_oversized_response_visible_body,
    build_model_analysis_provider_call_failed_visible_body,
    build_model_analysis_visible_body,
)
from app.model_analysis.material_pack_reviewability import validate_material_pack_reviewability
from app.model_analysis.payloads import (
    build_blocked_result,
    build_failed_result,
    build_invalid_request_result,
    build_model_analysis_result_id,
    build_model_analysis_run_id,
    build_result_payload,
    build_review_version_key,
    build_run_payload,
    build_skipped_result_from_existing,
    build_success_result,
    optional_text,
)
from app.model_analysis.prompt_builder import build_model_review_prompt
from app.model_analysis.provider_resolution import ProviderResolution, resolve_provider_for_request
from app.model_analysis.providers.base import ProviderCallError, ProviderRequest
from app.model_analysis.repository import (
    ModelAnalysisRepository,
    create_default_model_analysis_repository,
)
from app.model_analysis.schema_validator import validate_model_review_output
from app.model_analysis.service_helpers import (
    build_artifact_payload,
    build_limit_error,
    sha256_text,
)
from app.model_analysis.types import (
    MODEL_ANALYSIS_EVENT_SOURCE,
    MODEL_REVIEW_MODEL_KEY_DEFAULT,
    MODEL_REVIEW_MODEL_ROLE_DEFAULT,
    MODEL_REVIEW_MODE_DEFAULT,
    MODEL_REVIEW_PROVIDER_MOCK,
    ModelAnalysisHermesStatus,
    ModelAnalysisRequest,
    ModelAnalysisServiceResult,
    ModelAnalysisStatus,
    ModelProviderCallArtifactPersistencePayload,
    ModelProviderResult,
    PromptBuildResult,
    ReviewDecision,
)

try:
    from sqlalchemy.exc import IntegrityError
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    IntegrityError = None  # type: ignore[assignment]

ALLOWED_MODEL_ANALYSIS_TRIGGER_SOURCES = frozenset({TRIGGER_SOURCE_CLI})
FINAL_REVIEW_RESULT_STATUSES = (
    ModelAnalysisStatus.SUCCESS,
    ModelAnalysisStatus.PARTIAL_SUCCESS,
)


class ModelAnalysisService:
    """Coordinate one stage-19 model review-gate attempt.

    Parameters: settings, repository, provider, and alert sender are injectable
    for tests.
    Return value: service instance.
    Failure scenarios: invalid request, missing/non-success material pack,
    prompt/output size limits, schema invalid output, persistence failure, and
    Hermes failure are converted into structured results.
    External effects: dry-run reads only; confirm-write may write stage-19
    rows and may send Hermes according to config.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        repository: ModelAnalysisRepository | Any | None = None,
        provider: Any | None = None,
        alert_sender: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repository = repository or create_default_model_analysis_repository()
        self._provider = provider
        self._alert_sender = alert_sender or _default_alert_sender

    def run_model_analysis(
        self,
        db_session: Any,
        *,
        request: ModelAnalysisRequest,
    ) -> ModelAnalysisServiceResult:
        """Run a bounded model review for one stage-18 material pack.

        Parameters: caller-owned MySQL session and stage-19 request.
        Return value: compact `ModelAnalysisServiceResult`.
        Failure scenarios: see class docstring.
        External effects: dry-run never writes; confirm-write writes only when
        `MODEL_REVIEW_ENABLED=true`.
        """

        trace_id = request.trace_id or uuid.uuid4().hex
        run_id = build_model_analysis_run_id(request.material_pack_id)
        invalid_result = _validate_request(request, model_analysis_run_id=run_id, trace_id=trace_id)
        if invalid_result is not None:
            return invalid_result
        if request.confirm_write and not self._settings.model_review_enabled:
            return build_blocked_result(
                request,
                model_analysis_run_id=run_id,
                review_version_key=None,
                trace_id=trace_id,
                message="MODEL_REVIEW_ENABLED=false blocks confirmed writes.",
                error_code="model_review_disabled",
                model_key=request.model_key or MODEL_REVIEW_MODEL_KEY_DEFAULT,
                model_role=MODEL_REVIEW_MODEL_ROLE_DEFAULT,
                analysis_mode=MODEL_REVIEW_MODE_DEFAULT,
            )

        provider_resolution = resolve_provider_for_request(
            settings=self._settings,
            request=request,
            injected_provider=self._provider,
        )
        review_version_key = build_review_version_key(
            material_pack_id=request.material_pack_id,
            model_provider=provider_resolution.provider_name,
            model_key=provider_resolution.model_key,
            model_name=provider_resolution.model_name,
            model_version=provider_resolution.model_version,
            profile_hash=provider_resolution.profile_hash,
            prompt_template_hash=provider_resolution.prompt_template_hash,
            prompt_template_version=provider_resolution.prompt_template_version,
            review_schema_version=provider_resolution.review_schema_version,
            review_mode=provider_resolution.analysis_mode,
        )
        if provider_resolution.blocked_message:
            return build_blocked_result(
                request,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message=provider_resolution.blocked_message,
                error_code=provider_resolution.blocked_error_code or "provider_not_supported",
                model_key=provider_resolution.model_key,
                model_role=provider_resolution.model_role,
                analysis_mode=provider_resolution.analysis_mode,
            )
        try:
            material_pack = self._repository.get_material_pack_by_id(
                db_session,
                material_pack_id=request.material_pack_id,
            )
        except Exception as exc:  # noqa: BLE001 - database read failure is a service failure.
            _rollback_if_possible(db_session)
            return build_failed_result(
                request,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="Stage-18 material pack lookup failed.",
                error_message=str(exc),
            )
        if material_pack is None:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                material_pack=None,
                prompt=None,
                provider_result=None,
                provider_metadata=provider_resolution,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="analysis_material_pack does not exist.",
                error_code="material_pack_not_found",
            )
        reviewability = validate_material_pack_reviewability(material_pack)
        if not reviewability.is_reviewable:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                material_pack=material_pack,
                prompt=None,
                provider_result=None,
                provider_metadata=provider_resolution,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message=reviewability.message,
                error_code=reviewability.error_code,
                error_message=reviewability.error_message,
            )

        try:
            existing_result = self._repository.get_existing_result_by_review_version_key(
                db_session,
                review_version_key=review_version_key,
            )
        except Exception as exc:  # noqa: BLE001 - database read failure is a service failure.
            _rollback_if_possible(db_session)
            return build_failed_result(
                request,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="Model analysis idempotency check failed.",
                error_message=str(exc),
            )
        if existing_result is not None:
            return build_skipped_result_from_existing(
                request,
                existing_result=existing_result,
                model_analysis_run_id=run_id,
                trace_id=trace_id,
                details={"skip_reason": "already_exists"},
            )

        prompt = build_model_review_prompt(material_pack, settings=self._settings)
        input_limit_error = build_limit_error(
            char_count=prompt.input_char_count,
            byte_count=prompt.input_byte_count,
            max_chars=self._settings.model_review_max_input_chars,
            max_bytes=self._settings.model_review_max_input_bytes,
            prefix="input",
        )
        if input_limit_error is not None:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_result=None,
                provider_metadata=provider_resolution,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message=input_limit_error["message"],
                error_code=input_limit_error["error_code"],
            )

        provider_resolution = self._enrich_provider_resolution_before_call(
            provider_resolution=provider_resolution,
            prompt=prompt,
        )
        provider_request = self._build_real_provider_request_if_needed(
            provider_resolution=provider_resolution,
            prompt=prompt,
            material_pack_id=request.material_pack_id,
            model_analysis_run_id=run_id,
            trace_id=trace_id,
        )
        running_run_row = None
        if provider_resolution.is_real_model and request.confirm_write and self._settings.model_review_enabled:
            running_result = self._persist_running_real_model_run(
                db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_metadata=provider_resolution,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
            )
            if isinstance(running_result, ModelAnalysisServiceResult):
                return running_result
            running_run_row = running_result

        if not request.dry_run and provider_resolution.is_real_model and (
            request.capture_raw_request or self._settings.model_review_capture_raw_request
        ):
            request_artifact = self._write_request_artifact_or_return_failure(
                db_session=db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_request=provider_request,
                provider_resolution=provider_resolution,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                running_run_row=running_run_row,
            )
            if isinstance(request_artifact, ModelAnalysisServiceResult):
                return request_artifact

        provider_output = self._call_provider(
            db_session=db_session,
            request=request,
            prompt=prompt,
            provider_resolution=provider_resolution,
            provider_request=provider_request,
            model_analysis_run_id=run_id,
            review_version_key=review_version_key,
            material_pack=material_pack,
            trace_id=trace_id,
            running_run_row=running_run_row,
        )
        if isinstance(provider_output, ModelAnalysisServiceResult):
            return provider_output
        provider_resolution = self._enrich_provider_resolution_after_call(
            provider_resolution=provider_resolution,
            provider_output=provider_output,
            prompt=prompt,
        )
        artifact_payloads: list[ModelProviderCallArtifactPersistencePayload] = []
        raw_response_oversized = build_limit_error(
            char_count=int(getattr(provider_output, "raw_response_char_count", 0) or 0),
            byte_count=int(getattr(provider_output, "raw_response_byte_count", 0) or 0),
            max_chars=self._settings.model_review_max_output_chars,
            max_bytes=self._settings.model_review_max_output_bytes,
            prefix="raw_response",
        )
        if not request.dry_run and provider_resolution.is_real_model and (
            raw_response_oversized is not None or request.capture_raw_response or self._settings.model_review_capture_raw_response
        ):
            artifact = self._write_response_artifact_if_allowed(
                db_session=db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_output=provider_output,
                provider_resolution=provider_resolution,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                capture_reason=(
                    "oversized_response"
                    if raw_response_oversized is not None
                    else "capture_raw_response"
                ),
                running_run_row=running_run_row,
            )
            if isinstance(artifact, ModelAnalysisServiceResult):
                return artifact
            if artifact is not None:
                provider_resolution.raw_response_storage_ref = artifact.storage_ref
                artifact_payloads.append(
                    build_artifact_payload(
                        artifact,
                        model_analysis_run_id=run_id,
                        provider_resolution=provider_resolution,
                    )
                )
        output_limit_error = build_limit_error(
            char_count=provider_output.output_char_count,
            byte_count=provider_output.output_byte_count,
            max_chars=self._settings.model_review_max_output_chars,
            max_bytes=self._settings.model_review_max_output_bytes,
            prefix="output",
        )
        if output_limit_error is not None:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_result=provider_output,
                provider_metadata=provider_resolution,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message=output_limit_error["message"],
                error_code=output_limit_error["error_code"],
                artifact_payloads=artifact_payloads,
                send_oversized_alert=provider_resolution.is_real_model,
                running_run_row=running_run_row,
            )

        schema_result = validate_model_review_output(provider_output.output)
        if not schema_result.is_valid:
            blocked_error_code = schema_result.error_code or "schema_invalid"
            blocked_error_message = schema_result.error_message
            blocked_message = "Model review output schema is invalid."
            send_oversized_alert = False
            if raw_response_oversized is not None and provider_resolution.is_real_model:
                blocked_error_code = "model_output_too_large"
                blocked_error_message = (
                    f"{raw_response_oversized['message']} Schema extraction failed: "
                    f"{schema_result.error_message or blocked_error_code}"
                )
                blocked_message = "Model provider raw response is too large and no safe structured result was extracted."
                send_oversized_alert = True
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_result=provider_output,
                provider_metadata=provider_resolution,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message=blocked_message,
                error_code=blocked_error_code,
                error_message=blocked_error_message,
                artifact_payloads=artifact_payloads,
                send_oversized_alert=send_oversized_alert,
                running_run_row=running_run_row,
            )

        normalized = schema_result.normalized_output
        if normalized.get("review_decision") == ReviewDecision.BLOCKED.value:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_result=provider_output,
                provider_metadata=provider_resolution,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="Provider returned blocked review decision.",
                error_code="provider_review_blocked",
                artifact_payloads=artifact_payloads,
                running_run_row=running_run_row,
            )

        human_review_required = bool(normalized["human_review_required"])
        result_id = build_model_analysis_result_id(request.material_pack_id)
        result = build_success_result(
            request,
            model_analysis_run_id=run_id,
            model_analysis_result_id=result_id,
            review_version_key=review_version_key,
            material_pack=material_pack,
            normalized=normalized,
            prompt=prompt,
            provider_result=provider_output,
            details={
                "provider": provider_resolution.provider_name,
                "model_key": provider_resolution.model_key,
                "model_name": provider_resolution.model_name,
                "model_role": provider_resolution.model_role,
                "analysis_mode": provider_resolution.analysis_mode,
                "prompt_template_version": provider_resolution.prompt_template_version,
                "review_schema_version": provider_resolution.review_schema_version,
                "mock_provider_only": provider_resolution.provider_name == MODEL_REVIEW_PROVIDER_MOCK,
                "no_real_model_call": not provider_resolution.is_real_model,
                "not_final_trading_advice": True,
                "profile_hash": provider_resolution.profile_hash,
                "profile_version": provider_resolution.profile_version,
                "raw_request_hash": provider_resolution.raw_request_hash,
                "raw_request_storage_ref": provider_resolution.raw_request_storage_ref,
                "raw_response_hash": getattr(provider_output, "raw_response_hash", None),
                "raw_response_storage_ref": provider_resolution.raw_response_storage_ref,
                "raw_response_oversized": raw_response_oversized is not None,
                "input_token_count": provider_resolution.input_token_count,
                "output_token_count": provider_resolution.output_token_count,
                "total_token_count": provider_resolution.total_token_count,
                "estimated_cost": provider_resolution.estimated_cost,
                "cost_currency": provider_resolution.cost_currency,
            },
        )
        if request.dry_run:
            dry_result = replace(result, details={**dict(result.details), "dry_run": True})
            if raw_response_oversized is not None and provider_resolution.is_real_model:
                return replace(
                    dry_result,
                    hermes_status=ModelAnalysisHermesStatus.SKIPPED_DRY_RUN,
                    details={
                        **dict(dry_result.details),
                        "oversized_hermes_skipped_reason": "dry_run",
                    },
                )
            return dry_result

        run_payload = build_run_payload(
            request=request,
            material_pack=material_pack,
            prompt=prompt,
            provider_metadata=provider_resolution,
            provider_result=provider_output,
            model_analysis_run_id=run_id,
            review_version_key=review_version_key,
            trace_id=trace_id,
            status=ModelAnalysisStatus.SUCCESS,
            human_review_required=human_review_required,
            error_code=None,
            error_message=None,
            settings=self._settings,
        )
        result_payload = build_result_payload(
            result_id=result_id,
            model_analysis_run_id=run_id,
            review_version_key=review_version_key,
            material_pack=material_pack,
            normalized=normalized,
        )
        persistence_phase = "run"
        try:
            if running_run_row is not None:
                run_row = self._repository.update_model_analysis_run(
                    db_session,
                    running_run_row,
                    payload=run_payload,
                )
            else:
                run_row = self._repository.create_model_analysis_run(db_session, payload=run_payload)
            persistence_phase = "result"
            result_row = self._repository.create_model_analysis_result(db_session, payload=result_payload)
            persistence_phase = "artifact"
            for artifact_payload in artifact_payloads:
                self._repository.create_model_provider_call_artifact(db_session, payload=artifact_payload)
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001 - persistence errors become structured results.
            _rollback_if_possible(db_session)
            if persistence_phase == "artifact" and provider_resolution.is_real_model:
                return self._return_artifact_write_failed(
                    db_session=db_session,
                    request=request,
                    material_pack=material_pack,
                    prompt=prompt,
                    provider_result=provider_output,
                    provider_metadata=provider_resolution,
                    model_analysis_run_id=run_id,
                    review_version_key=review_version_key,
                    trace_id=trace_id,
                    error_message=f"provider artifact metadata persistence failed: {exc}",
                    running_run_row=running_run_row,
                )
            skipped_result = self._build_skipped_result_after_unique_conflict(
                db_session,
                request=request,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                exc=exc,
            )
            if skipped_result is not None:
                return skipped_result
            if running_run_row is not None:
                return self._return_or_persist_failed(
                    db_session=db_session,
                    request=request,
                    material_pack=material_pack,
                    prompt=prompt,
                    provider_result=provider_output,
                    provider_metadata=provider_resolution,
                    model_analysis_run_id=run_id,
                    review_version_key=review_version_key,
                    trace_id=trace_id,
                    message="Model analysis persistence failed.",
                    error_code="model_analysis_persistence_failed",
                    error_message=str(exc),
                    running_run_row=running_run_row,
                )
            return build_failed_result(
                request,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="Model analysis persistence failed.",
                error_message=str(exc),
            )

        result = replace(result, model_analysis_result_id=getattr(result_row, "model_analysis_result_id", result_id))
        if raw_response_oversized is not None and provider_resolution.is_real_model:
            return self._record_oversized_hermes_and_return(db_session, run_row=run_row, result=result)
        return self._record_hermes_and_return(db_session, run_row=run_row, result=result)

    def _call_provider(
        self,
        *,
        db_session: Any,
        request: ModelAnalysisRequest,
        prompt: PromptBuildResult,
        provider_resolution: ProviderResolution,
        provider_request: ProviderRequest | None,
        model_analysis_run_id: str,
        review_version_key: str,
        material_pack: Any | None,
        trace_id: str,
        running_run_row: Any | None = None,
    ) -> ModelProviderResult | ModelAnalysisServiceResult:
        if provider_resolution.is_real_model:
            if provider_request is None:
                return build_blocked_result(
                    request,
                    model_analysis_run_id=model_analysis_run_id,
                    review_version_key=None,
                    trace_id=trace_id,
                    message="real model profile/provider config is missing.",
                    error_code="real_model_config_missing",
                    model_key=provider_resolution.model_key,
                    model_role=provider_resolution.model_role,
                    analysis_mode=provider_resolution.analysis_mode,
                )
            try:
                return provider_resolution.provider.call_review_model(provider_request)
            except ProviderCallError as exc:
                provider_result = getattr(exc, "provider_response", None)
                if provider_result is not None:
                    provider_resolution = self._enrich_provider_resolution_after_call(
                        provider_resolution=provider_resolution,
                        provider_output=provider_result,
                        prompt=prompt,
                    )
                    raw_response_oversized = build_limit_error(
                        char_count=int(getattr(provider_result, "raw_response_char_count", 0) or 0),
                        byte_count=int(getattr(provider_result, "raw_response_byte_count", 0) or 0),
                        max_chars=self._settings.model_review_max_output_chars,
                        max_bytes=self._settings.model_review_max_output_bytes,
                        prefix="raw_response",
                    )
                    artifact_payloads: list[ModelProviderCallArtifactPersistencePayload] = []
                    if raw_response_oversized is not None:
                        artifact = self._write_response_artifact_if_allowed(
                            db_session=db_session,
                            request=request,
                            material_pack=material_pack,
                            prompt=prompt,
                            provider_output=provider_result,
                            provider_resolution=provider_resolution,
                            model_analysis_run_id=model_analysis_run_id,
                            review_version_key=review_version_key,
                            trace_id=trace_id,
                            capture_reason="oversized_response",
                            running_run_row=running_run_row,
                        )
                        if isinstance(artifact, ModelAnalysisServiceResult):
                            return artifact
                        if artifact is not None:
                            provider_resolution.raw_response_storage_ref = artifact.storage_ref
                            artifact_payloads.append(
                                build_artifact_payload(
                                    artifact,
                                    model_analysis_run_id=model_analysis_run_id,
                                    provider_resolution=provider_resolution,
                                )
                            )
                        return self._return_or_persist_blocked(
                            db_session,
                            request=request,
                            material_pack=material_pack,
                            prompt=prompt,
                            provider_result=provider_result,
                            provider_metadata=provider_resolution,
                            model_analysis_run_id=model_analysis_run_id,
                            review_version_key=review_version_key,
                            trace_id=trace_id,
                            message=(
                                "Model provider raw response is too large and no safe structured result "
                                "was extracted."
                            ),
                            error_code="model_output_too_large",
                            error_message=f"{raw_response_oversized['message']} Provider parse failed: {exc}",
                            artifact_payloads=artifact_payloads,
                            send_oversized_alert=True,
                            running_run_row=running_run_row,
                        )
                return self._return_or_persist_failed(
                    db_session=db_session,
                    request=request,
                    material_pack=material_pack,
                    prompt=prompt,
                    provider_result=provider_result,
                    provider_metadata=provider_resolution,
                    model_analysis_run_id=model_analysis_run_id,
                    review_version_key=review_version_key,
                    trace_id=trace_id,
                    message="Real model provider call failed.",
                    error_code="provider_call_failed",
                    error_message=str(exc),
                    running_run_row=running_run_row,
                )
        return provider_resolution.provider.review_material(prompt)

    def _build_real_provider_request_if_needed(
        self,
        *,
        provider_resolution: ProviderResolution,
        prompt: PromptBuildResult,
        material_pack_id: str,
        model_analysis_run_id: str,
        trace_id: str,
    ) -> ProviderRequest | None:
        """Build a real-provider request object without making the external call."""

        if not provider_resolution.is_real_model:
            return None
        if provider_resolution.profile is None or provider_resolution.provider_config is None:
            return None
        return ProviderRequest(
            prompt=prompt,
            profile=provider_resolution.profile,
            provider_config=provider_resolution.provider_config,
            api_key=provider_resolution.api_key,
            trace_id=trace_id,
            material_pack_id=material_pack_id,
            model_analysis_run_id=model_analysis_run_id,
        )

    def _persist_running_real_model_run(
        self,
        db_session: Any,
        *,
        request: ModelAnalysisRequest,
        material_pack: Any,
        prompt: PromptBuildResult,
        provider_metadata: ProviderResolution,
        model_analysis_run_id: str,
        review_version_key: str,
        trace_id: str,
    ) -> Any | ModelAnalysisServiceResult:
        """Insert the `running` audit row before the high-cost real model call."""

        payload = build_run_payload(
            request=request,
            material_pack=material_pack,
            prompt=prompt,
            provider_metadata=provider_metadata,
            provider_result=None,
            model_analysis_run_id=model_analysis_run_id,
            review_version_key=review_version_key,
            trace_id=trace_id,
            status=ModelAnalysisStatus.RUNNING,
            human_review_required=False,
            error_code=None,
            error_message=None,
            settings=self._settings,
        )
        try:
            run_row = self._repository.create_model_analysis_run(db_session, payload=payload)
            _commit_if_possible(db_session)
            return run_row
        except Exception as exc:  # noqa: BLE001 - do not call a real model without an attempt row.
            _rollback_if_possible(db_session)
            return build_failed_result(
                request,
                model_analysis_run_id=model_analysis_run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                aggregation_run_id=optional_text(getattr(material_pack, "aggregation_run_id", None)),
                strategy_signal_run_id=optional_text(getattr(material_pack, "strategy_signal_run_id", None)),
                message="Real model running audit row persistence failed.",
                error_code="model_analysis_running_run_persistence_failed",
                error_message=str(exc),
            )

    def _write_request_artifact_or_return_failure(
        self,
        *,
        db_session: Any,
        request: ModelAnalysisRequest,
        material_pack: Any,
        prompt: PromptBuildResult,
        provider_request: ProviderRequest | None,
        provider_resolution: ProviderResolution,
        model_analysis_run_id: str,
        review_version_key: str,
        trace_id: str,
        running_run_row: Any | None,
    ) -> ArtifactWriteResult | None | ModelAnalysisServiceResult:
        """Capture the raw provider request as an isolated artifact when asked."""

        raw_request_text = self._build_raw_request_artifact_text(
            provider_request=provider_request,
            provider_resolution=provider_resolution,
            prompt=prompt,
            material_pack_id=request.material_pack_id,
            model_analysis_run_id=model_analysis_run_id,
            trace_id=trace_id,
        )
        try:
            artifact = write_model_provider_artifact(
                settings=self._settings,
                artifact_type="raw_request",
                content=raw_request_text,
                capture_reason=f"capture_raw_request:{model_analysis_run_id}:{provider_resolution.model_key}",
            )
        except Exception as exc:  # noqa: BLE001 - artifact failure must be visible and audited.
            provider_resolution.raw_request_hash = sha256_text(raw_request_text)
            return self._return_artifact_write_failed(
                db_session=db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_result=None,
                provider_metadata=provider_resolution,
                model_analysis_run_id=model_analysis_run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                error_message=f"raw request artifact write failed: {exc}",
                running_run_row=running_run_row,
            )
        provider_resolution.raw_request_hash = artifact.sha256_hash
        provider_resolution.raw_request_storage_ref = artifact.storage_ref
        if running_run_row is None or request.dry_run or not request.confirm_write or not self._settings.model_review_enabled:
            return artifact
        artifact_payload = build_artifact_payload(
            artifact,
            model_analysis_run_id=model_analysis_run_id,
            provider_resolution=provider_resolution,
        )
        payload = build_run_payload(
            request=request,
            material_pack=material_pack,
            prompt=prompt,
            provider_metadata=provider_resolution,
            provider_result=None,
            model_analysis_run_id=model_analysis_run_id,
            review_version_key=review_version_key,
            trace_id=trace_id,
            status=ModelAnalysisStatus.RUNNING,
            human_review_required=False,
            error_code=None,
            error_message=None,
            settings=self._settings,
        )
        try:
            self._repository.update_model_analysis_run(db_session, running_run_row, payload=payload)
            self._repository.create_model_provider_call_artifact(db_session, payload=artifact_payload)
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
            return self._return_artifact_write_failed(
                db_session=db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_result=None,
                provider_metadata=provider_resolution,
                model_analysis_run_id=model_analysis_run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                error_message=f"raw request artifact metadata persistence failed: {exc}",
                running_run_row=running_run_row,
            )
        return artifact

    def _build_raw_request_artifact_text(
        self,
        *,
        provider_request: ProviderRequest | None,
        provider_resolution: ProviderResolution,
        prompt: PromptBuildResult,
        material_pack_id: str,
        model_analysis_run_id: str,
        trace_id: str,
    ) -> str:
        """Render a secret-free raw request artifact for real-provider audits."""

        payload: Mapping[str, Any]
        build_payload = getattr(provider_resolution.provider, "build_request_payload", None)
        if provider_request is not None and callable(build_payload):
            payload = build_payload(provider_request)
        else:
            payload = {
                "model": provider_resolution.model_name,
                "request_params": dict(provider_resolution.request_params_summary_json),
                "prompt": prompt.prompt_text,
            }
        safe_payload = _redact_sensitive_mapping(payload)
        artifact = {
            "artifact_kind": "raw_request",
            "provider": provider_resolution.provider_name,
            "model_key": provider_resolution.model_key,
            "model_name": provider_resolution.model_name,
            "model_version": provider_resolution.model_version,
            "profile_hash": provider_resolution.profile_hash,
            "material_pack_id": material_pack_id,
            "model_analysis_run_id": model_analysis_run_id,
            "trace_id": trace_id,
            "capture_reason": "capture_raw_request",
            "payload": safe_payload,
        }
        return json.dumps(artifact, ensure_ascii=False, sort_keys=True, default=str)

    def _enrich_provider_resolution_before_call(
        self,
        *,
        provider_resolution: ProviderResolution,
        prompt: PromptBuildResult,
    ) -> ProviderResolution:
        """Record deterministic request metadata before any real provider call."""

        if provider_resolution.profile is not None:
            provider_resolution.request_params_summary_json = dict(provider_resolution.profile.request_params)
            provider_resolution.capabilities_json = dict(provider_resolution.profile.capabilities)
            request_basis = json.dumps(
                {
                    "profile_hash": provider_resolution.profile_hash,
                    "request_params": provider_resolution.profile.request_params,
                    "prompt_hash": sha256_text(prompt.prompt_text),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            provider_resolution.request_payload_hash = sha256_text(request_basis)
            provider_resolution.raw_request_hash = provider_resolution.request_payload_hash
        return provider_resolution

    def _enrich_provider_resolution_after_call(
        self,
        *,
        provider_resolution: ProviderResolution,
        provider_output: ModelProviderResult,
        prompt: PromptBuildResult,
    ) -> ProviderResolution:
        if provider_resolution.profile is not None:
            if not provider_resolution.request_payload_hash:
                self._enrich_provider_resolution_before_call(
                    provider_resolution=provider_resolution,
                    prompt=prompt,
                )
            cost = estimate_provider_call_cost(
                profile=provider_resolution.profile,
                usage=getattr(provider_output, "usage", {}) or {},
            )
            provider_resolution.provider_usage_json = cost.provider_usage_json
            provider_resolution.input_token_count = cost.input_token_count
            provider_resolution.output_token_count = cost.output_token_count
            provider_resolution.total_token_count = cost.total_token_count
            provider_resolution.estimated_cost = cost.estimated_cost
            provider_resolution.cost_currency = cost.cost_currency
        return provider_resolution

    def _write_response_artifact_if_allowed(
        self,
        *,
        db_session: Any,
        request: ModelAnalysisRequest,
        material_pack: Any,
        prompt: PromptBuildResult,
        provider_output: ModelProviderResult,
        provider_resolution: ProviderResolution,
        model_analysis_run_id: str,
        review_version_key: str,
        trace_id: str,
        capture_reason: str,
        running_run_row: Any | None = None,
    ) -> ArtifactWriteResult | ModelAnalysisServiceResult | None:
        raw_response_text = str(getattr(provider_output, "raw_response_text", "") or "")
        if not raw_response_text:
            return None
        try:
            return write_model_provider_artifact(
                settings=self._settings,
                artifact_type="raw_response" if capture_reason != "oversized_response" else "oversized_response",
                content=raw_response_text,
                capture_reason=f"{capture_reason}:{model_analysis_run_id}:{provider_resolution.model_key}",
            )
        except Exception as exc:  # noqa: BLE001 - raw response isolation failure must be audited.
            return self._return_artifact_write_failed(
                db_session=db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_result=provider_output,
                provider_metadata=provider_resolution,
                model_analysis_run_id=model_analysis_run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                error_message=f"raw response artifact write failed: {exc}",
                running_run_row=running_run_row,
            )

    def _return_or_persist_blocked(
        self,
        db_session: Any,
        *,
        request: ModelAnalysisRequest,
        material_pack: Any | None,
        prompt: PromptBuildResult | None,
        provider_result: ModelProviderResult | None,
        provider_metadata: ProviderResolution,
        model_analysis_run_id: str,
        review_version_key: str,
        trace_id: str,
        message: str,
        error_code: str,
        error_message: str | None = None,
        artifact_payloads: list[ModelProviderCallArtifactPersistencePayload] | None = None,
        send_oversized_alert: bool = False,
        running_run_row: Any | None = None,
    ) -> ModelAnalysisServiceResult:
        result = build_blocked_result(
            request,
            model_analysis_run_id=model_analysis_run_id,
            review_version_key=review_version_key,
            trace_id=trace_id,
            aggregation_run_id=optional_text(getattr(material_pack, "aggregation_run_id", None)),
            strategy_signal_run_id=optional_text(getattr(material_pack, "strategy_signal_run_id", None)),
            input_char_count=prompt.input_char_count if prompt else 0,
            input_byte_count=prompt.input_byte_count if prompt else 0,
            output_char_count=provider_result.output_char_count if provider_result else 0,
            output_byte_count=provider_result.output_byte_count if provider_result else 0,
            message=message,
            error_code=error_code,
            error_message=error_message,
            model_key=provider_metadata.model_key,
            model_role=provider_metadata.model_role,
            analysis_mode=provider_metadata.analysis_mode,
        )
        result = replace(
            result,
            raw_response_char_count=int(getattr(provider_result, "raw_response_char_count", 0) or 0),
            raw_response_byte_count=int(getattr(provider_result, "raw_response_byte_count", 0) or 0),
            details={
                "provider": provider_metadata.provider_name,
                "model_key": provider_metadata.model_key,
                "model_name": provider_metadata.model_name,
                "profile_hash": provider_metadata.profile_hash,
                "raw_response_storage_ref": provider_metadata.raw_response_storage_ref,
                "raw_response_hash": getattr(provider_result, "raw_response_hash", None),
            },
        )
        if request.dry_run or not request.confirm_write or not self._settings.model_review_enabled:
            return result
        payload = build_run_payload(
            request=request,
            material_pack=material_pack,
            prompt=prompt,
            provider_metadata=provider_metadata,
            provider_result=provider_result,
            model_analysis_run_id=model_analysis_run_id,
            review_version_key=review_version_key,
            trace_id=trace_id,
            status=ModelAnalysisStatus.BLOCKED,
            human_review_required=False,
            error_code=error_code,
            error_message=error_message or message,
            settings=self._settings,
        )
        persistence_phase = "run"
        try:
            if running_run_row is not None:
                run_row = self._repository.update_model_analysis_run(db_session, running_run_row, payload=payload)
            else:
                run_row = self._repository.create_model_analysis_run(db_session, payload=payload)
            persistence_phase = "artifact"
            for artifact_payload in artifact_payloads or []:
                self._repository.create_model_provider_call_artifact(db_session, payload=artifact_payload)
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
            if persistence_phase == "artifact" and provider_metadata.is_real_model:
                return self._return_artifact_write_failed(
                    db_session=db_session,
                    request=request,
                    material_pack=material_pack,
                    prompt=prompt,
                    provider_result=provider_result,
                    provider_metadata=provider_metadata,
                    model_analysis_run_id=model_analysis_run_id,
                    review_version_key=review_version_key,
                    trace_id=trace_id,
                    error_message=f"provider artifact metadata persistence failed: {exc}",
                    running_run_row=running_run_row,
                )
            return build_failed_result(
                request,
                model_analysis_run_id=model_analysis_run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                aggregation_run_id=result.aggregation_run_id,
                strategy_signal_run_id=result.strategy_signal_run_id,
                message="Blocked model analysis audit persistence failed.",
                error_message=str(exc),
            )
        if send_oversized_alert:
            return self._record_oversized_hermes_and_return(db_session, run_row=run_row, result=result)
        return self._record_hermes_and_return(db_session, run_row=run_row, result=result)

    def _return_or_persist_failed(
        self,
        *,
        db_session: Any,
        request: ModelAnalysisRequest,
        material_pack: Any | None,
        prompt: PromptBuildResult | None,
        provider_result: ModelProviderResult | None,
        provider_metadata: ProviderResolution,
        model_analysis_run_id: str,
        review_version_key: str | None,
        trace_id: str,
        message: str,
        error_code: str,
        error_message: str,
        running_run_row: Any | None = None,
    ) -> ModelAnalysisServiceResult:
        result = build_failed_result(
            request,
            model_analysis_run_id=model_analysis_run_id,
            review_version_key=review_version_key,
            trace_id=trace_id,
            aggregation_run_id=optional_text(getattr(material_pack, "aggregation_run_id", None)),
            strategy_signal_run_id=optional_text(getattr(material_pack, "strategy_signal_run_id", None)),
            message=message,
            error_code=error_code,
            error_message=error_message,
        )
        result = replace(
            result,
            raw_response_char_count=int(getattr(provider_result, "raw_response_char_count", 0) or 0),
            raw_response_byte_count=int(getattr(provider_result, "raw_response_byte_count", 0) or 0),
            details={
                "provider": provider_metadata.provider_name,
                "model_key": provider_metadata.model_key,
                "model_name": provider_metadata.model_name,
                "profile_hash": provider_metadata.profile_hash,
                "raw_response_hash": getattr(provider_result, "raw_response_hash", None),
                "raw_response_storage_ref": provider_metadata.raw_response_storage_ref,
            },
        )
        if request.dry_run:
            return replace(result, hermes_status=ModelAnalysisHermesStatus.SKIPPED_DRY_RUN)
        if not request.confirm_write or not self._settings.model_review_enabled:
            return result
        payload = build_run_payload(
            request=request,
            material_pack=material_pack,
            prompt=prompt,
            provider_metadata=provider_metadata,
            provider_result=provider_result,
            model_analysis_run_id=model_analysis_run_id,
            review_version_key=review_version_key or "",
            trace_id=trace_id,
            status=ModelAnalysisStatus.FAILED,
            human_review_required=False,
            error_code=error_code,
            error_message=error_message,
            settings=self._settings,
        )
        try:
            if running_run_row is not None:
                run_row = self._repository.update_model_analysis_run(db_session, running_run_row, payload=payload)
            else:
                run_row = self._repository.create_model_analysis_run(db_session, payload=payload)
            _commit_if_possible(db_session)
        except Exception:
            _rollback_if_possible(db_session)
            return result
        if provider_metadata.is_real_model and error_code == "provider_call_failed":
            return self._record_provider_failed_hermes_and_return(db_session, run_row=run_row, result=result)
        return result

    def _return_artifact_write_failed(
        self,
        *,
        db_session: Any,
        request: ModelAnalysisRequest,
        material_pack: Any | None,
        prompt: PromptBuildResult | None,
        provider_result: ModelProviderResult | None,
        provider_metadata: ProviderResolution,
        model_analysis_run_id: str,
        review_version_key: str,
        trace_id: str,
        error_message: str,
        running_run_row: Any | None = None,
    ) -> ModelAnalysisServiceResult:
        """Fail the attempt when raw request/response artifact isolation fails."""

        result = build_failed_result(
            request,
            model_analysis_run_id=model_analysis_run_id,
            review_version_key=review_version_key,
            trace_id=trace_id,
            aggregation_run_id=optional_text(getattr(material_pack, "aggregation_run_id", None)),
            strategy_signal_run_id=optional_text(getattr(material_pack, "strategy_signal_run_id", None)),
            message="Model provider artifact write failed.",
            error_code="artifact_write_failed",
            error_message=error_message,
        )
        result = replace(
            result,
            model_key=provider_metadata.model_key,
            model_role=provider_metadata.model_role,
            analysis_mode=provider_metadata.analysis_mode,
            raw_response_char_count=int(getattr(provider_result, "raw_response_char_count", 0) or 0),
            raw_response_byte_count=int(getattr(provider_result, "raw_response_byte_count", 0) or 0),
            details={
                "provider": provider_metadata.provider_name,
                "model_key": provider_metadata.model_key,
                "model_name": provider_metadata.model_name,
                "profile_hash": provider_metadata.profile_hash,
                "raw_response_hash": getattr(provider_result, "raw_response_hash", None),
                "raw_response_storage_ref": provider_metadata.raw_response_storage_ref,
                "raw_request_hash": provider_metadata.raw_request_hash,
                "raw_request_storage_ref": provider_metadata.raw_request_storage_ref,
                "formal_result_generated": False,
            },
        )
        if request.dry_run or not request.confirm_write or not self._settings.model_review_enabled:
            hermes_status = (
                ModelAnalysisHermesStatus.SKIPPED_DRY_RUN
                if request.dry_run
                else ModelAnalysisHermesStatus.NOT_REQUIRED
            )
            return replace(
                result,
                hermes_status=hermes_status,
                details={
                    **dict(result.details),
                    "artifact_failed_hermes_skipped_reason": "dry_run" if request.dry_run else "not_confirm_write",
                },
            )
        payload = build_run_payload(
            request=request,
            material_pack=material_pack,
            prompt=prompt,
            provider_metadata=provider_metadata,
            provider_result=provider_result,
            model_analysis_run_id=model_analysis_run_id,
            review_version_key=review_version_key,
            trace_id=trace_id,
            status=ModelAnalysisStatus.FAILED,
            human_review_required=False,
            error_code="artifact_write_failed",
            error_message=error_message,
            settings=self._settings,
        )
        try:
            if running_run_row is not None:
                run_row = self._repository.update_model_analysis_run(db_session, running_run_row, payload=payload)
            else:
                run_row = self._repository.create_model_analysis_run(db_session, payload=payload)
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
            return replace(
                result,
                error_message=f"{error_message}; artifact failure audit persistence failed: {exc}",
            )
        return self._record_artifact_failed_hermes_and_return(db_session, run_row=run_row, result=result)

    def _build_skipped_result_after_unique_conflict(
        self,
        db_session: Any,
        *,
        request: ModelAnalysisRequest,
        model_analysis_run_id: str,
        review_version_key: str,
        trace_id: str,
        exc: Exception,
    ) -> ModelAnalysisServiceResult | None:
        """Convert concurrent final-result unique conflicts into skipped."""

        if not _is_unique_constraint_error(exc):
            return None
        try:
            existing = self._repository.get_existing_result_by_review_version_key(
                db_session,
                review_version_key=review_version_key,
            )
        except Exception:  # noqa: BLE001 - fall back to original persistence failure.
            return None
        if existing is None:
            return None
        return build_skipped_result_from_existing(
            request,
            existing_result=existing,
            model_analysis_run_id=model_analysis_run_id,
            trace_id=trace_id,
            details={"skip_reason": "already_exists", "unique_conflict_recovered": True},
        )

    def _record_hermes_and_return(
        self,
        db_session: Any,
        *,
        run_row: Any,
        result: ModelAnalysisServiceResult,
    ) -> ModelAnalysisServiceResult:
        hermes_status, hermes_message, hermes_error, hermes_sent_at_utc = self._send_or_skip_hermes(result=result)
        try:
            self._repository.record_hermes_result(
                db_session,
                run_row,
                hermes_status=hermes_status.value,
                hermes_message=hermes_message,
                hermes_error=hermes_error,
                hermes_sent_at_utc=hermes_sent_at_utc,
            )
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001 - notification status must not rewrite review result.
            _rollback_if_possible(db_session)
            return replace(
                result,
                hermes_status=ModelAnalysisHermesStatus.FAILED,
                error_message=result.error_message or f"Hermes status persistence failed: {exc}",
            )
        return replace(result, hermes_status=hermes_status)

    def _record_oversized_hermes_and_return(
        self,
        db_session: Any,
        *,
        run_row: Any,
        result: ModelAnalysisServiceResult,
    ) -> ModelAnalysisServiceResult:
        hermes_status, hermes_message, hermes_error, hermes_sent_at_utc = self._send_or_skip_oversized_hermes(
            result=result
        )
        try:
            self._repository.record_hermes_result(
                db_session,
                run_row,
                hermes_status=hermes_status.value,
                hermes_message=hermes_message,
                hermes_error=hermes_error,
                hermes_sent_at_utc=hermes_sent_at_utc,
            )
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
            return replace(
                result,
                hermes_status=ModelAnalysisHermesStatus.FAILED,
                error_message=result.error_message or f"Oversized response Hermes status persistence failed: {exc}",
            )
        return replace(result, hermes_status=hermes_status)

    def _record_artifact_failed_hermes_and_return(
        self,
        db_session: Any,
        *,
        run_row: Any,
        result: ModelAnalysisServiceResult,
    ) -> ModelAnalysisServiceResult:
        hermes_status, hermes_message, hermes_error, hermes_sent_at_utc = self._send_or_skip_artifact_failed_hermes(
            result=result
        )
        try:
            self._repository.record_hermes_result(
                db_session,
                run_row,
                hermes_status=hermes_status.value,
                hermes_message=hermes_message,
                hermes_error=hermes_error,
                hermes_sent_at_utc=hermes_sent_at_utc,
            )
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
            return replace(
                result,
                hermes_status=ModelAnalysisHermesStatus.FAILED,
                error_message=result.error_message or f"Artifact failure Hermes status persistence failed: {exc}",
            )
        return replace(result, hermes_status=hermes_status)

    def _record_provider_failed_hermes_and_return(
        self,
        db_session: Any,
        *,
        run_row: Any,
        result: ModelAnalysisServiceResult,
    ) -> ModelAnalysisServiceResult:
        hermes_status, hermes_message, hermes_error, hermes_sent_at_utc = self._send_or_skip_provider_failed_hermes(
            result=result
        )
        try:
            self._repository.record_hermes_result(
                db_session,
                run_row,
                hermes_status=hermes_status.value,
                hermes_message=hermes_message,
                hermes_error=hermes_error,
                hermes_sent_at_utc=hermes_sent_at_utc,
            )
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
            return replace(
                result,
                hermes_status=ModelAnalysisHermesStatus.FAILED,
                error_message=result.error_message or f"Provider failure Hermes status persistence failed: {exc}",
            )
        return replace(result, hermes_status=hermes_status)

    def _send_or_skip_provider_failed_hermes(
        self,
        *,
        result: ModelAnalysisServiceResult,
    ) -> tuple[ModelAnalysisHermesStatus, str | None, str | None, datetime | None]:
        if not self._settings.model_review_hermes_enabled:
            return ModelAnalysisHermesStatus.DISABLED, None, None, None
        visible_body = build_model_analysis_provider_call_failed_visible_body(result)
        alert_event = AlertEvent(
            alert_type=AlertType.MODEL_ANALYSIS,
            severity=AlertSeverity.WARNING,
            title="BTC 大模型请求失败",
            summary="BTC 大模型请求失败，未生成正式审查结果；这不是最终交易建议，未自动交易。",
            details={
                WECHAT_VISIBLE_BODY_DETAIL_KEY: visible_body,
                "model_analysis_run_id": result.model_analysis_run_id,
                "material_pack_id": result.material_pack_id,
                "provider": result.details.get("provider", "") if result.details else "",
                "model_key": result.model_key or "",
                "model_name": result.details.get("model_name", "") if result.details else "",
                "error_code": result.error_code or "provider_call_failed",
                "not_final_trading_advice": True,
                "no_auto_trading": True,
            },
            source=MODEL_ANALYSIS_EVENT_SOURCE,
            trace_id=result.trace_id,
        )
        try:
            send_result = self._alert_sender(alert_event, settings=self._settings, send_real_alert=True)
        except Exception as exc:  # noqa: BLE001
            return ModelAnalysisHermesStatus.FAILED, visible_body, str(exc), None
        if getattr(send_result, "status", None) == AlertSendStatus.SUBMITTED_TO_HERMES:
            return (
                ModelAnalysisHermesStatus.SENT,
                visible_body,
                None,
                getattr(send_result, "submitted_at_utc", None) or now_utc(),
            )
        return (
            ModelAnalysisHermesStatus.FAILED,
            visible_body,
            getattr(send_result, "error_message", "") or getattr(send_result, "message", "") or "Hermes not sent",
            None,
        )

    def _send_or_skip_artifact_failed_hermes(
        self,
        *,
        result: ModelAnalysisServiceResult,
    ) -> tuple[ModelAnalysisHermesStatus, str | None, str | None, datetime | None]:
        if not self._settings.model_review_hermes_on_oversized_output:
            return ModelAnalysisHermesStatus.DISABLED, None, None, None
        visible_body = build_model_analysis_artifact_write_failed_visible_body(result)
        alert_event = AlertEvent(
            alert_type=AlertType.MODEL_ANALYSIS,
            severity=AlertSeverity.WARNING,
            title="BTC 大模型审查 artifact 写入失败",
            summary="BTC 大模型审查 artifact 写入失败，未生成正式审查结果；这不是最终交易建议，未自动交易。",
            details={
                WECHAT_VISIBLE_BODY_DETAIL_KEY: visible_body,
                "model_analysis_run_id": result.model_analysis_run_id,
                "material_pack_id": result.material_pack_id,
                "error_code": result.error_code or "artifact_write_failed",
                "not_final_trading_advice": True,
                "no_auto_trading": True,
            },
            source=MODEL_ANALYSIS_EVENT_SOURCE,
            trace_id=result.trace_id,
        )
        try:
            send_result = self._alert_sender(alert_event, settings=self._settings, send_real_alert=True)
        except Exception as exc:  # noqa: BLE001
            return ModelAnalysisHermesStatus.FAILED, visible_body, str(exc), None
        if getattr(send_result, "status", None) == AlertSendStatus.SUBMITTED_TO_HERMES:
            return (
                ModelAnalysisHermesStatus.SENT,
                visible_body,
                None,
                getattr(send_result, "submitted_at_utc", None) or now_utc(),
            )
        return (
            ModelAnalysisHermesStatus.FAILED,
            visible_body,
            getattr(send_result, "error_message", "") or getattr(send_result, "message", "") or "Hermes not sent",
            None,
        )

    def _send_or_skip_oversized_hermes(
        self,
        *,
        result: ModelAnalysisServiceResult,
    ) -> tuple[ModelAnalysisHermesStatus, str | None, str | None, datetime | None]:
        if not self._settings.model_review_hermes_on_oversized_output:
            return ModelAnalysisHermesStatus.DISABLED, None, None, None
        visible_body = build_model_analysis_oversized_response_visible_body(result)
        alert_event = AlertEvent(
            alert_type=AlertType.MODEL_ANALYSIS,
            severity=AlertSeverity.WARNING,
            title="BTC 大模型审查返回过长",
            summary="BTC 大模型审查返回过长，已按安全规则处理；这不是最终交易建议，未自动交易。",
            details={
                WECHAT_VISIBLE_BODY_DETAIL_KEY: visible_body,
                "model_analysis_run_id": result.model_analysis_run_id,
                "material_pack_id": result.material_pack_id,
                "raw_response_char_count": result.raw_response_char_count,
                "raw_response_byte_count": result.raw_response_byte_count,
                "not_final_trading_advice": True,
                "no_auto_trading": True,
            },
            source=MODEL_ANALYSIS_EVENT_SOURCE,
            trace_id=result.trace_id,
        )
        try:
            send_result = self._alert_sender(alert_event, settings=self._settings, send_real_alert=True)
        except Exception as exc:  # noqa: BLE001
            return ModelAnalysisHermesStatus.FAILED, visible_body, str(exc), None
        if getattr(send_result, "status", None) == AlertSendStatus.SUBMITTED_TO_HERMES:
            return (
                ModelAnalysisHermesStatus.SENT,
                visible_body,
                None,
                getattr(send_result, "submitted_at_utc", None) or now_utc(),
            )
        return (
            ModelAnalysisHermesStatus.FAILED,
            visible_body,
            getattr(send_result, "error_message", "") or getattr(send_result, "message", "") or "Hermes not sent",
            None,
        )

    def _send_or_skip_hermes(
        self,
        *,
        result: ModelAnalysisServiceResult,
    ) -> tuple[ModelAnalysisHermesStatus, str | None, str | None, datetime | None]:
        if not self._settings.model_review_hermes_enabled:
            return ModelAnalysisHermesStatus.DISABLED, None, None, None
        if result.status not in FINAL_REVIEW_RESULT_STATUSES:
            return ModelAnalysisHermesStatus.NOT_REQUIRED, None, None, None
        visible_body = build_model_analysis_visible_body(result)
        alert_event = AlertEvent(
            alert_type=AlertType.MODEL_ANALYSIS,
            severity=AlertSeverity.INFO,
            title="BTC 大模型审查候选结果",
            summary="BTC 大模型审查候选结果，不是最终交易建议。",
            details={
                WECHAT_VISIBLE_BODY_DETAIL_KEY: visible_body,
                "model_analysis_run_id": result.model_analysis_run_id,
                "model_analysis_result_id": result.model_analysis_result_id or "",
                "material_pack_id": result.material_pack_id,
                "review_decision": result.review_decision or "",
                "evidence_quality": result.evidence_quality or "",
                "risk_acceptability": result.risk_acceptability or "",
                "strategy_conflict_level": result.strategy_conflict_level or "",
                "human_review_required": result.human_review_required,
                "not_final_trading_advice": True,
                "no_auto_trading": True,
            },
            source=MODEL_ANALYSIS_EVENT_SOURCE,
            trace_id=result.trace_id,
        )
        try:
            send_result = self._alert_sender(
                alert_event,
                settings=self._settings,
                send_real_alert=True,
            )
        except Exception as exc:  # noqa: BLE001
            return ModelAnalysisHermesStatus.FAILED, visible_body, str(exc), None
        if getattr(send_result, "status", None) == AlertSendStatus.SUBMITTED_TO_HERMES:
            return (
                ModelAnalysisHermesStatus.SENT,
                visible_body,
                None,
                getattr(send_result, "submitted_at_utc", None) or now_utc(),
            )
        return (
            ModelAnalysisHermesStatus.FAILED,
            visible_body,
            getattr(send_result, "error_message", "") or getattr(send_result, "message", "") or "Hermes not sent",
            None,
        )


def run_model_analysis(
    *,
    db_session: Any,
    request: ModelAnalysisRequest,
    service: ModelAnalysisService | None = None,
) -> ModelAnalysisServiceResult:
    """Convenience app-service function used by CLI and tests."""

    active_service = service or create_default_model_analysis_service()
    return active_service.run_model_analysis(db_session, request=request)


def create_default_model_analysis_service() -> ModelAnalysisService:
    """Create the default stage-19 model analysis service."""

    return ModelAnalysisService()


def _validate_request(
    request: ModelAnalysisRequest,
    *,
    model_analysis_run_id: str,
    trace_id: str,
) -> ModelAnalysisServiceResult | None:
    problems: list[str] = []
    if not request.material_pack_id.strip():
        problems.append("material_pack_id is required")
    if request.trigger_source not in ALLOWED_MODEL_ANALYSIS_TRIGGER_SOURCES:
        problems.append("trigger_source supports only cli in stage 19")
    if request.dry_run and request.confirm_write:
        problems.append("dry_run and confirm_write cannot both be true")
    if not request.dry_run and not request.confirm_write:
        problems.append("non-dry-run model analysis requires confirm_write")
    if not problems:
        return None
    return build_invalid_request_result(
        request,
        model_analysis_run_id=model_analysis_run_id,
        trace_id=trace_id,
        error_message="; ".join(problems),
    )


def _is_unique_constraint_error(exc: Exception) -> bool:
    if IntegrityError is not None and isinstance(exc, IntegrityError):
        text = str(getattr(exc, "orig", exc)).lower()
    else:
        text = f"{exc.__class__.__name__}: {exc}".lower()
    return any(marker in text for marker in ("unique", "duplicate", "uq_", "uk_"))


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


def _rollback_if_possible(db_session: Any) -> None:
    rollback = getattr(db_session, "rollback", None)
    if callable(rollback):
        rollback()


def _redact_sensitive_mapping(value: Any) -> Any:
    """Return a JSON-safe copy with secrets and auth headers removed."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered in {"authorization", "api_key", "api-key", "secret", "token", "cookie"}:
                result[key_text] = "***REDACTED***"
                continue
            result[key_text] = _redact_sensitive_mapping(item)
        return result
    if isinstance(value, list):
        return [_redact_sensitive_mapping(item) for item in value]
    return value


def _default_alert_sender(*args: Any, **kwargs: Any) -> Any:
    from app.alerting.service import send_alert

    return send_alert(*args, **kwargs)


__all__ = [
    "ALLOWED_MODEL_ANALYSIS_TRIGGER_SOURCES",
    "ModelAnalysisService",
    "create_default_model_analysis_service",
    "run_model_analysis",
]
