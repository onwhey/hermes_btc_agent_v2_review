"""Conditional setup builder for stage-21A strategy advice.

This file belongs to `app/strategy_advice`. It creates bounded setup payloads
only when risk gates permit conditional human review. A setup is not an order,
not an automatic trading signal, and not an executable instruction.

Called by `app/strategy_advice/service.py`. External services: none. MySQL:
none in this file. Redis: none. Hermes: none. Large-model calls: none. Trading
execution: none.
"""

from __future__ import annotations

from typing import Any

from app.strategy_advice.id_utils import build_strategy_advice_setup_id
from app.strategy_advice.lifecycle import AdviceCandidate
from app.strategy_advice.schema import AdviceAction, DirectionalBias, StrategyAdviceTradeSetupPersistencePayload
from app.strategy_advice.schema import TradePermission, load_json_text


def build_trade_setup_payloads(
    *,
    advice_id: str,
    candidate: AdviceCandidate,
    aggregation_row: Any,
) -> tuple[StrategyAdviceTradeSetupPersistencePayload, ...]:
    """Return zero or one basic setup payload for a safe conditional advice.

    Parameters: target `advice_id`, candidate, and source aggregation row.
    Return value: setup payload tuple.
    Failure scenarios: malformed upstream JSON is treated as empty.
    External effects: none; caller decides whether to persist.
    """

    if candidate.risk_blocked:
        return ()
    if candidate.advice_action != AdviceAction.CONDITIONAL_TRADE:
        return ()
    if candidate.trade_permission != TradePermission.CONDITIONALLY_ALLOWED:
        return ()
    setup_rank = 1
    side = _side_for_bias(candidate.directional_bias)
    return (
        StrategyAdviceTradeSetupPersistencePayload(
            setup_id=build_strategy_advice_setup_id(advice_id=advice_id, setup_rank=setup_rank),
            advice_id=advice_id,
            setup_rank=setup_rank,
            setup_type=_setup_type_for_bias(candidate.directional_bias),
            side=side,
            entry_zone_json={
                "mode": "manual_observation_only",
                "price_generated": False,
                "text": "Stage 21A does not generate entry prices; user must review the next 4h context.",
            },
            trigger_condition_json={
                "requires_human_confirmation": True,
                "text": "Consider only if the next base-interval review keeps the same direction, risk, and model status.",
            },
            invalid_condition_json={
                "text": (
                    "Invalid if stage-20 risk status deteriorates, model review becomes expired, "
                    "or strategy conflict rises to high."
                ),
            },
            stop_loss_json={
                "price_generated": False,
                "text": "Stage 21A does not generate stop-loss prices.",
            },
            target_zones_json=[],
            expiry_base_bars=3,
            permission=TradePermission.CONDITIONALLY_ALLOWED,
            source_strategy_names_json=[],
            source_model_keys_json=_source_model_keys(aggregation_row),
            status="active",
        ),
    )


def _side_for_bias(direction: DirectionalBias) -> str:
    if direction == DirectionalBias.BULLISH:
        return "long"
    if direction == DirectionalBias.BEARISH:
        return "short"
    return "neutral"


def _setup_type_for_bias(direction: DirectionalBias) -> str:
    if direction == DirectionalBias.BULLISH:
        return "conditional_long_review"
    if direction == DirectionalBias.BEARISH:
        return "conditional_short_review"
    return "conditional_neutral_review"


def _source_model_keys(aggregation_row: Any) -> list[str]:
    keys = load_json_text(getattr(aggregation_row, "invoked_model_keys_json", "[]"), [])
    if not isinstance(keys, (list, tuple)):
        return []
    return [str(item)[:120] for item in keys[:8]]


__all__ = ["build_trade_setup_payloads"]
