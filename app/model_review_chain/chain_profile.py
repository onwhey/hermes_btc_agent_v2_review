"""Static mock chain profiles for stage-20B.

This file belongs to `app/model_review_chain`. It resolves a chain key into a
finite list of mock step definitions. It does not call provider clients, read
or write databases, touch Redis, send Hermes, connect scheduler, or produce
trading advice.
"""

from __future__ import annotations

from app.model_review_chain.schema import (
    DEFAULT_CHAIN_KEY,
    MOCK_CHAIN_PROFILE_VERSION,
    ChainProfile,
    ChainStepDefinition,
)


def resolve_chain_profile(chain_key: str) -> ChainProfile | None:
    """Return the configured mock chain profile for `chain_key`.

    Parameters: `chain_key` is supplied by the CLI/service request.
    Return value: a static `ChainProfile`, or `None` when the key is unknown.
    Failure scenarios: no external errors; unknown keys are handled by the
    service as blocked requests.
    External effects: none; this method never calls a real model provider.
    """

    normalized_key = (chain_key or "").strip()
    if normalized_key != DEFAULT_CHAIN_KEY:
        return None
    return ChainProfile(
        chain_key=DEFAULT_CHAIN_KEY,
        chain_profile_version=MOCK_CHAIN_PROFILE_VERSION,
        steps=(
            ChainStepDefinition(
                step_no=1,
                model_key="mock_deepseek_structure_review",
                model_role="structure_review",
            ),
            ChainStepDefinition(
                step_no=2,
                model_key="mock_gpt_risk_review",
                model_role="risk_review",
            ),
        ),
    )


__all__ = ["resolve_chain_profile"]
