"""Stage-19 model analysis review-gate service.

Call chain:
scripts/run_model_analysis.py::main
    -> app/model_analysis/service.py::run_model_analysis
    -> app/model_analysis/repository.py::get_material_pack_by_id
    -> app/model_analysis/prompt_builder.py::build_model_review_prompt
    -> app/model_analysis/providers/mock.py::MockModelReviewProvider.review_material
    -> app/model_analysis/schema_validator.py::validate_model_review_output
    -> app/model_analysis/repository.py::create_model_analysis_run
    -> app/model_analysis/repository.py::create_model_analysis_result

This file belongs to `app/model_analysis`. It consumes only stage-18
`analysis_material_pack` rows with `status=success`, runs a bounded mock review
provider, validates schema, optionally writes stage-19 tables, and optionally
sends a Chinese Hermes summary.

It does not call any real model provider, does not read market Klines directly,
does not modify formal Kline tables, does not read/write Redis, does not
implement strategy classes, does not judge long/short from Klines, does not
generate final trading advice, does not read private trading state, and does
not perform trading.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.alerting.templates import WECHAT_VISIBLE_BODY_DETAIL_KEY
from app.alerting.types import AlertEvent, AlertSendStatus, AlertSeverity, AlertType
from app.core.config import AppSettings, get_settings
from app.core.time_utils import now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_analysis.hermes_formatter import build_model_analysis_visible_body
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
    is_human_review_decision,
    optional_text,
)
from app.model_analysis.prompt_builder import build_model_review_prompt
from app.model_analysis.providers.mock import MockModelReviewProvider
from app.model_analysis.repository import (
    ModelAnalysisRepository,
    create_default_model_analysis_repository,
)
from app.model_analysis.schema_validator import validate_model_review_output
from app.model_analysis.types import (
    MODEL_ANALYSIS_EVENT_SOURCE,
    MODEL_REVIEW_MOCK_MODEL_NAME,
    MODEL_REVIEW_MOCK_MODEL_VERSION,
    MODEL_REVIEW_MODE_DEFAULT,
    MODEL_REVIEW_PROVIDER_MOCK,
    ModelAnalysisHermesStatus,
    ModelAnalysisRequest,
    ModelAnalysisServiceResult,
    ModelAnalysisStatus,
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
    """Coordinate one stage-19A model review-gate attempt.

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
        """Run a bounded mock model review for one stage-18 material pack.

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

        provider_result = self._resolve_provider()
        review_version_key = build_review_version_key(
            material_pack_id=request.material_pack_id,
            model_provider=provider_result.provider_name,
            model_name=provider_result.model_name,
            model_version=provider_result.model_version,
            prompt_template_version=self._settings.model_review_prompt_template_version,
            review_schema_version=self._settings.model_review_schema_version,
            review_mode=MODEL_REVIEW_MODE_DEFAULT,
        )
        if provider_result.blocked_message:
            return build_blocked_result(
                request,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message=provider_result.blocked_message,
                error_code="provider_not_supported",
            )
        if request.confirm_write and not self._settings.model_review_enabled:
            return build_blocked_result(
                request,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="MODEL_REVIEW_ENABLED=false blocks confirmed writes.",
                error_code="model_review_disabled",
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
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="analysis_material_pack does not exist.",
                error_code="material_pack_not_found",
            )
        if str(getattr(material_pack, "status", "")) != ModelAnalysisStatus.SUCCESS.value:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                material_pack=material_pack,
                prompt=None,
                provider_result=None,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="analysis_material_pack status is not success.",
                error_code="material_pack_status_not_success",
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
        input_limit_error = _limit_error(
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
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message=input_limit_error["message"],
                error_code=input_limit_error["error_code"],
            )

        provider_output = provider_result.provider.review_material(prompt)
        output_limit_error = _limit_error(
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
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message=output_limit_error["message"],
                error_code=output_limit_error["error_code"],
            )

        schema_result = validate_model_review_output(provider_output.output)
        if not schema_result.is_valid:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_result=provider_output,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="Model review output schema is invalid.",
                error_code=schema_result.error_code or "schema_invalid",
                error_message=schema_result.error_message,
            )

        normalized = schema_result.normalized_output
        if normalized.get("review_decision") == ReviewDecision.BLOCKED.value:
            return self._return_or_persist_blocked(
                db_session,
                request=request,
                material_pack=material_pack,
                prompt=prompt,
                provider_result=provider_output,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="Provider returned blocked review decision.",
                error_code="provider_review_blocked",
            )

        human_review_required = is_human_review_decision(str(normalized["review_decision"]))
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
                "provider": provider_result.provider_name,
                "model_name": provider_result.model_name,
                "mock_provider_only": True,
                "no_real_model_call": True,
                "not_final_trading_advice": True,
            },
        )
        if request.dry_run:
            return replace(result, details={**dict(result.details), "dry_run": True})

        run_payload = build_run_payload(
            request=request,
            material_pack=material_pack,
            prompt=prompt,
            provider_metadata=provider_result,
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
        try:
            run_row = self._repository.create_model_analysis_run(db_session, payload=run_payload)
            result_row = self._repository.create_model_analysis_result(db_session, payload=result_payload)
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001 - persistence errors become structured results.
            _rollback_if_possible(db_session)
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
            return build_failed_result(
                request,
                model_analysis_run_id=run_id,
                review_version_key=review_version_key,
                trace_id=trace_id,
                message="Model analysis persistence failed.",
                error_message=str(exc),
            )

        result = replace(result, model_analysis_result_id=getattr(result_row, "model_analysis_result_id", result_id))
        return self._record_hermes_and_return(db_session, run_row=run_row, result=result)

    def _resolve_provider(self) -> "_ProviderResolution":
        provider = self._provider
        configured_provider = self._settings.model_review_provider.strip().lower()
        if provider is None and configured_provider == MODEL_REVIEW_PROVIDER_MOCK:
            provider = MockModelReviewProvider()
        provider_name = str(getattr(provider, "provider_name", configured_provider or MODEL_REVIEW_PROVIDER_MOCK))
        model_name = str(getattr(provider, "model_name", MODEL_REVIEW_MOCK_MODEL_NAME))
        model_version = str(getattr(provider, "model_version", MODEL_REVIEW_MOCK_MODEL_VERSION))
        if configured_provider != MODEL_REVIEW_PROVIDER_MOCK:
            return _ProviderResolution(
                provider=provider,
                provider_name=provider_name,
                model_name=model_name,
                model_version=model_version,
                blocked_message="real model provider is not implemented in stage 19A",
            )
        return _ProviderResolution(
            provider=provider,
            provider_name=provider_name,
            model_name=model_name,
            model_version=model_version,
            blocked_message=None,
        )

    def _return_or_persist_blocked(
        self,
        db_session: Any,
        *,
        request: ModelAnalysisRequest,
        material_pack: Any | None,
        prompt: PromptBuildResult | None,
        provider_result: ModelProviderResult | None,
        model_analysis_run_id: str,
        review_version_key: str,
        trace_id: str,
        message: str,
        error_code: str,
        error_message: str | None = None,
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
        )
        if request.dry_run or not request.confirm_write or not self._settings.model_review_enabled:
            return result
        provider_metadata = self._resolve_provider()
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
            human_review_required=True,
            error_code=error_code,
            error_message=error_message or message,
            settings=self._settings,
        )
        try:
            run_row = self._repository.create_model_analysis_run(db_session, payload=payload)
            _commit_if_possible(db_session)
        except Exception as exc:  # noqa: BLE001
            _rollback_if_possible(db_session)
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
        return self._record_hermes_and_return(db_session, run_row=run_row, result=result)

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


class _ProviderResolution:
    def __init__(
        self,
        *,
        provider: Any,
        provider_name: str,
        model_name: str,
        model_version: str,
        blocked_message: str | None,
    ) -> None:
        self.provider = provider
        self.provider_name = provider_name
        self.model_name = model_name
        self.model_version = model_version
        self.blocked_message = blocked_message


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
        problems.append("trigger_source supports only cli in stage 19A")
    if request.dry_run and request.confirm_write:
        problems.append("dry_run and confirm_write cannot both be true")
    if not request.dry_run and not request.confirm_write:
        problems.append("non-dry-run model analysis requires confirm_write")
    if request.use_real_model:
        problems.append("real model provider is not implemented in stage 19A")
    if not problems:
        return None
    return build_invalid_request_result(
        request,
        model_analysis_run_id=model_analysis_run_id,
        trace_id=trace_id,
        error_message="; ".join(problems),
    )


def _limit_error(
    *,
    char_count: int,
    byte_count: int,
    max_chars: int,
    max_bytes: int,
    prefix: str,
) -> dict[str, str] | None:
    if char_count > max_chars:
        return {
            "message": f"Model review {prefix} exceeds {max_chars} characters.",
            "error_code": f"{prefix}_char_limit_exceeded",
        }
    if byte_count > max_bytes:
        return {
            "message": f"Model review {prefix} exceeds {max_bytes} bytes.",
            "error_code": f"{prefix}_byte_limit_exceeded",
        }
    return None


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


def _default_alert_sender(*args: Any, **kwargs: Any) -> Any:
    from app.alerting.service import send_alert

    return send_alert(*args, **kwargs)


__all__ = [
    "ALLOWED_MODEL_ANALYSIS_TRIGGER_SOURCES",
    "ModelAnalysisService",
    "create_default_model_analysis_service",
    "run_model_analysis",
]
