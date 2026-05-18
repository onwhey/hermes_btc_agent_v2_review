"""Hermes visible-body formatter for stage-18 aggregation notifications.

This file belongs to `app/strategy/aggregation`. It formats a compact
operator-facing message for Hermes after the aggregation service has produced
and persisted a result.

Called by: `app/strategy/aggregation/service.py`.

External services: none. MySQL: none. Redis: none. Hermes: this file only
formats text; it does not send. DeepSeek/large models: none. Trading execution:
none. The message explicitly states that candidate_direction is not a final
suggestion and no automatic trading happened.
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
        "BTC strategy aggregation result"
        if result.status in (StrategyAggregationStatus.SUCCESS, StrategyAggregationStatus.PARTIAL_SUCCESS)
        else "BTC strategy aggregation abnormal"
    )
    lines = [
        f"[{title}]",
        f"status: {result.status.value}",
        f"candidate_direction: {result.candidate_direction.value if result.candidate_direction else ''}",
        f"risk_level: {result.risk_level.value if result.risk_level else ''}",
        f"risk_gate_status: {result.risk_gate_status.value if result.risk_gate_status else ''}",
        f"conflict_level: {result.conflict_level.value if result.conflict_level else ''}",
        f"effective_strategy_count: {result.effective_strategy_count}",
        f"aggregation_run_id: {result.aggregation_run_id}",
        f"material_pack_id: {result.material_pack_id or ''}",
        f"strategy_signal_run_id: {result.strategy_signal_run_id}",
        f"snapshot_id: {result.snapshot_id or ''}",
        f"trace_id: {result.trace_id}",
    ]
    if result.error_message:
        lines.append(f"reason: {_compact_text(result.error_message, max_length=260)}")
    message = getattr(aggregation_row, "message", "") or result.message
    if message:
        lines.append(f"message: {_compact_text(str(message), max_length=260)}")
    lines.extend(
        [
            "Boundary: this is a strategy aggregation candidate, not final trading advice.",
            "Stage 18 did not call a large model, did not enter the advice lifecycle, and did not auto-trade.",
            "candidate_direction is only a candidate direction for later analysis layers.",
        ]
    )
    return "\n".join(lines)


def _compact_text(value: str, *, max_length: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."


__all__ = ["build_strategy_aggregation_visible_body"]
