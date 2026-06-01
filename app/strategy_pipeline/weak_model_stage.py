"""27A/27B weak-model orchestration gate for the strategy pipeline.

This file belongs to `app/strategy_pipeline`. It coordinates already existing
27A weak-model execution/reuse and 27B output-quality execution/reuse before
stage 18 material-pack generation.

Call chain:
app/strategy_pipeline/service.py::StrategyPipelineService._run_confirmed_pipeline
    -> app/strategy_pipeline/weak_model_stage.py::run_or_reuse_weak_model_stages_for_pipeline
    -> app/weak_models/service.py::WeakModelService.run_weak_models_for_strategy_signal
    -> app/weak_models/output_quality_service.py::WeakModelOutputQualityService.check_weak_model_output_quality

The module does not implement weak-model formulas, does not change weak-model
configuration, does not build material-pack schema, does not call any large
model, does not send Hermes, does not request Binance REST, does not read
private account/position state, and does not perform automatic trading. It may
read MySQL through the pipeline repository and may write only through existing
27A/27B services when the pipeline is already in confirm-write mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.strategy_pipeline.types import (
    PIPELINE_STEP_STAGE27A,
    PIPELINE_STEP_STAGE27B,
    StrategyPipelineRequest,
    StrategyPipelineStatus,
    status_value,
)
from app.strategy_pipeline.utils import PipelineState, compact_object, require_slot, text_or_none
from app.weak_models.output_quality_types import (
    WeakModelQualityCheckRequest,
    WeakModelQualityStatus,
)
from app.weak_models.types import WeakModelRunRequest, WeakModelRunStatus


WEAK_MODEL_ACTION_CREATED = "created"
WEAK_MODEL_ACTION_REUSED = "reused"
WEAK_MODEL_ACTION_BLOCKED = "blocked"


@dataclass(frozen=True)
class WeakModelPipelineStageOutcome:
    """Outcome returned to the pipeline service after 27A/27B orchestration."""

    should_continue: bool
    status: StrategyPipelineStatus = StrategyPipelineStatus.BLOCKED
    current_step: str | None = None
    message: str = ""
    error_code: str | None = None
    error_message: str | None = None


def run_or_reuse_weak_model_stages_for_pipeline(
    db_session: Any,
    *,
    request: StrategyPipelineRequest,
    state: PipelineState,
    settings: Any,
    repository: Any,
    weak_model_service: Any,
    quality_service: Any,
) -> WeakModelPipelineStageOutcome:
    """Ensure 27A and 27B have completed before stage 18 can run.

    Parameters identify the current pipeline state and injected services.
    Returns a continue/block outcome. Failures are represented as blocked
    pipeline states so stage 18 does not generate `missing` or `unchecked`
    weak-model material in automatic runs. External services are not contacted
    by this helper; it only calls existing local DB-backed 27A/27B services.
    """

    stage27a = _run_or_reuse_stage27a(
        db_session,
        request=request,
        state=state,
        settings=settings,
        repository=repository,
        weak_model_service=weak_model_service,
    )
    if not stage27a.should_continue:
        return stage27a

    return _run_or_reuse_stage27b(
        db_session,
        request=request,
        state=state,
        settings=settings,
        repository=repository,
        quality_service=quality_service,
    )


def _run_or_reuse_stage27a(
    db_session: Any,
    *,
    request: StrategyPipelineRequest,
    state: PipelineState,
    settings: Any,
    repository: Any,
    weak_model_service: Any,
) -> WeakModelPipelineStageOutcome:
    state.current_step = PIPELINE_STEP_STAGE27A
    if not bool(getattr(settings, "strategy_pipeline_weak_models_enabled", True)):
        state.weak_model_pipeline_action = WEAK_MODEL_ACTION_BLOCKED
        state.details["stage27a_config_disabled"] = {
            "env": "STRATEGY_PIPELINE_WEAK_MODELS_ENABLED",
            "value": False,
            "reason": "weak_model_disabled_by_config",
        }
        return WeakModelPipelineStageOutcome(
            should_continue=False,
            current_step=PIPELINE_STEP_STAGE27A,
            message="27A weak-model stage is disabled by pipeline configuration; stage 18 is blocked.",
            error_code="weak_model_disabled_by_config",
        )

    package = _lookup_reusable_weak_model_package(
        db_session,
        request=request,
        state=state,
        repository=repository,
    )
    if package is not None:
        _apply_weak_model_package_to_state(state, package=package, action=WEAK_MODEL_ACTION_REUSED)
        return WeakModelPipelineStageOutcome(should_continue=True)

    try:
        result = _call_stage27a_service(
            db_session,
            request=request,
            state=state,
            weak_model_service=weak_model_service,
        )
    except Exception as exc:  # noqa: BLE001 - pipeline must block before stage 18.
        state.weak_model_pipeline_action = WEAK_MODEL_ACTION_BLOCKED
        state.details["stage27a_exception"] = str(exc)
        return WeakModelPipelineStageOutcome(
            should_continue=False,
            current_step=PIPELINE_STEP_STAGE27A,
            message="27A weak-model stage raised before producing a reusable WMR/WMA; stage 18 is blocked.",
            error_code="weak_model_run_failed",
            error_message=str(exc),
        )

    state.details["stage27a_result"] = compact_object(result)
    aggregation = getattr(result, "aggregation", None)
    if status_value(getattr(result, "status", "")) != WeakModelRunStatus.SUCCESS.value or aggregation is None:
        _apply_weak_model_result_to_state(state, result=result, action=WEAK_MODEL_ACTION_BLOCKED)
        return WeakModelPipelineStageOutcome(
            should_continue=False,
            current_step=PIPELINE_STEP_STAGE27A,
            message=str(getattr(result, "error_message", "") or getattr(result, "message", "") or "27A weak-model run failed."),
            error_code=getattr(result, "error_code", None) or getattr(result, "blocked_reason", None) or "weak_model_run_failed",
            error_message=getattr(result, "error_message", None),
        )

    _apply_weak_model_result_to_state(state, result=result, action=_database_action_to_pipeline_action(result))
    return WeakModelPipelineStageOutcome(should_continue=True)


def _run_or_reuse_stage27b(
    db_session: Any,
    *,
    request: StrategyPipelineRequest,
    state: PipelineState,
    settings: Any,
    repository: Any,
    quality_service: Any,
) -> WeakModelPipelineStageOutcome:
    state.current_step = PIPELINE_STEP_STAGE27B
    if not bool(getattr(settings, "strategy_pipeline_weak_model_quality_gate_enabled", True)):
        state.weak_model_quality_pipeline_action = WEAK_MODEL_ACTION_BLOCKED
        state.details["stage27b_config_disabled"] = {
            "env": "STRATEGY_PIPELINE_WEAK_MODEL_QUALITY_GATE_ENABLED",
            "value": False,
            "reason": "weak_model_quality_gate_disabled_by_config",
        }
        return WeakModelPipelineStageOutcome(
            should_continue=False,
            current_step=PIPELINE_STEP_STAGE27B,
            message="27B weak-model quality gate is disabled by pipeline configuration; stage 18 is blocked.",
            error_code="weak_model_quality_gate_disabled_by_config",
        )

    if not state.weak_model_run_id:
        state.weak_model_quality_pipeline_action = WEAK_MODEL_ACTION_BLOCKED
        return WeakModelPipelineStageOutcome(
            should_continue=False,
            current_step=PIPELINE_STEP_STAGE27B,
            message="27B cannot run because 27A did not provide weak_model_run_id.",
            error_code="weak_model_run_missing",
        )

    quality_check = _lookup_reusable_quality_check(
        db_session,
        repository=repository,
        weak_model_run_id=state.weak_model_run_id,
    )
    if quality_check is not None:
        _apply_quality_check_to_state(state, quality_check=quality_check, action=WEAK_MODEL_ACTION_REUSED)
    else:
        try:
            report = _call_stage27b_service(
                db_session,
                request=request,
                state=state,
                quality_service=quality_service,
            )
        except Exception as exc:  # noqa: BLE001 - pipeline must block before stage 18.
            state.weak_model_quality_pipeline_action = WEAK_MODEL_ACTION_BLOCKED
            state.details["stage27b_exception"] = str(exc)
            return WeakModelPipelineStageOutcome(
                should_continue=False,
                current_step=PIPELINE_STEP_STAGE27B,
                message="27B weak-model quality check raised before producing WMQC; stage 18 is blocked.",
                error_code="weak_model_quality_check_failed",
                error_message=str(exc),
            )
        results = tuple(getattr(report, "results", ()) or ())
        result = results[0] if results else None
        state.details["stage27b_result"] = compact_object(result) if result is not None else {"status": "missing"}
        if result is None:
            state.weak_model_quality_pipeline_action = WEAK_MODEL_ACTION_BLOCKED
            return WeakModelPipelineStageOutcome(
                should_continue=False,
                current_step=PIPELINE_STEP_STAGE27B,
                message="27B did not return a quality result; stage 18 is blocked.",
                error_code="weak_model_quality_check_failed",
            )
        _apply_quality_result_to_state(state, result=result, action=_database_action_to_pipeline_action(result))

    quality_status = str(state.weak_model_quality_status or "")
    if quality_status == WeakModelQualityStatus.CRITICAL.value:
        return WeakModelPipelineStageOutcome(
            should_continue=False,
            current_step=PIPELINE_STEP_STAGE27B,
            message="27B weak-model quality status is critical; stage 18 is blocked.",
            error_code="weak_model_quality_critical",
        )
    if quality_status not in {WeakModelQualityStatus.PASSED.value, WeakModelQualityStatus.WARNING.value}:
        return WeakModelPipelineStageOutcome(
            should_continue=False,
            current_step=PIPELINE_STEP_STAGE27B,
            message="27B weak-model quality check did not pass or warn; stage 18 is blocked.",
            error_code="weak_model_quality_check_failed",
        )
    return WeakModelPipelineStageOutcome(should_continue=True)


def _lookup_reusable_weak_model_package(
    db_session: Any,
    *,
    request: StrategyPipelineRequest,
    state: PipelineState,
    repository: Any,
) -> Any | None:
    getter = getattr(repository, "get_latest_success_weak_model_package_for_strategy_run", None)
    if not callable(getter) or not state.strategy_signal_run_id:
        return None
    snapshot_id = state.strategy_signal_snapshot_id or _lookup_strategy_signal_snapshot_id(
        db_session,
        repository=repository,
        run_id=state.strategy_signal_run_id,
    )
    state.strategy_signal_snapshot_id = snapshot_id
    return getter(
        db_session,
        strategy_signal_run_id=state.strategy_signal_run_id,
        snapshot_id=snapshot_id,
        symbol=request.symbol,
        base_interval=request.base_interval,
        higher_interval=request.higher_interval,
        kline_slot_utc=require_slot(state.kline_slot_utc),
    )


def _lookup_strategy_signal_snapshot_id(db_session: Any, *, repository: Any, run_id: str) -> str | None:
    getter = getattr(repository, "get_strategy_signal_run_by_run_id", None)
    if not callable(getter):
        return None
    row = getter(db_session, run_id=run_id)
    return text_or_none(getattr(row, "snapshot_id", None)) if row is not None else None


def _lookup_reusable_quality_check(db_session: Any, *, repository: Any, weak_model_run_id: str) -> Any | None:
    getter = getattr(repository, "get_latest_weak_model_quality_check_by_run_id", None)
    if not callable(getter):
        return None
    return getter(db_session, weak_model_run_id=weak_model_run_id)


def _call_stage27a_service(
    db_session: Any,
    *,
    request: StrategyPipelineRequest,
    state: PipelineState,
    weak_model_service: Any,
) -> Any:
    weak_model_request = WeakModelRunRequest(
        strategy_signal_run_id=str(state.strategy_signal_run_id or ""),
        pipeline_run_id=state.pipeline_run_id,
        symbol=request.symbol,
        base_interval=request.base_interval,
        higher_interval=request.higher_interval,
        kline_slot_utc=state.kline_slot_utc,
        trigger_source=request.trigger_source,
        dry_run=False,
        confirm_write=True,
        created_by=request.created_by,
        trace_id=request.trace_id,
    )
    if hasattr(weak_model_service, "run_weak_models_for_strategy_signal"):
        return weak_model_service.run_weak_models_for_strategy_signal(db_session, weak_model_request)
    return weak_model_service(db_session=db_session, request=weak_model_request)


def _call_stage27b_service(
    db_session: Any,
    *,
    request: StrategyPipelineRequest,
    state: PipelineState,
    quality_service: Any,
) -> Any:
    quality_request = WeakModelQualityCheckRequest(
        weak_model_run_id=state.weak_model_run_id,
        symbol=request.symbol,
        base_interval=request.base_interval,
        higher_interval=request.higher_interval,
        dry_run=False,
        confirm_write=True,
        trace_id=request.trace_id,
    )
    if hasattr(quality_service, "check_weak_model_output_quality"):
        return quality_service.check_weak_model_output_quality(db_session, request=quality_request)
    return quality_service(db_session=db_session, request=quality_request)


def _apply_weak_model_package_to_state(state: PipelineState, *, package: Any, action: str) -> None:
    run = getattr(package, "run", None)
    aggregation = getattr(package, "aggregation", None)
    state.weak_model_run_id = text_or_none(getattr(run, "weak_model_run_id", None))
    state.weak_model_aggregation_id = text_or_none(getattr(aggregation, "weak_model_aggregation_id", None))
    state.weak_model_status = status_value(getattr(run, "run_status", "")) or None
    state.weak_model_pipeline_action = action
    _apply_aggregation_summary_to_state(state, aggregation)
    state.details["stage27a_result"] = {
        "status": state.weak_model_status,
        "weak_model_run_id": state.weak_model_run_id,
        "weak_model_aggregation_id": state.weak_model_aggregation_id,
        "pipeline_action": action,
    }


def _apply_weak_model_result_to_state(state: PipelineState, *, result: Any, action: str) -> None:
    aggregation = getattr(result, "aggregation", None)
    state.weak_model_run_id = text_or_none(getattr(result, "weak_model_run_id", None))
    state.weak_model_aggregation_id = text_or_none(
        getattr(result, "weak_model_aggregation_id", None)
        or getattr(aggregation, "weak_model_aggregation_id", None)
    )
    state.weak_model_status = status_value(getattr(result, "status", "")) or None
    state.weak_model_pipeline_action = action
    _apply_aggregation_summary_to_state(state, aggregation)
    state.details["stage27a_pipeline_action"] = action


def _apply_aggregation_summary_to_state(state: PipelineState, aggregation: Any | None) -> None:
    if aggregation is None:
        return
    state.weak_model_directional_score = _float_or_none(getattr(aggregation, "directional_score", None))
    state.weak_model_risk_level = text_or_none(getattr(aggregation, "risk_level", None))
    state.weak_model_trade_permission = text_or_none(getattr(aggregation, "trade_permission", None))


def _apply_quality_check_to_state(state: PipelineState, *, quality_check: Any, action: str) -> None:
    state.weak_model_quality_check_id = text_or_none(getattr(quality_check, "quality_check_id", None))
    state.weak_model_quality_status = status_value(getattr(quality_check, "status", "")) or None
    state.weak_model_quality_pipeline_action = action
    state.details["stage27b_result"] = {
        "status": state.weak_model_quality_status,
        "quality_check_id": state.weak_model_quality_check_id,
        "weak_model_run_id": text_or_none(getattr(quality_check, "weak_model_run_id", None)),
        "weak_model_aggregation_id": text_or_none(getattr(quality_check, "weak_model_aggregation_id", None)),
        "pipeline_action": action,
    }


def _apply_quality_result_to_state(state: PipelineState, *, result: Any, action: str) -> None:
    state.weak_model_quality_check_id = text_or_none(getattr(result, "quality_check_id", None))
    state.weak_model_quality_status = status_value(getattr(result, "status", "")) or None
    state.weak_model_quality_pipeline_action = action
    state.details["stage27b_pipeline_action"] = action


def _database_action_to_pipeline_action(result: Any) -> str:
    action_text = str(getattr(result, "database_action", "") or "")
    if "updated" in action_text or "reused" in action_text:
        return WEAK_MODEL_ACTION_REUSED
    if "created" in action_text or "pending" in action_text:
        return WEAK_MODEL_ACTION_CREATED
    return WEAK_MODEL_ACTION_CREATED


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "WEAK_MODEL_ACTION_BLOCKED",
    "WEAK_MODEL_ACTION_CREATED",
    "WEAK_MODEL_ACTION_REUSED",
    "WeakModelPipelineStageOutcome",
    "run_or_reuse_weak_model_stages_for_pipeline",
]
