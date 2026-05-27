"""Validator for the stage-23A strategy result contract.

This file belongs to `app/strategy/common`. It validates public strategy result
shape, JSON serializability, schema versions, size limits, hashes, and common
field legality before persistence.
It does not access external services, read or write MySQL, read or write Redis,
send Hermes, call DeepSeek or any large language model, read private trading
state, generate final advice, modify Kline tables, or perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from app.strategy.common.constants import (
    ALLOWED_FILTER_STATUSES,
    ALLOWED_FILTER_DECISIONS,
    ALLOWED_KEY_LEVEL_TYPES,
    ALLOWED_MARKET_BIASES,
    ALLOWED_RISK_LEVELS,
    ALLOWED_SCENARIO_TYPES,
    ALLOWED_STRATEGY_ROLES,
    ALLOWED_STRATEGY_STATUSES,
    ALLOWED_TRIGGER_STATES,
    ALLOWED_VOLUME_CONFIRMATIONS,
    ALLOWED_VOLUME_STATES,
    FORBIDDEN_PUBLIC_PAYLOAD_TOKENS,
    MAX_COMMON_PAYLOAD_BYTES,
    MAX_STRATEGY_MODEL_MATERIAL_BYTES,
    MAX_STRATEGY_PAYLOAD_BYTES,
    STRATEGY_COMMON_RESULT_SCHEMA_VERSION,
    STRATEGY_RESULT_CONTRACT_VERSION,
    VALIDATION_STATUS_FAILED,
    VALIDATION_STATUS_PASSED,
)
from app.strategy.common.payload_tools import (
    ensure_json_mapping,
    payload_sha256,
    payload_size_bytes,
)
from app.strategy.common.result_contract import StrategyResult


@dataclass(frozen=True)
class StrategyResultValidationIssue:
    """One validation issue for a strategy result."""

    code: str
    field: str
    message: str

    def to_jsonable(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class StrategyResultValidationResult:
    """Validation outcome including hashes for bounded JSON payloads."""

    status: str
    issues: tuple[StrategyResultValidationIssue, ...]
    common_payload_hash: str | None = None
    strategy_model_material_hash: str | None = None
    strategy_payload_hash: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == VALIDATION_STATUS_PASSED

    def errors_json(self) -> tuple[Mapping[str, str], ...]:
        return tuple(issue.to_jsonable() for issue in self.issues)


class StrategyResultValidator:
    """Validate public contract fields without interpreting private payloads."""

    def validate_strategy_result(self, result: StrategyResult) -> StrategyResultValidationResult:
        """Validate one strategy result and return hashes for valid payloads."""

        issues: list[StrategyResultValidationIssue] = []
        common_payload = _safe_common_payload(result, issues)
        strategy_model_material = _safe_private_mapping(
            result.strategy_model_material_json,
            field="strategy_model_material_json",
            issues=issues,
        )
        strategy_payload = _safe_private_mapping(
            result.strategy_payload_json,
            field="strategy_payload_json",
            issues=issues,
        )

        _validate_top_level(result, issues)
        if common_payload is not None:
            _validate_common_payload(result, common_payload, issues)
            _validate_role_rules(result, common_payload, issues)
            _validate_public_words(common_payload, issues)
            _validate_payload_size(
                common_payload,
                field="common_result",
                max_bytes=MAX_COMMON_PAYLOAD_BYTES,
                issues=issues,
            )
        if strategy_model_material is not None:
            _validate_payload_size(
                strategy_model_material,
                field="strategy_model_material_json",
                max_bytes=MAX_STRATEGY_MODEL_MATERIAL_BYTES,
                issues=issues,
            )
        if strategy_payload is not None:
            _validate_payload_size(
                strategy_payload,
                field="strategy_payload_json",
                max_bytes=MAX_STRATEGY_PAYLOAD_BYTES,
                issues=issues,
            )

        if issues:
            return StrategyResultValidationResult(
                status=VALIDATION_STATUS_FAILED,
                issues=tuple(issues),
            )
        return StrategyResultValidationResult(
            status=VALIDATION_STATUS_PASSED,
            issues=(),
            common_payload_hash=payload_sha256(common_payload),
            strategy_model_material_hash=payload_sha256(strategy_model_material),
            strategy_payload_hash=payload_sha256(strategy_payload),
        )


def validate_strategy_result(result: StrategyResult) -> StrategyResultValidationResult:
    """Convenience function used by tests and adapters."""

    return StrategyResultValidator().validate_strategy_result(result)


def _safe_common_payload(
    result: StrategyResult,
    issues: list[StrategyResultValidationIssue],
) -> Mapping[str, Any] | None:
    try:
        return result.common_result.to_jsonable()
    except Exception as exc:  # noqa: BLE001 - validation reports structured failures.
        issues.append(_issue("common_payload_not_jsonable", "common_result", str(exc)))
        return None


def _safe_private_mapping(
    payload: Mapping[str, Any] | None,
    *,
    field: str,
    issues: list[StrategyResultValidationIssue],
) -> Mapping[str, Any] | None:
    try:
        return ensure_json_mapping(payload)
    except Exception as exc:  # noqa: BLE001
        issues.append(_issue("payload_not_jsonable", field, str(exc)))
        return None


def _validate_top_level(result: StrategyResult, issues: list[StrategyResultValidationIssue]) -> None:
    if result.contract_version != STRATEGY_RESULT_CONTRACT_VERSION:
        issues.append(_issue("contract_version_invalid", "contract_version", "unsupported contract version"))
    if not result.strategy_name.strip():
        issues.append(_issue("strategy_name_required", "strategy_name", "strategy_name is required"))
    if not result.strategy_version.strip():
        issues.append(_issue("strategy_version_required", "strategy_version", "strategy_version is required"))
    if result.strategy_role not in ALLOWED_STRATEGY_ROLES:
        issues.append(_issue("strategy_role_invalid", "strategy_role", "unsupported strategy role"))
    if result.strategy_status not in ALLOWED_STRATEGY_STATUSES:
        issues.append(_issue("strategy_status_invalid", "strategy_status", "unsupported strategy status"))


def _validate_common_payload(
    result: StrategyResult,
    payload: Mapping[str, Any],
    issues: list[StrategyResultValidationIssue],
) -> None:
    if payload.get("schema_version") != STRATEGY_COMMON_RESULT_SCHEMA_VERSION:
        issues.append(_issue("schema_version_invalid", "common_result.schema_version", "unsupported schema version"))
    if payload.get("not_trading_advice") is not True:
        issues.append(_issue("not_trading_advice_required", "common_result.not_trading_advice", "must be true"))
    if str(payload.get("market_bias", "")) not in ALLOWED_MARKET_BIASES:
        issues.append(_issue("market_bias_invalid", "common_result.market_bias", "unsupported market bias"))
    if str(payload.get("risk_level", "")) not in ALLOWED_RISK_LEVELS:
        issues.append(_issue("risk_level_invalid", "common_result.risk_level", "unsupported risk level"))
    _validate_unit_interval(payload.get("signal_strength"), "common_result.signal_strength", issues)
    _validate_unit_interval(payload.get("confidence_score"), "common_result.confidence_score", issues)
    _validate_reason_codes(payload.get("reason_codes"), issues)
    _validate_key_levels(payload.get("key_levels"), issues)
    _validate_scenario_candidates(payload.get("scenario_candidates"), issues)
    _validate_risk_flags(payload.get("risk_flags"), issues)
    _validate_evidence_items(payload.get("evidence_items"), issues)
    if result.strategy_role == "filter" and payload.get("filter_status") not in ALLOWED_FILTER_STATUSES:
        issues.append(_issue("filter_status_invalid", "common_result.filter_status", "unsupported filter status"))
    if payload.get("filter_decision") is not None and payload.get("filter_decision") not in ALLOWED_FILTER_DECISIONS:
        issues.append(_issue("filter_decision_invalid", "common_result.filter_decision", "unsupported filter decision"))
    if payload.get("trigger_state") is not None and payload.get("trigger_state") not in ALLOWED_TRIGGER_STATES:
        issues.append(_issue("trigger_state_invalid", "common_result.trigger_state", "unsupported trigger state"))
    if payload.get("volume_state") is not None and payload.get("volume_state") not in ALLOWED_VOLUME_STATES:
        issues.append(_issue("volume_state_invalid", "common_result.volume_state", "unsupported volume state"))
    if payload.get("volume_confirmation") is not None and payload.get("volume_confirmation") not in ALLOWED_VOLUME_CONFIRMATIONS:
        issues.append(
            _issue("volume_confirmation_invalid", "common_result.volume_confirmation", "unsupported volume confirmation")
        )


def _validate_role_rules(
    result: StrategyResult,
    payload: Mapping[str, Any],
    issues: list[StrategyResultValidationIssue],
) -> None:
    if result.strategy_role == "directional" and result.strategy_status == "success":
        if payload.get("market_bias") == "not_applicable":
            issues.append(_issue("directional_market_bias_required", "common_result.market_bias", "directional success needs market bias"))
        if not payload.get("reason_codes"):
            issues.append(_issue("reason_codes_required", "common_result.reason_codes", "directional success needs reason codes"))
        if not str(payload.get("reason_text", "")).strip():
            issues.append(_issue("reason_text_required", "common_result.reason_text", "directional success needs reason text"))
        scenarios = payload.get("scenario_candidates")
        if not isinstance(scenarios, list) or not scenarios:
            issues.append(
                _issue("scenario_candidates_required", "common_result.scenario_candidates", "directional success needs scenarios")
            )
        else:
            for index, item in enumerate(scenarios):
                _validate_directional_scenario(item, index, issues)

    if result.strategy_role == "support_resistance" and result.strategy_status == "success":
        if not payload.get("key_levels"):
            issues.append(_issue("key_levels_required", "common_result.key_levels", "support/resistance success needs key levels"))
        if not str(payload.get("reason_text", "")).strip():
            issues.append(_issue("reason_text_required", "common_result.reason_text", "support/resistance success needs reason text"))

    if result.strategy_role == "risk_control" and result.strategy_status == "success":
        if not payload.get("risk_flags"):
            issues.append(_issue("risk_flags_required", "common_result.risk_flags", "risk-control success needs risk flags"))
        if payload.get("risk_level") in {"", None, "not_applicable"}:
            issues.append(_issue("risk_level_required", "common_result.risk_level", "risk-control success needs risk level"))

    if result.strategy_role == "context" and result.strategy_status == "success":
        if not str(payload.get("reason_text", "")).strip():
            issues.append(_issue("reason_text_required", "common_result.reason_text", "context success needs reason text"))
        if not payload.get("evidence_items") and not str(payload.get("context_summary", "")).strip():
            issues.append(_issue("context_evidence_required", "common_result.evidence_items", "context success needs evidence or summary"))

    if result.strategy_role == "placeholder":
        if result.strategy_status != "not_implemented":
            issues.append(_issue("placeholder_status_invalid", "strategy_status", "placeholder must be not_implemented"))
        for field_name in ("key_levels", "scenario_candidates", "risk_flags", "evidence_items"):
            if payload.get(field_name):
                issues.append(_issue("placeholder_payload_not_empty", f"common_result.{field_name}", "placeholder must not fake analysis"))


def _validate_reason_codes(value: Any, issues: list[StrategyResultValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(_issue("reason_codes_invalid", "common_result.reason_codes", "reason_codes must be a list"))
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(_issue("reason_code_invalid", f"common_result.reason_codes[{index}]", "reason code must be a non-empty string"))


def _validate_key_levels(value: Any, issues: list[StrategyResultValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(_issue("key_levels_invalid", "common_result.key_levels", "key_levels must be a list"))
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            issues.append(_issue("key_level_invalid", f"common_result.key_levels[{index}]", "key level must be an object"))
            continue
        if item.get("level_type") not in ALLOWED_KEY_LEVEL_TYPES:
            issues.append(_issue("key_level_type_invalid", f"common_result.key_levels[{index}].level_type", "unsupported key level type"))


def _validate_scenario_candidates(value: Any, issues: list[StrategyResultValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(_issue("scenario_candidates_invalid", "common_result.scenario_candidates", "scenarios must be a list"))
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            issues.append(_issue("scenario_candidate_invalid", f"common_result.scenario_candidates[{index}]", "scenario must be an object"))
            continue
        if item.get("scenario_type") not in ALLOWED_SCENARIO_TYPES:
            issues.append(
                _issue("scenario_type_invalid", f"common_result.scenario_candidates[{index}].scenario_type", "unsupported scenario type")
            )
        if str(item.get("direction_bias", "not_applicable")) not in ALLOWED_MARKET_BIASES:
            issues.append(
                _issue("scenario_direction_invalid", f"common_result.scenario_candidates[{index}].direction_bias", "unsupported scenario bias")
            )


def _validate_directional_scenario(
    item: Any,
    index: int,
    issues: list[StrategyResultValidationIssue],
) -> None:
    if not isinstance(item, Mapping):
        return
    for field_name in ("activation_condition", "invalidation_condition", "risk_boundary"):
        if not str(item.get(field_name, "")).strip():
            issues.append(
                _issue("directional_scenario_field_required", f"common_result.scenario_candidates[{index}].{field_name}", "field is required")
            )
    try:
        observation_bars = int(item.get("observation_period_bars"))
    except (TypeError, ValueError):
        observation_bars = 0
    if observation_bars <= 0:
        issues.append(
            _issue(
                "observation_period_invalid",
                f"common_result.scenario_candidates[{index}].observation_period_bars",
                "observation_period_bars must be greater than 0",
            )
        )


def _validate_risk_flags(value: Any, issues: list[StrategyResultValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(_issue("risk_flags_invalid", "common_result.risk_flags", "risk_flags must be a list"))
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            issues.append(_issue("risk_flag_invalid", f"common_result.risk_flags[{index}]", "risk flag must be an object"))
            continue
        if str(item.get("risk_level", "")) not in ALLOWED_RISK_LEVELS:
            issues.append(_issue("risk_flag_level_invalid", f"common_result.risk_flags[{index}].risk_level", "unsupported risk level"))
        if not isinstance(item.get("triggered"), bool):
            issues.append(_issue("risk_flag_trigger_invalid", f"common_result.risk_flags[{index}].triggered", "triggered must be boolean"))


def _validate_evidence_items(value: Any, issues: list[StrategyResultValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(_issue("evidence_items_invalid", "common_result.evidence_items", "evidence_items must be a list"))
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            issues.append(_issue("evidence_item_invalid", f"common_result.evidence_items[{index}]", "evidence item must be an object"))


def _validate_unit_interval(
    value: Any,
    field: str,
    issues: list[StrategyResultValidationIssue],
) -> None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        issues.append(_issue("number_invalid", field, "value must be numeric"))
        return
    if parsed < Decimal("0") or parsed > Decimal("1"):
        issues.append(_issue("number_out_of_range", field, "value must be between 0 and 1"))


def _validate_payload_size(
    payload: Mapping[str, Any],
    *,
    field: str,
    max_bytes: int,
    issues: list[StrategyResultValidationIssue],
) -> None:
    try:
        size = payload_size_bytes(payload)
    except TypeError as exc:
        issues.append(_issue("payload_not_jsonable", field, str(exc)))
        return
    if size > max_bytes:
        issues.append(_issue("payload_too_large", field, f"payload size {size} exceeds {max_bytes} bytes"))


def _validate_public_words(payload: Mapping[str, Any], issues: list[StrategyResultValidationIssue]) -> None:
    for path, text in _iter_public_text_values(payload):
        lowered = text.lower()
        for token in FORBIDDEN_PUBLIC_PAYLOAD_TOKENS:
            if token in lowered:
                issues.append(_issue("forbidden_public_token", path, f"forbidden token: {token}"))


def _iter_public_text_values(value: Any, *, path: str = "common_result") -> tuple[tuple[str, str], ...]:
    items: list[tuple[str, str]] = []
    if isinstance(value, str):
        items.append((path, value))
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            items.extend(_iter_public_text_values(nested, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            items.extend(_iter_public_text_values(nested, path=f"{path}[{index}]"))
    return tuple(items)


def _issue(code: str, field: str, message: str) -> StrategyResultValidationIssue:
    return StrategyResultValidationIssue(code=code, field=field, message=message)


__all__ = [
    "StrategyResultValidationIssue",
    "StrategyResultValidationResult",
    "StrategyResultValidator",
    "validate_strategy_result",
]
