"""Pure evidence checks for the 26B strategy evidence quality gate.

本文件属于 `app/strategy/evidence_quality` 模块。
本文件负责 26B 证据质量纯判断：解析 public common payload、检查 active 策略
状态、required role/provides、SSR/SEA scope 等。本文件不访问数据库，不发送
Hermes，不请求 Binance，不读写 Redis，不调用 DeepSeek 或其他大模型，不读取账户
或仓位，不生成订单，不自动交易。
主要被 `service.py` 调用。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from app.strategy.evidence_quality.types import (
    STRATEGY_EVIDENCE_QUALITY_TRIGGER_PIPELINE,
    STRATEGY_EVIDENCE_QUALITY_VERSION,
    NormalOperatingStrategyDefinition,
    StrategyEvidenceQualityCheckIssue,
    StrategyEvidenceQualityGateRequest,
    StrategyEvidenceQualityGateResult,
    StrategyEvidenceQualityPersistencePayload,
    StrategyEvidenceQualitySeverity,
)

SUCCESS_STATUSES = {"success", "partial_success"}
FAILED_OR_INVALID_STATUSES = {"failed", "invalid"}


def evaluate_active_strategy_result(
    *,
    definition: NormalOperatingStrategyDefinition,
    row: Any | None,
    required_role_provides: Mapping[str, tuple[str, ...]],
    expected_run_id: str,
) -> tuple[dict[str, Any], tuple[StrategyEvidenceQualityCheckIssue, ...]]:
    """Evaluate one active strategy row without database or external effects."""

    failures: list[StrategyEvidenceQualityCheckIssue] = []
    if row is None:
        failures.append(
            issue(
                "active_strategy_result_missing",
                f"active 策略 {definition.strategy_name} 本轮证据缺失。",
                strategy_name=definition.strategy_name,
                strategy_role=definition.strategy_role,
            )
        )
        return strategy_quality_row(definition, row, "missing", ()), tuple(failures)

    row_run_id = str(getattr(row, "run_id", "") or "")
    if row_run_id and row_run_id != expected_run_id:
        failures.append(
            issue(
                "strategy_result_run_mismatch",
                f"策略 {definition.strategy_name} 的 run_id 与本轮 SSR 不一致。",
                strategy_name=definition.strategy_name,
                strategy_role=definition.strategy_role,
            )
        )
    strategy_status = str(getattr(row, "strategy_status", "") or "").strip().lower()
    if strategy_status in FAILED_OR_INVALID_STATUSES:
        failures.append(
            issue(
                f"active_strategy_status_{strategy_status}",
                f"active 策略 {definition.strategy_name} strategy_status={strategy_status}。",
                strategy_name=definition.strategy_name,
                strategy_role=definition.strategy_role,
            )
        )
    validation_status = str(getattr(row, "validation_status", "") or "").strip().lower()
    if validation_status in FAILED_OR_INVALID_STATUSES:
        failures.append(
            issue(
                f"active_strategy_validation_{validation_status}",
                f"active 策略 {definition.strategy_name} validation_status={validation_status}。",
                strategy_name=definition.strategy_name,
                strategy_role=definition.strategy_role,
            )
        )
    payload, parse_error = load_common_payload(getattr(row, "common_payload_json", None))
    if parse_error:
        failures.append(
            issue(
                "common_payload_json_parse_failed",
                f"active 策略 {definition.strategy_name} common_payload_json 解析失败：{parse_error}",
                strategy_name=definition.strategy_name,
                strategy_role=definition.strategy_role,
            )
        )
        return strategy_quality_row(definition, row, "failed", ()), tuple(failures)

    required_fields = tuple(required_role_provides.get(definition.strategy_role, ()))
    missing_fields = tuple(field for field in required_fields if is_missing_value(payload.get(field)))
    for field_name in missing_fields:
        failures.append(
            issue(
                "required_field_missing",
                f"active 策略 {definition.strategy_name} 缺少核心字段 {field_name}。",
                strategy_name=definition.strategy_name,
                strategy_role=definition.strategy_role,
                field_name=field_name,
            )
        )
    return strategy_quality_row(definition, row, "failed" if failures else "passed", required_fields), tuple(failures)


def chain_scope_issues(
    *,
    request: StrategyEvidenceQualityGateRequest,
    aggregation: Any,
) -> tuple[StrategyEvidenceQualityCheckIssue, ...]:
    """Check SEA belongs to the current SSR and market scope."""

    failures: list[StrategyEvidenceQualityCheckIssue] = []
    if str(getattr(aggregation, "strategy_signal_run_id", "") or "") != request.strategy_signal_run_id:
        failures.append(issue("strategy_signal_run_id_mismatch", "23F/24 聚合结果的 strategy_signal_run_id 与本轮 SSR 不一致。"))
    if str(getattr(aggregation, "status", "") or "").strip().lower() not in SUCCESS_STATUSES:
        failures.append(
            issue(
                "strategy_evidence_aggregation_status_unusable",
                f"23F/24 聚合结果 status={getattr(aggregation, 'status', '')}，不可进入 18 材料包。",
            )
        )
    for field_name, expected in (
        ("symbol", request.symbol),
        ("base_interval", request.base_interval),
        ("higher_interval", request.higher_interval),
    ):
        actual = str(getattr(aggregation, field_name, "") or "")
        if actual and actual != expected:
            failures.append(
                issue(
                    "strategy_evidence_scope_mismatch",
                    f"23F/24 聚合结果 {field_name}={actual} 与 pipeline={expected} 不一致。",
                    field_name=field_name,
                )
            )
    return tuple(failures)


def required_role_issues(
    *,
    role_quality: Mapping[str, Any],
    required_roles: tuple[str, ...],
    required_role_provides: Mapping[str, tuple[str, ...]],
) -> tuple[StrategyEvidenceQualityCheckIssue, ...]:
    """Check 23F/24 role coverage for required roles and provides."""

    failures: list[StrategyEvidenceQualityCheckIssue] = []
    for role in required_roles:
        row = role_quality.get(role)
        if not isinstance(row, Mapping) or not bool(row.get("covered")):
            failures.append(issue("required_role_missing", f"23F/24 聚合结果缺少 required role={role} 的有效证据。", strategy_role=role))
        missing_provides = tuple(str(item) for item in as_list(row.get("missing_provides") if isinstance(row, Mapping) else ()))
        for provide in missing_provides:
            failures.append(
                issue(
                    "required_provide_missing",
                    f"23F/24 聚合结果 role={role} 缺少 required provide={provide}。",
                    strategy_role=role,
                    field_name=provide,
                )
            )
        if isinstance(row, Mapping) and not missing_provides and not bool(row.get("covered")):
            for provide in required_role_provides.get(role, ()):
                failures.append(
                    issue(
                        "required_provide_missing",
                        f"23F/24 聚合结果 role={role} 未覆盖 required provide={provide}。",
                        strategy_role=role,
                        field_name=provide,
                    )
                )
    return tuple(failures)


def role_quality_from_aggregation(aggregation: Any, *, required_roles: tuple[str, ...]) -> dict[str, Any]:
    """Extract compact role quality facts from SEA role coverage JSON."""

    role_matrix = json_mapping(getattr(aggregation, "role_coverage_matrix_json", "{}"))
    roles = role_matrix.get("roles") if isinstance(role_matrix.get("roles"), Mapping) else role_matrix
    result: dict[str, Any] = {}
    for role in required_roles:
        role_row = roles.get(role) if isinstance(roles, Mapping) else None
        result[role] = dict(role_row) if isinstance(role_row, Mapping) else {"role": role, "covered": False}
    return result


def rows_by_strategy(rows: tuple[Any, ...]) -> dict[str, Any]:
    """Return the first row per strategy name for one SSR."""

    result: dict[str, Any] = {}
    for row in rows:
        name = str(getattr(row, "strategy_name", "") or "").strip()
        if name:
            result.setdefault(name, row)
    return result


def strategy_quality_row(
    definition: NormalOperatingStrategyDefinition,
    row: Any | None,
    status: str,
    required_fields: tuple[str, ...],
) -> dict[str, Any]:
    """Build a compact per-strategy quality summary."""

    return {
        "strategy_name": definition.strategy_name,
        "strategy_role": definition.strategy_role,
        "maturity_stage": definition.maturity_stage,
        "participation_mode": definition.participation_mode,
        "decision_weight": definition.decision_weight,
        "can_veto": definition.can_veto,
        "result_present": row is not None,
        "strategy_status": str(getattr(row, "strategy_status", "") or "") if row is not None else "",
        "validation_status": str(getattr(row, "validation_status", "") or "") if row is not None else "",
        "required_fields": list(required_fields),
        "quality_status": status,
    }


def persistence_payload_from_result(
    result: StrategyEvidenceQualityGateResult,
) -> StrategyEvidenceQualityPersistencePayload:
    """Build repository payload from a service result."""

    details = dict(result.details)
    return StrategyEvidenceQualityPersistencePayload(
        quality_check_id=result.quality_check_id,
        pipeline_run_id=result.pipeline_run_id,
        strategy_signal_run_id=result.strategy_signal_run_id,
        evidence_aggregation_id=result.strategy_evidence_aggregation_id,
        symbol=result.symbol,
        base_interval=result.base_interval,
        higher_interval=result.higher_interval,
        kline_slot_utc=result.kline_slot_utc,
        status=result.status.value,
        severity=result.severity.value,
        should_block_pipeline=result.should_block_pipeline,
        error_code=result.error_code,
        error_message=result.error_message,
        failed_checks=tuple(item.to_dict() for item in result.failed_checks),
        warning_checks=tuple(item.to_dict() for item in result.warning_checks),
        strategy_quality=dict(details.get("strategy_quality", {})),
        role_quality=dict(details.get("role_quality", {})),
        config_snapshot=dict(details.get("config_snapshot", {})),
        alert_required=result.alert_required,
        alert_status=result.alert_status,
        alert_message_id=result.alert_message_id,
        not_trading_advice=result.not_trading_advice,
        trigger_source=STRATEGY_EVIDENCE_QUALITY_TRIGGER_PIPELINE,
        trace_id=result.trace_id,
    )


def config_snapshot(
    *,
    active_strategies: tuple[NormalOperatingStrategyDefinition, ...],
    required_roles: tuple[str, ...],
    required_role_provides: Mapping[str, tuple[str, ...]],
    gate_enabled: bool,
    alert_enabled: bool,
) -> dict[str, Any]:
    """Build a compact config snapshot for 26B audit rows."""

    return {
        "version": STRATEGY_EVIDENCE_QUALITY_VERSION,
        "gate_enabled": gate_enabled,
        "alert_enabled": alert_enabled,
        "normal_operating_strategy_count": len(active_strategies),
        "normal_operating_strategies": [item.strategy_name for item in active_strategies],
        "required_roles": list(required_roles),
        "required_role_provides": {role: list(provides) for role, provides in required_role_provides.items()},
        "normal_operating_strategy_rule": "enabled=true AND maturity_stage=active AND (participation_mode=decision_participant OR can_veto=true)",
    }


def load_common_payload(value: Any) -> tuple[dict[str, Any], str | None]:
    """Parse public `common_payload_json` into a mapping."""

    if isinstance(value, Mapping):
        return dict(value), None
    if value is None:
        return {}, "common_payload_json is empty"
    text = str(value).strip()
    if not text:
        return {}, "common_payload_json is empty"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, str(exc)
    if not isinstance(parsed, Mapping):
        return {}, "common_payload_json is not an object"
    return dict(parsed), None


def json_mapping(value: Any) -> dict[str, Any]:
    """Parse a JSON mapping, returning `{}` on malformed values."""

    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def issue(
    error_code: str,
    reason: str,
    *,
    strategy_name: str | None = None,
    strategy_role: str | None = None,
    field_name: str | None = None,
    severity: str = StrategyEvidenceQualitySeverity.CRITICAL.value,
) -> StrategyEvidenceQualityCheckIssue:
    """Build one compact quality issue."""

    return StrategyEvidenceQualityCheckIssue(
        error_code=error_code,
        reason=reason,
        strategy_name=strategy_name,
        strategy_role=strategy_role,
        field_name=field_name,
        severity=severity,
    )


def build_error_message(failed_checks: tuple[StrategyEvidenceQualityCheckIssue, ...]) -> str:
    """Build the Chinese pipeline error summary."""

    preview = "；".join(item.reason for item in failed_checks[:5])
    suffix = f"；另有 {len(failed_checks) - 5} 项异常" if len(failed_checks) > 5 else ""
    return f"策略证据质量重大异常：{preview}{suffix}。已阻断 18 材料包。"


def field_label(item: StrategyEvidenceQualityCheckIssue) -> str | None:
    """Return `role.field` for missing field summaries."""

    if not item.field_name:
        return None
    if item.strategy_role:
        return f"{item.strategy_role}.{item.field_name}"
    return item.field_name


def is_missing_value(value: Any) -> bool:
    """Return whether a public evidence field is effectively missing."""

    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def as_list(value: Any) -> list[Any]:
    """Normalize a scalar/tuple/list to a list."""

    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    return [value]


def unique_non_empty(values: Any) -> tuple[str, ...]:
    """Return unique non-empty strings while preserving order."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return tuple(result)


def existing_quality_check_id(existing_quality: Any | None) -> str | None:
    """Return existing quality_check_id when an idempotent row exists."""

    value = getattr(existing_quality, "quality_check_id", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def alert_status(alert_result: Any) -> str:
    """Return normalized alert status from the alert dispatcher result."""

    return str(getattr(alert_result, "alert_status", "") or "submit_failed")


def alert_message_id(alert_result: Any) -> int | None:
    """Return normalized alert_message_id from the alert dispatcher result."""

    return int_or_none(getattr(alert_result, "alert_message_id", None))


def int_or_none(value: Any) -> int | None:
    """Return an int or None from optional scalar values."""

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "alert_message_id",
    "alert_status",
    "build_error_message",
    "chain_scope_issues",
    "config_snapshot",
    "evaluate_active_strategy_result",
    "existing_quality_check_id",
    "field_label",
    "int_or_none",
    "issue",
    "persistence_payload_from_result",
    "required_role_issues",
    "role_quality_from_aggregation",
    "rows_by_strategy",
    "unique_non_empty",
]
