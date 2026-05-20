"""Token usage and cost helpers for stage-19B model analysis.

This file belongs to `app/model_analysis`. It converts provider usage metadata
into compact, auditable token and cost fields.

Called by `app/model_analysis/service.py`. External services: none. MySQL:
none. Redis: none. Hermes: none. DeepSeek: none in this file. Trading
execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from app.model_analysis.model_profile import ModelProfile


@dataclass(frozen=True)
class CostEstimate:
    """Compact token and cost estimate for one provider call."""

    input_token_count: int | None
    output_token_count: int | None
    total_token_count: int | None
    estimated_cost: str | None
    cost_currency: str | None
    provider_usage_json: Mapping[str, Any]


def estimate_provider_call_cost(*, profile: ModelProfile, usage: Mapping[str, Any]) -> CostEstimate:
    """Estimate cost from provider usage and profile cost policy.

    If provider usage is missing, the result explicitly marks it as missing
    and leaves cost empty instead of pretending precision.
    """

    if not usage:
        return CostEstimate(
            input_token_count=None,
            output_token_count=None,
            total_token_count=None,
            estimated_cost=None,
            cost_currency=str(profile.cost_policy.get("currency") or ""),
            provider_usage_json={"usage_missing": True, "estimated": False},
        )
    input_tokens = _int_or_none(
        usage.get("prompt_tokens")
        if "prompt_tokens" in usage
        else usage.get("input_tokens")
    )
    output_tokens = _int_or_none(
        usage.get("completion_tokens")
        if "completion_tokens" in usage
        else usage.get("output_tokens")
    )
    total_tokens = _int_or_none(usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    estimated_cost = _estimate_cost(profile, input_tokens=input_tokens, output_tokens=output_tokens)
    usage_summary = dict(usage)
    usage_summary["usage_missing"] = False
    usage_summary["estimated"] = estimated_cost is not None
    return CostEstimate(
        input_token_count=input_tokens,
        output_token_count=output_tokens,
        total_token_count=total_tokens,
        estimated_cost=estimated_cost,
        cost_currency=str(profile.cost_policy.get("currency") or ""),
        provider_usage_json=usage_summary,
    )


def _estimate_cost(
    profile: ModelProfile,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
) -> str | None:
    if input_tokens is None or output_tokens is None:
        return None
    input_price = _decimal_or_none(profile.cost_policy.get("input_token_price"))
    output_price = _decimal_or_none(profile.cost_policy.get("output_token_price"))
    if input_price is None or output_price is None:
        return None
    cost = (Decimal(input_tokens) / Decimal(1_000_000) * input_price) + (
        Decimal(output_tokens) / Decimal(1_000_000) * output_price
    )
    return str(cost.quantize(Decimal("0.00000001")))


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


__all__ = ["CostEstimate", "estimate_provider_call_cost"]
