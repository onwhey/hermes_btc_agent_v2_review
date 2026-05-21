"""ORM registration facade for stage-20A model review aggregation.

This file belongs to `app/model_review_aggregation`. It re-exports the
storage-layer ORM model needed by the stage-20A repository and imports the
upstream stage-16/stage-18/stage-19 tables that the aggregation table and
queries reference by foreign key.

Called by `app/model_review_aggregation/repository.py`, tests, and manual
metadata checks. External services: none. MySQL: metadata import only; it does
not open connections, execute migrations, or write rows. Redis: none. Hermes:
none. Large-model calls: none. Trading execution: none.
"""

from __future__ import annotations

from app.storage.mysql.models.strategy_signal import StrategySignalRun
from app.storage.mysql.models.strategy_aggregation import AnalysisMaterialPack, StrategyAggregationRun
from app.storage.mysql.models.model_analysis import ModelAnalysisResult, ModelAnalysisRun
from app.storage.mysql.models.model_review_aggregation import ModelReviewAggregationRun

__all__ = [
    "AnalysisMaterialPack",
    "ModelAnalysisResult",
    "ModelAnalysisRun",
    "ModelReviewAggregationRun",
    "StrategyAggregationRun",
    "StrategySignalRun",
]
