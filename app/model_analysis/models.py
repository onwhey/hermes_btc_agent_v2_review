"""ORM registration facade for stage-19 model analysis.

This file belongs to `app/model_analysis`. It re-exports the storage-layer ORM
models needed by the stage-19 repository and, more importantly, imports the
upstream stage-16/stage-18 tables that `model_analysis_result` references by
foreign key.

Called by `app/model_analysis/repository.py`, tests, and manual metadata
checks. External services: none. MySQL: metadata import only; it does not open
connections, execute migrations, or write rows. Redis: none. Hermes: none.
Large-model calls: none. Trading execution: none.
"""

from __future__ import annotations

from app.storage.mysql.models.strategy_signal import StrategySignalRun
from app.storage.mysql.models.strategy_aggregation import AnalysisMaterialPack, StrategyAggregationRun
from app.storage.mysql.models.model_analysis import (
    ModelAnalysisResult,
    ModelAnalysisRun,
    ModelProviderCallArtifact,
)

__all__ = [
    "AnalysisMaterialPack",
    "ModelAnalysisResult",
    "ModelAnalysisRun",
    "ModelProviderCallArtifact",
    "StrategyAggregationRun",
    "StrategySignalRun",
]
