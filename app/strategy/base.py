"""Base class for independent strategy signal generators.

This file belongs to `app/strategy`. It defines the common interface used by
stage-16 strategies.
It does not read or write databases, send Hermes, request Binance, call large
language models, read account/position state, generate final trading advice, or
perform trading.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.strategy.types import StrategyEvaluationInput, StrategySignal

if TYPE_CHECKING:
    from app.strategy.evidence_context import EvidenceContext
    from app.strategy.common.result_contract import StrategyResult


class BaseStrategy:
    """Common interface for one independent strategy.

    Parameters: subclasses receive one `StrategyEvaluationInput` in `evaluate`.
    Return value: one 23A `StrategyResult`, or a legacy `StrategySignal` during
    compatibility migration.
    Failure scenarios: subclasses may raise; `StrategyRunner` captures failures
    so one strategy cannot stop the whole batch.
    External service access: forbidden for stage-16 strategies.
    Data impact: strategies must be pure calculations and must not write MySQL,
    Redis, Hermes, formal Kline tables, or final advice tables.
    """

    strategy_name: str = ""
    strategy_version: str = ""

    def evaluate(self, input_data: StrategyEvaluationInput) -> "StrategyResult | StrategySignal":
        """Evaluate one strategy against the provided snapshot-derived input."""

        raise NotImplementedError

    def evaluate_with_evidence(
        self,
        input_data: StrategyEvaluationInput,
        evidence_context: "EvidenceContext",
    ) -> "StrategyResult | StrategySignal":
        """Evaluate with same-run public evidence when a strategy needs it.

        The default implementation preserves the stage-16 interface for
        independent strategies. Dependent strategies may override this method,
        but must only read public fields exposed by `EvidenceContext`.
        """

        return self.evaluate(input_data)


__all__ = ["BaseStrategy"]
