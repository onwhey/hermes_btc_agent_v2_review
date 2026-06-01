"""Factory helpers for downstream services used by stage-25A pipeline.

This file belongs to `app/strategy_pipeline`. It only creates configured
instances of existing stage services so the pipeline service can stay focused
on orchestration.

Called by `app/strategy_pipeline/service.py`. External services: none at
factory time. MySQL: none. Redis: none. Hermes: none. Large models: none.
Trading execution: none.
"""

from __future__ import annotations

from dataclasses import replace

from app.core.config import AppSettings
from app.model_review_aggregation.service import ModelReviewAggregationService
from app.model_review_chain.worker import ModelReviewChainWorker
from app.scheduler.config import build_scheduler_runtime_config
from app.scheduler.strategy_signal_scheduler_service import StrategySignalSchedulerService
from app.strategy.aggregation.evidence_service import StrategyEvidenceAggregationService
from app.strategy.aggregation.service import StrategyAggregationService
from app.strategy.evidence_quality.service import StrategyEvidenceQualityGateService
from app.strategy.signal_service import StrategySignalService
from app.strategy_advice.scheduler_service import StrategyAdviceSchedulerService
from app.strategy_pipeline.types import StrategyPipelineRequest
from app.weak_models.output_quality_service import WeakModelOutputQualityService
from app.weak_models.service import WeakModelService


def create_pipeline_stage17_service(
    *,
    settings: AppSettings,
    request: StrategyPipelineRequest,
) -> StrategySignalSchedulerService:
    """Create stage-17 service using the pipeline scope and no Hermes send."""

    scheduler_config = replace(
        build_scheduler_runtime_config(settings),
        strategy_signal_symbol=request.symbol,
        strategy_signal_base_interval=request.base_interval,
        strategy_signal_higher_interval=request.higher_interval,
        strategy_signal_hermes_enabled=False,
    )
    return StrategySignalSchedulerService(config=scheduler_config, settings=settings)


def create_pipeline_stage18_service(*, settings: AppSettings) -> StrategyAggregationService:
    """Create stage-18 service while disabling Hermes inside the pipeline path."""

    stage_settings = replace(settings, strategy_aggregation_hermes_enabled=False)
    return StrategyAggregationService(settings=stage_settings)


def create_pipeline_stage23f_service() -> StrategyEvidenceAggregationService:
    """Create the existing 23F evidence aggregation service for explicit 25A use."""

    return StrategyEvidenceAggregationService()


def create_pipeline_stage26b_service(*, settings: AppSettings) -> StrategyEvidenceQualityGateService:
    """Create the 26B evidence quality gate service for explicit 25A use."""

    return StrategyEvidenceQualityGateService(settings=settings)


def create_pipeline_stage27a_service() -> WeakModelService:
    """Create the existing 27A weak-model service for explicit 25A use."""

    return WeakModelService()


def create_pipeline_stage27b_service() -> WeakModelOutputQualityService:
    """Create the existing 27B output-quality service for explicit 25A use."""

    return WeakModelOutputQualityService()


def create_pipeline_stage16_service() -> StrategySignalService:
    """Create the existing stage-16 strategy signal service for manual retry."""

    return StrategySignalService()


def create_pipeline_stage20_worker(
    *,
    settings: AppSettings,
    request: StrategyPipelineRequest,
) -> ModelReviewChainWorker:
    """Create 20C worker with pipeline-level real-model gate applied."""

    stage_settings = replace(
        settings,
        model_review_real_model_enabled=(
            settings.model_review_real_model_enabled
            and settings.strategy_pipeline_real_model_enabled
            and request.use_real_model
            and request.confirm_real_model_cost
        ),
        model_review_hermes_enabled=False,
    )
    return ModelReviewChainWorker(settings=stage_settings)


def create_pipeline_stage20a_service(*, settings: AppSettings) -> ModelReviewAggregationService:
    """Create the existing 20A aggregation service."""

    return ModelReviewAggregationService(settings=settings)


def create_pipeline_stage21_service(
    *,
    settings: AppSettings,
    request: StrategyPipelineRequest,
) -> StrategyAdviceSchedulerService:
    """Create 21C service with pipeline-level Hermes gate applied."""

    stage_settings = replace(
        settings,
        strategy_advice_notification_send_enabled=(
            settings.strategy_advice_notification_send_enabled
            and settings.strategy_pipeline_notification_send_enabled
            and request.send_real_hermes
        ),
    )
    return StrategyAdviceSchedulerService(settings=stage_settings)


__all__ = [
    "create_pipeline_stage16_service",
    "create_pipeline_stage17_service",
    "create_pipeline_stage18_service",
    "create_pipeline_stage23f_service",
    "create_pipeline_stage26b_service",
    "create_pipeline_stage27a_service",
    "create_pipeline_stage27b_service",
    "create_pipeline_stage20_worker",
    "create_pipeline_stage20a_service",
    "create_pipeline_stage21_service",
]
