"""Stage-20A model review aggregation package.

This package contains deterministic aggregation and reuse checks over already
persisted stage-19 model review rows. It does not call model providers,
generate final trading advice, connect scheduler jobs, write Redis, modify
formal Kline tables, or perform trading.
"""

from app.model_review_aggregation.schema import ModelReviewAggregationRequest, ModelReviewAggregationResult
from app.model_review_aggregation.service import (
    ModelReviewAggregationService,
    create_default_model_review_aggregation_service,
    run_model_review_aggregation,
)

__all__ = [
    "ModelReviewAggregationRequest",
    "ModelReviewAggregationResult",
    "ModelReviewAggregationService",
    "create_default_model_review_aggregation_service",
    "run_model_review_aggregation",
]
