"""Payload builders for stage-20B model review chains.

This file belongs to `app/model_review_chain`. It converts material/chain/step
rows into compact persistence payloads and mock `model_analysis_run` payloads.

Called by `app/model_review_chain/service.py`. It does not query databases,
commit transactions, call provider clients, touch Redis, send Hermes, connect
scheduler, modify formal Kline tables, or perform trading.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_analysis.types import (
    MODEL_REVIEW_MOCK_MODEL_VERSION,
    MODEL_REVIEW_PROVIDER_MOCK,
    ModelAnalysisRunPersistencePayload,
    ModelAnalysisStatus,
)
from app.model_review_chain.id_utils import (
    build_chain_step_id,
    build_review_version_key,
    stable_sha256_text,
)
from app.model_review_chain.schema import (
    ChainProfile,
    ModelReviewChainRequest,
    ModelReviewChainRunPersistencePayload,
    ModelReviewChainStatus,
    ModelReviewChainStepPersistencePayload,
    ModelReviewChainStepStatus,
    json_text,
)


def build_initial_chain_payload(
    *,
    request: ModelReviewChainRequest,
    profile: ChainProfile,
    chain_id: str,
    material_pack: Any,
) -> ModelReviewChainRunPersistencePayload:
    """Build the initial pending chain run payload."""

    return ModelReviewChainRunPersistencePayload(
        chain_id=chain_id,
        material_pack_id=str(_value(material_pack, "material_pack_id")),
        aggregation_run_id=optional_value(material_pack, "aggregation_run_id"),
        strategy_signal_run_id=optional_value(material_pack, "strategy_signal_run_id"),
        snapshot_id=optional_value(material_pack, "snapshot_id"),
        symbol=optional_value(material_pack, "symbol"),
        base_interval=optional_value(material_pack, "base_interval"),
        higher_interval=optional_value(material_pack, "higher_interval"),
        chain_key=profile.chain_key,
        chain_profile_version=profile.chain_profile_version,
        status=ModelReviewChainStatus.PENDING,
        trigger_source=request.trigger_source,
        trace_id=request.trace_id,
        current_step=0,
        total_steps=len(profile.steps),
        success_step_count=0,
        failed_step_count=0,
        timeout_step_count=0,
        skipped_step_count=0,
        blocked_step_count=0,
        max_retry_count=request.max_retry_count,
        summary_text="Mock chain created. No real model was called.",
        error_code=None,
        error_message=None,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
    )


def build_initial_step_payload(
    *,
    chain_id: str,
    definition: Any,
    parent_step_id: str | None,
    max_retry_count: int,
) -> ModelReviewChainStepPersistencePayload:
    """Build one initial pending chain step payload."""

    return ModelReviewChainStepPersistencePayload(
        chain_step_id=build_chain_step_id(chain_id=chain_id, step_no=definition.step_no),
        chain_id=chain_id,
        step_no=definition.step_no,
        model_key=definition.model_key,
        model_role=definition.model_role,
        parent_step_id=parent_step_id,
        parent_model_analysis_run_id=None,
        model_analysis_run_id=None,
        status=ModelReviewChainStepStatus.PENDING,
        attempt_no=0,
        max_retry_count=max_retry_count,
        started_at_utc=None,
        finished_at_utc=None,
        error_code=None,
        error_message=None,
        retry_after_utc=None,
        step_input_hash=None,
        step_output_hash=None,
    )


def build_transient_chain_rows(
    *,
    request: ModelReviewChainRequest,
    profile: ChainProfile,
    chain_id: str,
    material_pack: Any,
) -> tuple[Any, tuple[Any, ...]]:
    """Build dry-run-only chain/step rows without touching the database."""

    chain_payload = build_initial_chain_payload(
        request=request,
        profile=profile,
        chain_id=chain_id,
        material_pack=material_pack,
    )
    chain_row = SimpleNamespace(**payload_to_dict(chain_payload))
    step_rows = []
    parent_step_id: str | None = None
    for step_definition in profile.steps:
        step_payload = build_initial_step_payload(
            chain_id=chain_id,
            definition=step_definition,
            parent_step_id=parent_step_id,
            max_retry_count=request.max_retry_count,
        )
        step_row = SimpleNamespace(**payload_to_dict(step_payload))
        step_rows.append(step_row)
        parent_step_id = step_payload.chain_step_id
    return chain_row, tuple(step_rows)


def build_mock_model_analysis_payload(
    *,
    request: ModelReviewChainRequest,
    chain_row: Any,
    step_row: Any,
    attempt_no: int,
    model_analysis_run_id: str,
    parent_model_analysis_run_id: str | None,
    status: ModelAnalysisStatus,
    input_hash: str,
    output_hash: str,
    simulated_failure: bool,
) -> ModelAnalysisRunPersistencePayload:
    """Build one compact stage-19 run payload for a mock chain step."""

    input_summary = {
        "attempt_no": attempt_no,
        "chain_id": getattr(chain_row, "chain_id", ""),
        "chain_step_id": getattr(step_row, "chain_step_id", ""),
        "material_pack_id": getattr(chain_row, "material_pack_id", ""),
        "model_key": getattr(step_row, "model_key", ""),
        "parent_model_analysis_run_id": parent_model_analysis_run_id,
        "real_model_invoked": False,
        "simulated_failure": simulated_failure,
        "step_no": int(getattr(step_row, "step_no", 0) or 0),
    }
    input_text = json_text(input_summary)
    return ModelAnalysisRunPersistencePayload(
        model_analysis_run_id=model_analysis_run_id,
        review_version_key=build_review_version_key(
            chain_id=str(getattr(chain_row, "chain_id")),
            step_no=int(getattr(step_row, "step_no")),
            attempt_no=attempt_no,
        ),
        material_pack_id=str(getattr(chain_row, "material_pack_id", "")),
        aggregation_run_id=str(getattr(chain_row, "aggregation_run_id", "") or ""),
        strategy_signal_run_id=str(getattr(chain_row, "strategy_signal_run_id", "") or ""),
        snapshot_id=str(getattr(chain_row, "snapshot_id", "") or ""),
        symbol=str(getattr(chain_row, "symbol", "") or ""),
        base_interval=str(getattr(chain_row, "base_interval", "") or ""),
        higher_interval=str(getattr(chain_row, "higher_interval", "") or ""),
        review_schema_version="stage20b_mock_chain_schema_v1",
        prompt_template_version="stage20b_mock_chain_prompt_v1",
        model_provider=MODEL_REVIEW_PROVIDER_MOCK,
        model_name=str(getattr(step_row, "model_key", "mock_chain_step")),
        model_version=MODEL_REVIEW_MOCK_MODEL_VERSION,
        review_mode="chain_step",
        model_key=str(getattr(step_row, "model_key", "")),
        model_role=str(getattr(step_row, "model_role", "")),
        analysis_mode="relay_chain",
        chain_id=str(getattr(chain_row, "chain_id")),
        chain_step=int(getattr(step_row, "step_no", 0) or 0),
        parent_model_analysis_run_id=parent_model_analysis_run_id,
        comparison_group_id=None,
        status=status,
        input_material_hash=input_hash,
        input_summary_json=input_summary,
        input_char_count=len(input_text),
        input_byte_count=len(input_text.encode("utf-8")),
        output_char_count=0,
        output_byte_count=0,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        human_review_required=False,
        trigger_source=request.trigger_source,
        created_by=request.created_by,
        trace_id=request.trace_id,
        error_code="simulated_step_failure" if simulated_failure else None,
        error_message="Mock chain step failed by simulation." if simulated_failure else None,
        hermes_enabled=False,
        hermes_status=None,
        hermes_message=None,
        hermes_error=None,
        hermes_sent_at_utc=None,
        profile_version=str(getattr(chain_row, "chain_profile_version", "")),
        profile_hash=stable_sha256_text(
            {
                "chain_key": getattr(chain_row, "chain_key", ""),
                "model_key": getattr(step_row, "model_key", ""),
                "step_no": getattr(step_row, "step_no", 0),
            }
        ),
        api_style="stage20b_mock_state_machine",
        finish_reason="simulated_failure" if simulated_failure else "mock_success",
        request_payload_hash=input_hash,
        rendered_prompt_hash=input_hash,
        prompt_template_hash=stable_sha256_text({"prompt_template_version": "stage20b_mock_chain_prompt_v1"}),
        request_params_summary_json={"mock_only": True, "real_model_invoked": False},
        capabilities_json={"no_final_advice": True, "mock_only": True},
        response_metadata_summary_json={"step_output_hash": output_hash},
        provider_usage_json={"estimated_cost": "0", "real_model_invoked": False},
        raw_request_hash=None,
        raw_response_hash=None,
        raw_request_storage_ref=None,
        raw_response_storage_ref=None,
    )


def build_chain_payload_from_row(chain_row: Any, *, state: Any) -> ModelReviewChainRunPersistencePayload:
    """Build an updated chain payload from a row and derived state."""

    return ModelReviewChainRunPersistencePayload(
        chain_id=str(getattr(chain_row, "chain_id", "")),
        material_pack_id=str(getattr(chain_row, "material_pack_id", "")),
        aggregation_run_id=row_optional(chain_row, "aggregation_run_id"),
        strategy_signal_run_id=row_optional(chain_row, "strategy_signal_run_id"),
        snapshot_id=row_optional(chain_row, "snapshot_id"),
        symbol=row_optional(chain_row, "symbol"),
        base_interval=row_optional(chain_row, "base_interval"),
        higher_interval=row_optional(chain_row, "higher_interval"),
        chain_key=str(getattr(chain_row, "chain_key", "")),
        chain_profile_version=str(getattr(chain_row, "chain_profile_version", "")),
        status=state.status,
        trigger_source=str(getattr(chain_row, "trigger_source", TRIGGER_SOURCE_CLI)),
        trace_id=str(getattr(chain_row, "trace_id", "")),
        current_step=state.current_step,
        total_steps=int(getattr(chain_row, "total_steps", 0) or 0),
        success_step_count=state.success_step_count,
        failed_step_count=state.failed_step_count,
        timeout_step_count=state.timeout_step_count,
        skipped_step_count=state.skipped_step_count,
        blocked_step_count=state.blocked_step_count,
        max_retry_count=int(getattr(chain_row, "max_retry_count", 0) or 0),
        summary_text=state.summary_text,
        error_code=state.error_code,
        error_message=state.error_message,
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
    )


def build_step_payload_from_row(
    step_row: Any,
    *,
    status: ModelReviewChainStepStatus,
    attempt_no: int,
    started_at_utc: Any | None = None,
    finished_at_utc: Any | None = None,
    parent_model_analysis_run_id: str | None = None,
    model_analysis_run_id: str | None = None,
    step_input_hash: str | None = None,
    step_output_hash: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ModelReviewChainStepPersistencePayload:
    """Build an updated step payload from the existing step row."""

    return ModelReviewChainStepPersistencePayload(
        chain_step_id=str(getattr(step_row, "chain_step_id", "")),
        chain_id=str(getattr(step_row, "chain_id", "")),
        step_no=int(getattr(step_row, "step_no", 0) or 0),
        model_key=str(getattr(step_row, "model_key", "")),
        model_role=str(getattr(step_row, "model_role", "")),
        parent_step_id=row_optional(step_row, "parent_step_id"),
        parent_model_analysis_run_id=parent_model_analysis_run_id
        if parent_model_analysis_run_id is not None
        else row_optional(step_row, "parent_model_analysis_run_id"),
        model_analysis_run_id=model_analysis_run_id
        if model_analysis_run_id is not None
        else row_optional(step_row, "model_analysis_run_id"),
        status=status,
        attempt_no=attempt_no,
        max_retry_count=int(getattr(step_row, "max_retry_count", 0) or 0),
        started_at_utc=started_at_utc if started_at_utc is not None else getattr(step_row, "started_at_utc", None),
        finished_at_utc=finished_at_utc if finished_at_utc is not None else getattr(step_row, "finished_at_utc", None),
        error_code=error_code,
        error_message=error_message,
        retry_after_utc=getattr(step_row, "retry_after_utc", None),
        step_input_hash=step_input_hash if step_input_hash is not None else row_optional(step_row, "step_input_hash"),
        step_output_hash=step_output_hash if step_output_hash is not None else row_optional(step_row, "step_output_hash"),
    )


def build_step_input_hash(
    *,
    chain_row: Any,
    step_row: Any,
    parent_model_analysis_run_id: str | None,
    attempt_no: int,
) -> str:
    """Build a compact deterministic input hash for a mock step attempt."""

    return stable_sha256_text(
        {
            "attempt_no": attempt_no,
            "chain_id": getattr(chain_row, "chain_id", ""),
            "material_pack_id": getattr(chain_row, "material_pack_id", ""),
            "model_key": getattr(step_row, "model_key", ""),
            "parent_model_analysis_run_id": parent_model_analysis_run_id,
            "step_no": int(getattr(step_row, "step_no", 0) or 0),
        }
    )


def apply_step_payload_to_row(step_row: Any, payload: ModelReviewChainStepPersistencePayload) -> None:
    """Apply a dry-run step payload to an in-memory row."""

    for field_name, value in payload_to_dict(payload).items():
        if hasattr(value, "value"):
            value = value.value
        setattr(step_row, field_name, value)


def payload_to_dict(payload: Any) -> dict[str, Any]:
    """Return a shallow dictionary for a dataclass payload."""

    return dict(payload.__dict__)


def clone_row(row: Any) -> Any:
    """Clone an ORM row into a detached in-memory object for dry-run resume."""

    values = dict(getattr(row, "__dict__", {}))
    values.pop("_sa_instance_state", None)
    return SimpleNamespace(**values)


def optional_value(row: Any, field_name: str) -> str | None:
    """Return an object attribute as optional text."""

    value = _value(row, field_name)
    return str(value) if value not in (None, "") else None


def row_optional(row: Any, field_name: str) -> str | None:
    """Return a row attribute as optional text."""

    value = getattr(row, field_name, None)
    return str(value) if value not in (None, "") else None


def _value(row: Any, field_name: str) -> Any:
    return getattr(row, field_name, "")


__all__ = [
    "apply_step_payload_to_row",
    "build_chain_payload_from_row",
    "build_initial_chain_payload",
    "build_initial_step_payload",
    "build_mock_model_analysis_payload",
    "build_step_input_hash",
    "build_step_payload_from_row",
    "build_transient_chain_rows",
    "clone_row",
    "row_optional",
]
