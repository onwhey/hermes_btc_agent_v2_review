"""Strategy runner for stage-16 independent signal execution.

This file belongs to `app/strategy`. It runs enabled strategies against one
`StrategyEvaluationInput` and isolates per-strategy failures.
It does not write databases, send Hermes, request Binance, call large language
models, read account/position state, generate final advice, or trade.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.strategy.base import BaseStrategy
from app.strategy.common.result_adapter import adapt_strategy_output_to_signal
from app.strategy.evidence_context import EvidenceContext
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

        ordered_strategies = _order_strategies_for_public_dependencies(strategies)
        evidence_context = EvidenceContext.empty()
        signals: list[StrategySignal] = []
        for strategy in ordered_strategies:
            signal = self._evaluate_strategy(strategy, input_data, evidence_context)
            signals.append(signal)
            evidence_context = evidence_context.with_signal(signal)
        signals = tuple(signals)
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
    def _evaluate_strategy(
        strategy: BaseStrategy,
        input_data: StrategyEvaluationInput,
        evidence_context: EvidenceContext,
    ) -> StrategySignal:
        try:
            evaluate_with_evidence = getattr(strategy, "evaluate_with_evidence", None)
            if callable(evaluate_with_evidence):
                output = evaluate_with_evidence(input_data, evidence_context)
            else:
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


def _order_strategies_for_public_dependencies(strategies: tuple[BaseStrategy, ...]) -> tuple[BaseStrategy, ...]:
    """Order strategies so public evidence providers run before consumers."""

    ordered: list[BaseStrategy] = []
    provided: set[tuple[str, str]] = set()
    pending = list(strategies)
    while pending:
        progressed = False
        remaining: list[BaseStrategy] = []
        for strategy in pending:
            requirements = _strategy_requirements(strategy)
            if not requirements or all(requirement in provided for requirement in requirements):
                ordered.append(strategy)
                provided.update(_strategy_provides(strategy))
                progressed = True
            else:
                remaining.append(strategy)
        if not progressed:
            ordered.extend(remaining)
            break
        pending = remaining
    return tuple(ordered)


def _strategy_provides(strategy: BaseStrategy) -> set[tuple[str, str]]:
    role = str(getattr(strategy, "strategy_role", "") or "")
    provides = tuple(str(item) for item in getattr(strategy, "provides", ()) or ())
    return {(role, item) for item in provides if role and item}


def _strategy_requirements(strategy: BaseStrategy) -> set[tuple[str, str]]:
    requirements = getattr(strategy, "requires", ()) or ()
    result: set[tuple[str, str]] = set()
    for requirement in requirements:
        parsed = _parse_requirement(requirement)
        if parsed is not None:
            result.add(parsed)
    return result


def _parse_requirement(requirement: Any) -> tuple[str, str] | None:
    if isinstance(requirement, Mapping):
        role = str(requirement.get("role", "") or "")
        provides = str(requirement.get("provides", "") or "")
        return (role, provides) if role and provides else None
    if isinstance(requirement, str):
        parts = dict(
            tuple(part.strip() for part in token.split("=", 1))
            for token in requirement.split(",")
            if "=" in token
        )
        role = str(parts.get("role", ""))
        provides = str(parts.get("provides", ""))
        return (role, provides) if role and provides else None
    return None


__all__ = ["StrategyRunner"]
