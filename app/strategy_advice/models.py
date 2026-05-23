"""ORM registration facade for stage-21A strategy advice lifecycle.

This file belongs to `app/strategy_advice`. It re-exports the storage-layer ORM
models needed by the stage-21A repository and imports upstream stage-18,
stage-20A, and stage-20B tables referenced by foreign key.

Called by `app/strategy_advice/repository.py`, tests, and metadata checks.
External services: none. MySQL: metadata import only; it does not open
connections, execute migrations, or write rows. Redis: none. Hermes: none.
Large-model calls: none. Trading execution: none.
"""

from __future__ import annotations

from app.storage.mysql.models.model_review_aggregation import ModelReviewAggregationRun
from app.storage.mysql.models.model_review_chain import ModelReviewChainRun
from app.storage.mysql.models.strategy_advice import (
    StrategyAdvice,
    StrategyAdviceEvent,
    StrategyAdviceLifecycleReview,
    StrategyAdviceTradeSetup,
)
from app.storage.mysql.models.strategy_aggregation import AnalysisMaterialPack
from app.storage.mysql.models.strategy_signal import StrategySignalRun

__all__ = [
    "AnalysisMaterialPack",
    "ModelReviewAggregationRun",
    "ModelReviewChainRun",
    "StrategyAdvice",
    "StrategyAdviceEvent",
    "StrategyAdviceLifecycleReview",
    "StrategyAdviceTradeSetup",
    "StrategySignalRun",
]
