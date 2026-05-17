"""Placeholder for future Gann strategy expansion.

This file belongs to `app/strategy/strategies`. It deliberately does not
implement Gann analysis in stage 16 and returns a not-implemented signal.
It does not fake Gann conclusions, request Binance, write databases, send
Hermes, call large language models, read account/position state, generate final
trading advice, or trade.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.strategy.base import BaseStrategy
from app.strategy.types import (
    DirectionBias,
    RiskLevel,
    StrategyEvaluationInput,
    StrategySignal,
    StrategySignalStatus,
)


class GannPlaceholderStrategy(BaseStrategy):
    """Return a transparent placeholder signal for the future Gann strategy."""

    strategy_name = "gann_placeholder"
    strategy_version = "placeholder_v1"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._strategy_config = dict(config or {})

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategySignal:
        """Return `not_implemented` without pretending to perform Gann analysis."""

        return StrategySignal(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_status=StrategySignalStatus.NOT_IMPLEMENTED,
            direction_bias=DirectionBias.NOT_APPLICABLE,
            risk_level=RiskLevel.NOT_APPLICABLE,
            signal_strength=0.0,
            reason_codes=("gann_strategy_not_implemented",),
            reason_text="江恩策略尚未实现，本阶段仅保留策略扩展位，不输出江恩判断。",
            metrics={},
            debug_info={
                "snapshot_id": input_data.snapshot_id,
                "base_interval_value": input_data.base_interval_value,
                "higher_interval_value": input_data.higher_interval_value,
                "strategy_boundary": "independent_signal_only",
                "implementation_status": "placeholder",
                "strategy_config": self._strategy_config,
            },
            trace_id=input_data.trace_id,
        )


__all__ = ["GannPlaceholderStrategy"]
