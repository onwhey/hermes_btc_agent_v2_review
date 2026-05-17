"""Base class for independent strategy signal generators.

This file belongs to `app/strategy`. It defines the common interface used by
stage-16 strategies.
It does not read or write databases, send Hermes, request Binance, call large
language models, read account/position state, generate final trading advice, or
perform trading.
"""

from __future__ import annotations

from app.strategy.types import StrategyEvaluationInput, StrategySignal


class BaseStrategy:
    """Common interface for one independent strategy.

    Parameters: subclasses receive one `StrategyEvaluationInput` in `evaluate`.
    Return value: one `StrategySignal`.
    Failure scenarios: subclasses may raise; `StrategyRunner` captures failures
    so one strategy cannot stop the whole batch.
    External service access: forbidden for stage-16 strategies.
    Data impact: strategies must be pure calculations and must not write MySQL,
    Redis, Hermes, formal Kline tables, or final advice tables.
    """

    strategy_name: str = ""
    strategy_version: str = ""

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategySignal:
        """Evaluate one strategy against the provided snapshot-derived input."""

        raise NotImplementedError


__all__ = ["BaseStrategy"]

