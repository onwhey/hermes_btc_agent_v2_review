"""ORM registration facade for stage-20B model review chains.

This file belongs to `app/model_review_chain`. It re-exports the storage-layer
ORM models needed by the stage-20B repository and imports upstream stage-18 and
stage-19 tables referenced by foreign keys.

Called by `app/model_review_chain/repository.py`, tests, and manual metadata
checks. External services: none. MySQL: metadata import only; it does not open
connections, execute migrations, or write rows. Redis: none. Hermes: none.
DeepSeek/large-model calls: none. Trading execution: none.
"""

from __future__ import annotations

from app.storage.mysql.models.strategy_signal import StrategySignalRun
from app.storage.mysql.models.strategy_aggregation import AnalysisMaterialPack, StrategyAggregationRun
from app.storage.mysql.models.model_analysis import ModelAnalysisRun
from app.storage.mysql.models.model_review_chain import ModelReviewChainRun, ModelReviewChainStep

__all__ = [
    "AnalysisMaterialPack",
    "ModelAnalysisRun",
    "ModelReviewChainRun",
    "ModelReviewChainStep",
    "StrategyAggregationRun",
    "StrategySignalRun",
]
