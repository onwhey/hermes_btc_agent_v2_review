"""Pure 27B weak model output quality rules.

本文件属于 `app/weak_models` 模块，负责对已落库的 27A
`weak_model_result` 和 `weak_model_aggregation` 做纯内存质量检查。
本文件不读取数据库，不写数据库，不请求 Binance，不读写 Redis，不发送 Hermes，
不调用 DeepSeek/GPT/Claude，不读取账户或仓位，不生成订单，不自动交易。
主要被 `output_quality_service.py` 调用。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from app.weak_models.output_quality_types import WeakModelQualityIssue, WeakModelQualitySeverity

DIRECTIONAL_WARNING_THRESHOLD = 0.75
DIRECTIONAL_CRITICAL_THRESHOLD = 0.90
CONFIDENCE_WARNING_THRESHOLD = 0.80
CONFIDENCE_CRITICAL_THRESHOLD = 0.95


def evaluate_quality_issues(aggregation: Any | None, results: tuple[Any, ...]) -> tuple[WeakModelQualityIssue, ...]:
    """Return all 27B quality issues for one persisted 27A run."""

    issues: list[WeakModelQualityIssue] = []
    if aggregation is None:
        issues.append(
            WeakModelQualityIssue(
                error_code="weak_model_aggregation_missing",
                reason="weak_model_run 缺少 weak_model_aggregation；27B 不会自动重新聚合。",
                severity=WeakModelQualitySeverity.CRITICAL.value,
                field_name="weak_model_aggregation_id",
                expected="existing weak_model_aggregation",
            )
        )
    else:
        issues.extend(_directional_aggregation_issues(aggregation))
        issues.extend(_veto_factor_issues(aggregation))
        issues.extend(_context_summary_issues(aggregation, results))

    for row in results:
        issues.extend(_single_result_directional_issues(row))
        issues.extend(_single_result_confidence_issues(row))
        issues.extend(_single_result_risk_issues(row))
        issues.extend(_observe_only_context_pollution_issues(row))
    return tuple(issues)


def checked_model_summary(row: Any) -> dict[str, Any]:
    """Return bounded checked-model metadata without raw outputs."""

    return {
        "model_key": _text(row, "model_key"),
        "model_role": _text(row, "model_role"),
        "status": _text(row, "status"),
        "maturity_stage": _text(row, "maturity_stage"),
        "participation_mode": _text(row, "participation_mode"),
        "config_version": _text(row, "config_version"),
        "config_hash": _text(row, "config_hash"),
    }


def _directional_aggregation_issues(aggregation: Any) -> tuple[WeakModelQualityIssue, ...]:
    score = _float_or_none(getattr(aggregation, "directional_score", None))
    if score is None:
        return ()
    absolute = abs(score)
    if absolute >= DIRECTIONAL_CRITICAL_THRESHOLD:
        return (
            WeakModelQualityIssue(
                error_code="directional_score_too_extreme",
                reason="聚合 directional_score 过于极端，27C 前需要人工校准。",
                severity=WeakModelQualitySeverity.CRITICAL.value,
                field_name="directional_score",
                observed_value=score,
                expected=f"abs(directional_score) < {DIRECTIONAL_CRITICAL_THRESHOLD}",
                calibration_suggestion="考虑降低方向型模型 signal_score 上限或增加冲突/支撑压力降置信度。",
            ),
        )
    if absolute >= DIRECTIONAL_WARNING_THRESHOLD:
        return (
            WeakModelQualityIssue(
                error_code="directional_score_too_strong",
                reason="聚合 directional_score 偏强，接入 18 前建议人工复核。",
                severity=WeakModelQualitySeverity.WARNING.value,
                field_name="directional_score",
                observed_value=score,
                expected=f"abs(directional_score) < {DIRECTIONAL_WARNING_THRESHOLD}",
                calibration_suggestion="优先观察是否频繁输出 ±0.75，再决定是否调低方向分数。",
            ),
        )
    return ()


def _single_result_directional_issues(row: Any) -> tuple[WeakModelQualityIssue, ...]:
    if _text(row, "model_role") != "directional":
        return ()
    score = _float_or_none(getattr(row, "signal_score", None))
    if score is None:
        return ()
    absolute = abs(score)
    severity = None
    code = ""
    reason = ""
    if absolute >= DIRECTIONAL_CRITICAL_THRESHOLD:
        severity = WeakModelQualitySeverity.CRITICAL.value
        code = "directional_signal_score_too_extreme"
        reason = "单个方向型弱模型 signal_score 过于极端。"
    elif absolute > DIRECTIONAL_WARNING_THRESHOLD:
        severity = WeakModelQualitySeverity.WARNING.value
        code = "directional_signal_score_too_strong"
        reason = "单个方向型弱模型 signal_score 偏强。"
    if severity is None:
        return ()
    issues = [
        WeakModelQualityIssue(
            error_code=code,
            reason=reason,
            severity=severity,
            model_key=_text(row, "model_key"),
            field_name="signal_score",
            observed_value=score,
            expected="signal_score should be conservative before 27C",
            calibration_suggestion="必要时把强方向输出从 ±0.75 降到 ±0.50/±0.60。",
        )
    ]
    evidence = _json_dict(getattr(row, "evidence_json", None))
    if not evidence:
        issues.append(
            WeakModelQualityIssue(
                error_code="strong_direction_without_evidence",
                reason="强方向输出缺少 evidence_json 支撑。",
                severity=WeakModelQualitySeverity.WARNING.value,
                model_key=_text(row, "model_key"),
                field_name="evidence_json",
                expected="non-empty evidence_json for strong directional output",
            )
        )
    return tuple(issues)


def _single_result_confidence_issues(row: Any) -> tuple[WeakModelQualityIssue, ...]:
    confidence = _float_or_none(getattr(row, "confidence", None))
    if confidence is None:
        return ()
    if confidence >= CONFIDENCE_CRITICAL_THRESHOLD:
        severity = WeakModelQualitySeverity.CRITICAL.value
        code = "confidence_too_extreme"
        reason = "弱模型 confidence 过高，不符合 27B 保守化原则。"
    elif confidence >= CONFIDENCE_WARNING_THRESHOLD:
        severity = WeakModelQualitySeverity.WARNING.value
        code = "confidence_too_high"
        reason = "弱模型 confidence 偏高，需要确认是否有足够证据。"
    else:
        return ()
    return (
        WeakModelQualityIssue(
            error_code=code,
            reason=reason,
            severity=severity,
            model_key=_text(row, "model_key"),
            field_name="confidence",
            observed_value=confidence,
            expected=f"confidence < {CONFIDENCE_WARNING_THRESHOLD} unless evidence is exceptionally clear",
            calibration_suggestion="默认 confidence 建议保持在 0.50~0.70，极清晰证据才允许接近 0.80。",
        ),
    )


def _single_result_risk_issues(row: Any) -> tuple[WeakModelQualityIssue, ...]:
    if _text(row, "model_role") != "risk":
        return ()
    risk_score = _float_or_none(getattr(row, "risk_score", None))
    if risk_score is None:
        return ()
    risk_level = _text(row, "risk_level") or "unknown"
    expected_level = _expected_risk_level(risk_score)
    issues: list[WeakModelQualityIssue] = []
    if risk_level != expected_level:
        issues.append(
            WeakModelQualityIssue(
                error_code="risk_score_level_mismatch",
                reason="risk_score 与 risk_level 不匹配。",
                severity=WeakModelQualitySeverity.WARNING.value,
                model_key=_text(row, "model_key"),
                field_name="risk_level",
                observed_value={"risk_score": risk_score, "risk_level": risk_level},
                expected=expected_level,
                calibration_suggestion="复核 volatility_risk_gate 的 risk_score 分层阈值。",
            )
        )
    if risk_score >= 0.80 and (_text(row, "trade_permission") != "block" or not bool(getattr(row, "veto_triggered", False))):
        issues.append(
            WeakModelQualityIssue(
                error_code="risk_veto_expected_but_not_blocked",
                reason="risk_score >= 0.80 时未触发 block/veto。",
                severity=WeakModelQualitySeverity.WARNING.value,
                model_key=_text(row, "model_key"),
                field_name="trade_permission",
                observed_value={
                    "risk_score": risk_score,
                    "trade_permission": _text(row, "trade_permission"),
                    "veto_triggered": bool(getattr(row, "veto_triggered", False)),
                },
                expected="trade_permission=block and veto_triggered=true",
                calibration_suggestion="确认风险模型 can_veto 和 veto 阈值是否过松。",
            )
        )
    return tuple(issues)


def _veto_factor_issues(aggregation: Any) -> tuple[WeakModelQualityIssue, ...]:
    if not bool(getattr(aggregation, "veto_triggered", False)):
        return ()
    veto_factors = _json_list(getattr(aggregation, "veto_factors_json", None))
    if veto_factors:
        return ()
    return (
        WeakModelQualityIssue(
            error_code="veto_triggered_without_veto_factors",
            reason="聚合结果 veto_triggered=true，但 veto_factors 为空。",
            severity=WeakModelQualitySeverity.WARNING.value,
            field_name="veto_factors_json",
            observed_value=veto_factors,
            expected="non-empty veto_factors_json",
        ),
    )


def _context_summary_issues(aggregation: Any, results: tuple[Any, ...]) -> tuple[WeakModelQualityIssue, ...]:
    context_summary = _json_dict(getattr(aggregation, "context_summary_json", None))
    context_rows = tuple(row for row in results if _text(row, "model_role") == "context")
    if not context_summary or not context_summary.get("regime"):
        return (
            WeakModelQualityIssue(
                error_code="context_summary_missing",
                reason="weak_model_aggregation.context_summary_json 缺失或不完整。",
                severity=WeakModelQualitySeverity.WARNING.value,
                field_name="context_summary_json",
                observed_value=context_summary,
                expected="context summary with regime/source fields",
            ),
        )
    if context_rows and not context_summary.get("source_model_key"):
        return (
            WeakModelQualityIssue(
                error_code="context_summary_source_missing",
                reason="存在 context 弱模型结果，但 context_summary 缺少 source_model_key。",
                severity=WeakModelQualitySeverity.WARNING.value,
                field_name="context_summary_json",
                observed_value=context_summary,
                expected="source_model_key should reference context model",
            ),
        )
    issues: list[WeakModelQualityIssue] = []
    for row in context_rows:
        if _is_observe_only(row) and context_summary.get("source_model_key") == _text(row, "model_key"):
            source_stage = str(context_summary.get("source_maturity_stage") or "")
            if source_stage != "observe_only":
                issues.append(
                    WeakModelQualityIssue(
                        error_code="observe_only_context_stage_missing",
                        reason="observe_only context 被写入摘要时未标明 source_maturity_stage=observe_only。",
                        severity=WeakModelQualitySeverity.WARNING.value,
                        model_key=_text(row, "model_key"),
                        field_name="context_summary_json",
                        observed_value=context_summary,
                        expected="source_maturity_stage=observe_only",
                    )
                )
    return tuple(issues)


def _observe_only_context_pollution_issues(row: Any) -> tuple[WeakModelQualityIssue, ...]:
    if _text(row, "model_role") != "context" or not _is_observe_only(row):
        return ()
    polluted_fields: list[str] = []
    if abs(_float_or_none(getattr(row, "static_weight", None)) or 0.0) > 0:
        polluted_fields.append("static_weight")
    if abs(_float_or_none(getattr(row, "effective_score", None)) or 0.0) > 0:
        polluted_fields.append("effective_score")
    if _float_or_none(getattr(row, "signal_score", None)) is not None:
        polluted_fields.append("signal_score")
    if _float_or_none(getattr(row, "risk_score", None)) is not None:
        polluted_fields.append("risk_score")
    if _text(row, "trade_permission"):
        polluted_fields.append("trade_permission")
    if not polluted_fields:
        return ()
    return (
        WeakModelQualityIssue(
            error_code="observe_only_context_pollution",
            reason="observe_only context 存在可能污染方向分数或交易权限的字段。",
            severity=WeakModelQualitySeverity.WARNING.value,
            model_key=_text(row, "model_key"),
            field_name=",".join(polluted_fields),
            observed_value={field: getattr(row, field, None) for field in polluted_fields},
            expected="observe_only context should only provide context_summary background",
        ),
    )


def _expected_risk_level(risk_score: float) -> str:
    if risk_score < 0.35:
        return "low"
    if risk_score < 0.60:
        return "medium"
    if risk_score < 0.80:
        return "high"
    return "extreme"


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(row: Any, field_name: str) -> str:
    value = getattr(row, field_name, "")
    return "" if value is None else str(value)


def _is_observe_only(row: Any) -> bool:
    return _text(row, "maturity_stage") == "observe_only" or _text(row, "participation_mode") == "observe_only"


__all__ = ["checked_model_summary", "evaluate_quality_issues"]
