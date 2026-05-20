"""Repository for stage-19 model analysis review-gate persistence.

This file belongs to `app/model_analysis`. It reads only the stage-18
`analysis_material_pack` final material table and writes only stage-19
`model_analysis_run` / `model_analysis_result` rows.

Called by `app/model_analysis/service.py`.
External services: none. MySQL: reads/writes through the caller-owned session
and never commits. Redis: none. Hermes: none. Real model calls: none. Formal
Kline impact: none. Trading execution: none.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.time_utils import now_utc
from app.model_analysis.types import (
    ModelProviderCallArtifactPersistencePayload,
    ModelAnalysisResultPersistencePayload,
    ModelAnalysisRunPersistencePayload,
)
from app.storage.mysql.models.model_analysis import (
    ModelAnalysisResult as ModelAnalysisResultRow,
    ModelAnalysisRun,
    ModelProviderCallArtifact,
)
from app.storage.mysql.models.strategy_aggregation import AnalysisMaterialPack

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class ModelAnalysisRepository:
    """Data access helper for stage-19 review gate."""

    def get_material_pack_by_id(self, db_session: Any, *, material_pack_id: str) -> Any | None:
        """Return one stage-18 material pack by business id."""

        _require_sqlalchemy()
        stmt = select(AnalysisMaterialPack).where(AnalysisMaterialPack.material_pack_id == material_pack_id).limit(1)
        return db_session.execute(stmt).scalar_one_or_none()

    def get_existing_result_by_review_version_key(self, db_session: Any, *, review_version_key: str) -> Any | None:
        """Return an existing final review result for idempotency."""

        _require_sqlalchemy()
        stmt = (
            select(ModelAnalysisResultRow)
            .where(ModelAnalysisResultRow.review_version_key == review_version_key)
            .order_by(ModelAnalysisResultRow.id.desc())
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def create_model_analysis_run(
        self,
        db_session: Any,
        *,
        payload: ModelAnalysisRunPersistencePayload,
    ) -> ModelAnalysisRun:
        """Insert one `model_analysis_run` attempt row without committing."""

        created_at_utc = now_utc()
        row = ModelAnalysisRun(
            model_analysis_run_id=payload.model_analysis_run_id,
            review_version_key=payload.review_version_key,
            material_pack_id=payload.material_pack_id,
            aggregation_run_id=payload.aggregation_run_id,
            strategy_signal_run_id=payload.strategy_signal_run_id,
            snapshot_id=payload.snapshot_id,
            symbol=payload.symbol,
            base_interval=payload.base_interval,
            higher_interval=payload.higher_interval,
            review_schema_version=payload.review_schema_version,
            prompt_template_version=payload.prompt_template_version,
            model_provider=payload.model_provider,
            model_name=payload.model_name,
            model_version=payload.model_version,
            review_mode=payload.review_mode,
            model_key=payload.model_key,
            model_role=payload.model_role,
            analysis_mode=payload.analysis_mode,
            chain_id=payload.chain_id,
            chain_step=payload.chain_step,
            parent_model_analysis_run_id=payload.parent_model_analysis_run_id,
            comparison_group_id=payload.comparison_group_id,
            status=payload.status.value,
            input_material_hash=payload.input_material_hash,
            input_summary_json=_json_text(payload.input_summary_json),
            input_char_count=payload.input_char_count,
            input_byte_count=payload.input_byte_count,
            output_char_count=payload.output_char_count,
            output_byte_count=payload.output_byte_count,
            is_final_trading_advice=payload.is_final_trading_advice,
            is_trading_signal=payload.is_trading_signal,
            is_executable=payload.is_executable,
            auto_trading_allowed=payload.auto_trading_allowed,
            human_review_required=payload.human_review_required,
            trigger_source=payload.trigger_source,
            created_by=payload.created_by,
            trace_id=payload.trace_id,
            error_code=payload.error_code,
            error_message=payload.error_message,
            hermes_enabled=payload.hermes_enabled,
            hermes_status=payload.hermes_status,
            hermes_message=payload.hermes_message,
            hermes_error=payload.hermes_error,
            hermes_sent_at_utc=payload.hermes_sent_at_utc,
            profile_version=payload.profile_version,
            profile_hash=payload.profile_hash,
            api_style=payload.api_style,
            provider_request_id=payload.provider_request_id,
            finish_reason=payload.finish_reason,
            request_payload_hash=payload.request_payload_hash,
            rendered_prompt_hash=payload.rendered_prompt_hash,
            prompt_template_hash=payload.prompt_template_hash,
            request_params_summary_json=_json_text(payload.request_params_summary_json),
            capabilities_json=_json_text(payload.capabilities_json),
            response_metadata_summary_json=_json_text(payload.response_metadata_summary_json),
            provider_usage_json=_json_text(payload.provider_usage_json),
            raw_request_hash=payload.raw_request_hash,
            raw_response_hash=payload.raw_response_hash,
            raw_request_storage_ref=payload.raw_request_storage_ref,
            raw_response_storage_ref=payload.raw_response_storage_ref,
            raw_response_char_count=payload.raw_response_char_count,
            raw_response_byte_count=payload.raw_response_byte_count,
            input_token_count=payload.input_token_count,
            output_token_count=payload.output_token_count,
            total_token_count=payload.total_token_count,
            estimated_cost=payload.estimated_cost,
            cost_currency=payload.cost_currency,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def create_model_provider_call_artifact(
        self,
        db_session: Any,
        *,
        payload: ModelProviderCallArtifactPersistencePayload,
    ) -> ModelProviderCallArtifact:
        """Insert one isolated provider-call artifact row without committing."""

        row = ModelProviderCallArtifact(
            artifact_id=payload.artifact_id,
            model_analysis_run_id=payload.model_analysis_run_id,
            artifact_type=payload.artifact_type,
            provider=payload.provider,
            model_key=payload.model_key,
            model_name=payload.model_name,
            model_version=payload.model_version,
            profile_hash=payload.profile_hash,
            storage_ref=payload.storage_ref,
            sha256_hash=payload.sha256_hash,
            char_count=payload.char_count,
            byte_count=payload.byte_count,
            capture_reason=payload.capture_reason,
            created_at_utc=now_utc(),
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def create_model_analysis_result(
        self,
        db_session: Any,
        *,
        payload: ModelAnalysisResultPersistencePayload,
    ) -> ModelAnalysisResultRow:
        """Insert one `model_analysis_result` final row without committing."""

        created_at_utc = now_utc()
        row = ModelAnalysisResultRow(
            model_analysis_result_id=payload.model_analysis_result_id,
            model_analysis_run_id=payload.model_analysis_run_id,
            review_version_key=payload.review_version_key,
            material_pack_id=payload.material_pack_id,
            aggregation_run_id=payload.aggregation_run_id,
            strategy_signal_run_id=payload.strategy_signal_run_id,
            review_decision=payload.review_decision,
            human_review_required=payload.human_review_required,
            evidence_quality=payload.evidence_quality,
            logic_consistency=payload.logic_consistency,
            risk_acceptability=payload.risk_acceptability,
            strategy_conflict_level=payload.strategy_conflict_level,
            missing_evidence_json=_json_text(payload.missing_evidence_json),
            rejection_reasons_json=_json_text(payload.rejection_reasons_json),
            risk_warnings_json=_json_text(payload.risk_warnings_json),
            conditions_to_reconsider_json=_json_text(payload.conditions_to_reconsider_json),
            validation_focus_json=_json_text(payload.validation_focus_json),
            human_review_questions_json=_json_text(payload.human_review_questions_json),
            summary_text=payload.summary_text,
            not_trading_advice_text=payload.not_trading_advice_text,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def record_hermes_result(
        self,
        db_session: Any,
        run_row: Any,
        *,
        hermes_status: str,
        hermes_message: str | None,
        hermes_error: str | None,
        hermes_sent_at_utc: Any | None,
    ) -> Any:
        """Store Hermes dispatch outcome without changing review status."""

        run_row.hermes_status = hermes_status
        run_row.hermes_message = hermes_message
        run_row.hermes_error = hermes_error
        run_row.hermes_sent_at_utc = hermes_sent_at_utc
        run_row.updated_at_utc = now_utc()
        _flush_if_possible(db_session)
        return run_row


def create_default_model_analysis_repository() -> ModelAnalysisRepository:
    """Create the default stage-19 repository."""

    return ModelAnalysisRepository()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for model analysis repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = ["ModelAnalysisRepository", "create_default_model_analysis_repository"]
