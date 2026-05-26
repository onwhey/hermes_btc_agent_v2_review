"""Common strategy contract helpers for stage-23A.

The package contains protocol DTOs, read-only context views, validators, JSON
payload helpers, and adapters for the existing stage-16 strategy framework.
It does not request external services, write MySQL/Redis directly, send Hermes,
call large language models, generate final advice, or perform trading.
"""

from app.strategy.common.context_view import StrategyContextView
from app.strategy.common.result_contract import (
    StrategyCommonResult,
    StrategyEvidenceItem,
    StrategyKeyLevel,
    StrategyResult,
    StrategyRiskFlag,
    StrategyRole,
    StrategyScenarioCandidate,
)
from app.strategy.common.result_validator import StrategyResultValidator, validate_strategy_result

__all__ = [
    "StrategyCommonResult",
    "StrategyContextView",
    "StrategyEvidenceItem",
    "StrategyKeyLevel",
    "StrategyResult",
    "StrategyResultValidator",
    "StrategyRiskFlag",
    "StrategyRole",
    "StrategyScenarioCandidate",
    "validate_strategy_result",
]
