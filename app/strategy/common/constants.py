"""Constants for the strategy common contract layer.

This file belongs to `app/strategy/common`. It defines small protocol constants
shared by the stage-16 runner, validators, adapters, and tests.
It does not access external services, read or write MySQL, read or write Redis,
send Hermes, call DeepSeek or any large language model, read private trading
state, generate final advice, modify Kline tables, or perform trading.
"""

from __future__ import annotations

STRATEGY_RESULT_CONTRACT_VERSION = "strategy_result_contract_v1"
STRATEGY_COMMON_RESULT_SCHEMA_VERSION = "strategy_common_result_v1"

VALIDATION_STATUS_PASSED = "passed"
VALIDATION_STATUS_FAILED = "failed"
VALIDATION_STATUS_LEGACY_COMPATIBLE = "legacy_compatible"

MAX_COMMON_PAYLOAD_BYTES = 32_768
MAX_STRATEGY_MODEL_MATERIAL_BYTES = 32_768
MAX_STRATEGY_PAYLOAD_BYTES = 32_768

ALLOWED_STRATEGY_ROLES = frozenset(
    {
        "directional",
        "support_resistance",
        "risk_control",
        "filter",
        "context",
        "placeholder",
    }
)

ALLOWED_STRATEGY_STATUSES = frozenset(
    {
        "success",
        "no_signal",
        "invalid",
        "not_implemented",
        "failed",
    }
)

ALLOWED_MARKET_BIASES = frozenset(
    {
        "bullish_bias",
        "bearish_bias",
        "neutral",
        "mixed",
        "wait",
        "unknown",
        "not_applicable",
    }
)

ALLOWED_PRIMARY_REGIMES = frozenset(
    {
        "uptrend",
        "downtrend",
        "range",
        "volatile",
        "mixed",
        "insufficient_data",
        "unknown",
    }
)

ALLOWED_REGIME_PHASES = frozenset(
    {
        "trend_continuation",
        "pullback_in_uptrend",
        "countertrend_rebound",
        "range_mid_rotation",
        "range_support_rebound",
        "range_resistance_rejection",
        "breakout_attempt",
        "breakdown_attempt",
        "false_breakout",
        "transition",
        "unknown",
    }
)

ALLOWED_RISK_LEVELS = frozenset(
    {
        "low",
        "medium",
        "high",
        "extreme",
        "unknown",
        "not_applicable",
    }
)

ALLOWED_SCENARIO_TYPES = frozenset(
    {
        "long_candidate",
        "short_candidate",
        "wait",
        "risk_block",
        "observation_only",
    }
)

ALLOWED_KEY_LEVEL_TYPES = frozenset(
    {
        "support",
        "resistance",
        "range_boundary",
        "invalidation_reference",
        "trigger",
        "invalidation",
        "target_observation",
        "historical_reference",
        "reference",
    }
)

ALLOWED_FILTER_STATUSES = frozenset({"pass", "reject", "unknown"})
ALLOWED_FILTER_DECISIONS = frozenset({"passed", "blocked", "uncertain", "not_applicable"})
ALLOWED_TRIGGER_STATES = frozenset(
    {
        "breakout_attempt",
        "breakout_confirmed",
        "breakout_failed",
        "breakdown_attempt",
        "breakdown_confirmed",
        "breakdown_failed",
        "pullback_testing",
        "pullback_confirmed",
        "pullback_failed",
        "false_breakout",
        "false_breakdown",
        "no_clear_trigger",
        "insufficient_key_levels",
        "insufficient_data",
        "unknown",
    }
)
ALLOWED_VOLUME_STATES = frozenset({"expanding", "contracting", "normal", "spike", "insufficient", "unknown"})
ALLOWED_VOLUME_CONFIRMATIONS = frozenset(
    {"confirming", "weakening", "rejection_signal", "neutral", "insufficient", "unknown"}
)
ALLOWED_RISK_GATE_DECISIONS = frozenset(
    {
        "allow",
        "allow_with_caution",
        "wait",
        "block_long_candidate",
        "block_short_candidate",
        "block_current_candidate",
        "block_all_candidates",
        "insufficient_context",
        "unknown",
    }
)
ALLOWED_RISK_SCOPES = frozenset({"long_only", "short_only", "current_candidate", "all_candidates", "none", "unknown"})
ALLOWED_GLOBAL_MARKET_RISKS = frozenset({"normal", "elevated", "high", "extreme", "insufficient_data", "unknown"})
ALLOWED_CANDIDATE_RISKS = frozenset({"low", "medium", "high", "extreme", "not_applicable", "unknown"})
ALLOWED_VOLATILITY_STATES = frozenset(
    {"low_volatility", "normal_volatility", "high_volatility", "extreme_volatility", "insufficient_data", "unknown"}
)
ALLOWED_CHASE_RISKS = frozenset({"low", "medium", "high", "extreme", "unknown"})
ALLOWED_FEASIBILITIES = frozenset({"favorable", "acceptable", "poor", "invalid", "unknown", "insufficient_context"})

# These words are blocked only inside strategy public payload values. Keeping
# the list here lets the common layer reject execution-like wording while still
# not implementing any execution behavior.
FORBIDDEN_PUBLIC_PAYLOAD_TOKENS = frozenset(
    {
        "buy",
        "sell",
        "open_position",
        "close_position",
        "add_position",
        "reduce_position",
        "must_trade",
    }
)

__all__ = [
    "ALLOWED_FILTER_STATUSES",
    "ALLOWED_FILTER_DECISIONS",
    "ALLOWED_CANDIDATE_RISKS",
    "ALLOWED_CHASE_RISKS",
    "ALLOWED_FEASIBILITIES",
    "ALLOWED_GLOBAL_MARKET_RISKS",
    "ALLOWED_KEY_LEVEL_TYPES",
    "ALLOWED_MARKET_BIASES",
    "ALLOWED_PRIMARY_REGIMES",
    "ALLOWED_REGIME_PHASES",
    "ALLOWED_RISK_LEVELS",
    "ALLOWED_RISK_GATE_DECISIONS",
    "ALLOWED_RISK_SCOPES",
    "ALLOWED_SCENARIO_TYPES",
    "ALLOWED_STRATEGY_ROLES",
    "ALLOWED_STRATEGY_STATUSES",
    "ALLOWED_TRIGGER_STATES",
    "ALLOWED_VOLUME_CONFIRMATIONS",
    "ALLOWED_VOLUME_STATES",
    "ALLOWED_VOLATILITY_STATES",
    "FORBIDDEN_PUBLIC_PAYLOAD_TOKENS",
    "MAX_COMMON_PAYLOAD_BYTES",
    "MAX_STRATEGY_MODEL_MATERIAL_BYTES",
    "MAX_STRATEGY_PAYLOAD_BYTES",
    "STRATEGY_COMMON_RESULT_SCHEMA_VERSION",
    "STRATEGY_RESULT_CONTRACT_VERSION",
    "VALIDATION_STATUS_FAILED",
    "VALIDATION_STATUS_LEGACY_COMPATIBLE",
    "VALIDATION_STATUS_PASSED",
]
