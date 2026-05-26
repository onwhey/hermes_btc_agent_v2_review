"""Adapters between the 23A strategy contract and stage-16 persistence DTOs.

This file belongs to `app/strategy/common`. It validates `StrategyResult`
objects and adapts them into existing `StrategySignal` objects so the stage-16
main chain remains `signal_service -> input_builder -> runner -> repository`.
It does not access external services, read or write MySQL, read or write Redis,
send Hermes, call DeepSeek or any large language model, read private trading
state, generate final advice, modify Kline tables, or perform trading.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from app.strategy.common.constants import (
    STRATEGY_RESULT_CONTRACT_VERSION,
    VALIDATION_STATUS_FAILED,
    VALIDATION_STATUS_LEGACY_COMPATIBLE,
    VALIDATION_STATUS_PASSED,
)
from app.strategy.common.payload_tools import payload_sha256, payload_size_bytes
from app.strategy.common.result_contract import StrategyCommonResult, StrategyEvidenceItem, StrategyResult
from app.strategy.common.result_validator import StrategyResultValidationResult, validate_strategy_result
from app.strategy.types import DirectionBias, RiskLevel, StrategySignal, StrategySignalStatus


def adapt_strategy_output_to_signal(
    output: Any,
    *,
    fallback_strategy_name: str,
    fallback_strategy_version: str,
    trace_id: str,
) -> StrategySignal:
    """Adapt a strategy return value into the existing stage-16 signal DTO.

    Parameters: `output` may be a 23A `StrategyResult` or legacy
    `StrategySignal`.
    Return value: `StrategySignal` with compatibility fields populated.
    Failure scenarios: unsupported outputs become `invalid` signals instead of
    stopping the whole runner batch.
    External effects: none.
    """

    if isinstance(output, StrategyResult):
        return adapt_strategy_result_to_signal(output)
    if isinstance(output, StrategySignal):
        return adapt_legacy_signal_to_contract_signal(output)
    return StrategySignal(
        strategy_name=fallback_strategy_name,
        strategy_version=fallback_strategy_version or "unknown",
        strategy_status=StrategySignalStatus.INVALID,
        direction_bias=DirectionBias.UNKNOWN,
        risk_level=RiskLevel.UNKNOWN,
        signal_strength=0.0,
        reason_codes=("strategy_result_type_invalid",),
        reason_text="Strategy returned an unsupported result type.",
        metrics={},
        debug_info={"strategy_contract_validation": VALIDATION_STATUS_FAILED},
        trace_id=trace_id,
        error_message=f"unsupported_result_type={output.__class__.__name__}",
        validation_status=VALIDATION_STATUS_FAILED,
        validation_errors_json=(
            {
                "code": "strategy_result_type_invalid",
                "field": "strategy_result",
                "message": "unsupported strategy result type",
            },
        ),
    )


def adapt_strategy_result_to_signal(
    result: StrategyResult,
    *,
    validation: StrategyResultValidationResult | None = None,
) -> StrategySignal:
    """Validate and adapt a 23A `StrategyResult` into `StrategySignal`."""

    active_validation = validation or validate_strategy_result(result)
    if not active_validation.passed:
        return _invalid_signal_from_result(result, active_validation)

    common_payload = result.common_result.to_jsonable()
    model_material = dict(result.strategy_model_material_json)
    strategy_payload = dict(result.strategy_payload_json)
    return StrategySignal(
        strategy_name=result.strategy_name,
        strategy_version=result.strategy_version,
        strategy_status=StrategySignalStatus(result.strategy_status),
        direction_bias=_direction_bias_from_common(common_payload),
        risk_level=_risk_level_from_common(common_payload),
        signal_strength=_unit_float(common_payload.get("signal_strength", "0")),
        reason_codes=tuple(str(item) for item in common_payload.get("reason_codes", ())),
        reason_text=str(common_payload.get("reason_text", "")),
        metrics={
            "contract_version": result.contract_version,
            "strategy_role": result.strategy_role,
            "common_payload_hash": active_validation.common_payload_hash,
        },
        debug_info={
            "strategy_contract_validation": VALIDATION_STATUS_PASSED,
            "common_payload_size_bytes": payload_size_bytes(common_payload),
            "strategy_model_material_size_bytes": payload_size_bytes(model_material),
            "strategy_payload_size_bytes": payload_size_bytes(strategy_payload),
        },
        trace_id=result.trace_id,
        contract_version=result.contract_version,
        strategy_role=result.strategy_role,
        common_payload_json=common_payload,
        strategy_model_material_json=model_material,
        strategy_payload_json=strategy_payload,
        extension_payload_json=strategy_payload,
        common_payload_hash=active_validation.common_payload_hash,
        validation_status=VALIDATION_STATUS_PASSED,
        validation_errors_json=(),
    )


def adapt_legacy_signal_to_contract_signal(signal: StrategySignal) -> StrategySignal:
    """Wrap a pre-23A `StrategySignal` in a compatible common payload.

    Legacy signals remain consumable by stage 16/17 and stage 18. They are not
    treated as role-specific real strategies; their public payload is marked as
    context so the validator does not infer missing private strategy details.
    """

    common_payload = StrategyCommonResult(
        market_bias=signal.direction_bias.value,
        risk_level=signal.risk_level.value,
        signal_strength=str(signal.signal_strength),
        confidence_score=str(signal.signal_strength),
        reason_codes=tuple(signal.reason_codes),
        reason_text=signal.reason_text,
        evidence_items=(
            StrategyEvidenceItem(
                evidence_type="legacy_stage16_signal",
                direction=signal.direction_bias.value,
                strength=str(signal.signal_strength),
                description=signal.reason_text,
                source=signal.strategy_name,
            ),
        )
        if signal.reason_text
        else (),
        observation_window={},
        context_summary=signal.reason_text,
        not_trading_advice=True,
    ).to_jsonable()
    return StrategySignal(
        strategy_name=signal.strategy_name,
        strategy_version=signal.strategy_version,
        strategy_status=signal.strategy_status,
        direction_bias=signal.direction_bias,
        risk_level=signal.risk_level,
        signal_strength=signal.signal_strength,
        reason_codes=signal.reason_codes,
        reason_text=signal.reason_text,
        metrics=signal.metrics,
        debug_info=signal.debug_info,
        trace_id=signal.trace_id,
        error_message=signal.error_message,
        contract_version=STRATEGY_RESULT_CONTRACT_VERSION,
        strategy_role="context",
        common_payload_json=common_payload,
        strategy_model_material_json={},
        strategy_payload_json={},
        extension_payload_json={},
        common_payload_hash=payload_sha256(common_payload),
        validation_status=VALIDATION_STATUS_LEGACY_COMPATIBLE,
        validation_errors_json=(),
    )


def _invalid_signal_from_result(
    result: StrategyResult,
    validation: StrategyResultValidationResult,
) -> StrategySignal:
    return StrategySignal(
        strategy_name=result.strategy_name or "unknown",
        strategy_version=result.strategy_version or "unknown",
        strategy_status=StrategySignalStatus.INVALID,
        direction_bias=DirectionBias.UNKNOWN,
        risk_level=RiskLevel.UNKNOWN,
        signal_strength=0.0,
        reason_codes=("strategy_result_contract_invalid",),
        reason_text="Strategy result contract validation failed.",
        metrics={"contract_version": result.contract_version, "strategy_role": result.strategy_role},
        debug_info={"strategy_contract_validation": VALIDATION_STATUS_FAILED},
        trace_id=result.trace_id,
        error_message="; ".join(issue.message for issue in validation.issues),
        contract_version=result.contract_version,
        strategy_role=result.strategy_role,
        common_payload_json={},
        strategy_model_material_json={},
        strategy_payload_json={},
        extension_payload_json={},
        validation_status=VALIDATION_STATUS_FAILED,
        validation_errors_json=validation.errors_json(),
    )


def _direction_bias_from_common(payload: Mapping[str, Any]) -> DirectionBias:
    value = str(payload.get("market_bias", "unknown"))
    if value == "wait":
        value = DirectionBias.NEUTRAL.value
    try:
        return DirectionBias(value)
    except ValueError:
        return DirectionBias.UNKNOWN


def _risk_level_from_common(payload: Mapping[str, Any]) -> RiskLevel:
    value = str(payload.get("risk_level", "unknown"))
    try:
        return RiskLevel(value)
    except ValueError:
        return RiskLevel.UNKNOWN


def _unit_float(value: Any) -> float:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0
    if parsed < Decimal("0"):
        parsed = Decimal("0")
    if parsed > Decimal("1"):
        parsed = Decimal("1")
    return float(parsed)


__all__ = [
    "adapt_legacy_signal_to_contract_signal",
    "adapt_strategy_output_to_signal",
    "adapt_strategy_result_to_signal",
]
