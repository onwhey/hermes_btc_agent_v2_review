"""Strategy runner for stage-16 independent signal execution.

This file belongs to `app/strategy`. It runs enabled strategies against one
`StrategyEvaluationInput` and isolates per-strategy failures.
It does not write databases, send Hermes, request Binance, call large language
models, read account/position state, generate final advice, or trade.
"""

from __future__ import annotations

from app.strategy.base import BaseStrategy
from app.strategy.common.result_adapter import adapt_strategy_output_to_signal
from app.strategy.registry import create_default_strategy_registry
from app.strategy.types import (
    DirectionBias,
    RiskLevel,
    StrategyEvaluationInput,
    StrategyRunStatus,
    StrategyRunnerResult,
    StrategySignal,
    StrategySignalStatus,
)


class StrategyRunner:
    """Run all enabled strategies and return independent signals."""

    def __init__(self, *, registry: object | None = None) -> None:
        self._registry = registry or create_default_strategy_registry()

    def run_strategies(self, input_data: StrategyEvaluationInput) -> StrategyRunnerResult:
        """Run enabled strategies; one strategy failure does not stop the batch."""

        if not input_data.snapshot_id or not input_data.base_klines or not input_data.higher_klines:
            return StrategyRunnerResult(
                status=StrategyRunStatus.BLOCKED,
                signals=(),
                message="策略输入不完整，策略信号运行被阻断。",
                blocked_reason="strategy_input_invalid",
            )

        strategies = tuple(self._load_strategies())
        if not strategies:
            return StrategyRunnerResult(
                status=StrategyRunStatus.BLOCKED,
                signals=(),
                message="没有启用的策略，策略信号运行被阻断。",
                blocked_reason="no_enabled_strategy",
            )

        signals = tuple(self._evaluate_strategy(strategy, input_data) for strategy in strategies)
        failed_count = sum(1 for signal in signals if signal.strategy_status == StrategySignalStatus.FAILED)
        success_like_count = sum(
            1
            for signal in signals
            if signal.strategy_status in (StrategySignalStatus.SUCCESS, StrategySignalStatus.NO_SIGNAL)
        )
        if failed_count == len(signals):
            status = StrategyRunStatus.FAILED
            message = "所有策略均运行失败。"
        elif failed_count > 0 or success_like_count < len(signals):
            status = StrategyRunStatus.PARTIAL_SUCCESS
            message = "策略信号运行完成，部分策略未输出成功信号。"
        else:
            status = StrategyRunStatus.SUCCESS
            message = "策略信号运行完成。本阶段仅输出独立策略信号，不生成交易建议。"
        return StrategyRunnerResult(status=status, signals=signals, message=message)

    def _load_strategies(self) -> tuple[BaseStrategy, ...]:
        load_enabled = getattr(self._registry, "load_enabled_strategies")
        return tuple(load_enabled())

    @staticmethod
    def _evaluate_strategy(strategy: BaseStrategy, input_data: StrategyEvaluationInput) -> StrategySignal:
        try:
            output = strategy.evaluate(input_data)
        except Exception as exc:  # noqa: BLE001 - runner isolates strategy failures.
            return StrategySignal(
                strategy_name=strategy.strategy_name or strategy.__class__.__name__,
                strategy_version=strategy.strategy_version or "unknown",
                strategy_status=StrategySignalStatus.FAILED,
                direction_bias=DirectionBias.UNKNOWN,
                risk_level=RiskLevel.UNKNOWN,
                signal_strength=0.0,
                reason_codes=("strategy_exception",),
                reason_text="策略运行异常，已隔离该策略结果，其他策略继续运行。",
                metrics={},
                debug_info={"strategy_boundary": "failure_isolated"},
                trace_id=input_data.trace_id,
                error_message=str(exc),
                validation_status="failed",
            )
        return adapt_strategy_output_to_signal(
            output,
            fallback_strategy_name=strategy.strategy_name or strategy.__class__.__name__,
            fallback_strategy_version=strategy.strategy_version or "unknown",
            trace_id=input_data.trace_id,
        )


__all__ = ["StrategyRunner"]
