"""Hermes visible-body formatter for stage-18 aggregation notifications.

This file belongs to `app/strategy/aggregation`. It formats a compact
operator-facing message for Hermes after the aggregation service has produced
and persisted a result.

Called by: `app/strategy/aggregation/service.py`.

External services: none. MySQL: none. Redis: none. Hermes: this file only
formats text; it does not send. DeepSeek/large models: none. Trading execution:
none. The message explicitly states that analysis_hypothesis_direction is an analysis
hypothesis only, not a strategy signal, not a final suggestion, and no
automatic trading happened.
"""

from __future__ import annotations

from typing import Any

from app.strategy.aggregation.types import StrategyAggregationResult, StrategyAggregationStatus


def build_strategy_aggregation_visible_body(result: StrategyAggregationResult, aggregation_row: Any) -> str:
    """Build a compact visible body for a strategy aggregation notification.

    Parameters: the persisted aggregation row plus the service result.
    Return value: a short Hermes body.
    Failure scenarios: none expected; missing optional values are rendered as
    empty strings.
    External effects: no external calls and no database writes.
    """

    title = (
        "BTC 策略聚合分析假设结果"
        if result.status in (StrategyAggregationStatus.SUCCESS, StrategyAggregationStatus.PARTIAL_SUCCESS)
        else "BTC 策略聚合分析假设异常"
    )
    lines = [
        f"【标题】{title}",
        f"【摘要】第 18 阶段生成了策略聚合分析假设材料包，状态为 {result.status.value}。",
        f"【分析假设方向】{result.analysis_hypothesis_direction.value if result.analysis_hypothesis_direction else ''}",
        f"【假设语义】{result.analysis_hypothesis_semantics}",
        f"【方向来源】{result.direction_projection_source}",
        f"【停止交易假设来源】{result.stop_trading_source or ''}",
        f"【风险等级】{result.risk_level.value if result.risk_level else ''}",
        f"【风险闸门】{result.risk_gate_status.value if result.risk_gate_status else ''}",
        f"【冲突等级】{result.conflict_level.value if result.conflict_level else ''}",
        f"【有效策略数量】{result.effective_strategy_count}",
        f"【aggregation_run_id】{result.aggregation_run_id}",
        f"【material_pack_id】{result.material_pack_id or ''}",
        f"【strategy_signal_run_id】{result.strategy_signal_run_id}",
        f"【snapshot_id】{result.snapshot_id or ''}",
        f"【trace_id】{result.trace_id}",
    ]
    message = getattr(aggregation_row, "message", "") or result.message
    if result.error_message:
        lines.append(f"【原因】{_compact_text(result.error_message, max_length=260)}")
    elif message:
        lines.append(f"【原因】{_compact_text(str(message), max_length=260)}")
    if message:
        lines.append(f"【说明】{_compact_text(str(message), max_length=260)}")
    lines.extend(
        [
            "【建议动作】仅用于人工审阅第 18 阶段材料包和后续分析输入，不应据此执行交易。",
            "【边界】这是分析假设，不是策略信号。",
            "【边界】这不是最终交易建议。",
            "【边界】本阶段未调用大模型。",
            "【边界】本阶段未自动交易。",
            "【边界】analysis_hypothesis_direction 只是后续分析层使用的假设投影。",
            "【边界】is_strategy_signal=false；is_trading_advice=false；is_executable=false。",
        ]
    )
    return "\n".join(lines)


def _compact_text(value: str, *, max_length: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."


__all__ = ["build_strategy_aggregation_visible_body"]
