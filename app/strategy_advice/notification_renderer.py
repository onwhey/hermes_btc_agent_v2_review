"""Chinese renderer for stage-21B strategy advice notifications.

This file belongs to `app/strategy_advice`. It converts a persisted 21A
`strategy_advice_lifecycle_review.notification_payload_json` into a bounded
Chinese title/body for Hermes. It does not read/write databases, call Hermes,
call model providers, touch Redis, modify Kline data, or perform trading.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.strategy_advice.notification_schema import RenderedStrategyAdviceNotification
from app.strategy_advice.schema import load_json_text

RELATED_TYPE_STRATEGY_ADVICE = "strategy_advice"
RELATED_TYPE_LIFECYCLE_REVIEW = "strategy_advice_lifecycle_review"

ACTION_LABELS = {
    "wait": "等待",
    "avoid_trade": "不交易",
    "stop_trading": "暂停交易",
    "conditional_trade": "条件满足后才允许人工考虑",
    "manage_position": "管理已有人工仓位",
}

DIRECTION_LABELS = {
    "bullish": "偏多",
    "bearish": "偏空",
    "neutral": "中性",
    "mixed": "分歧",
    "unknown": "不明确",
}

PERMISSION_LABELS = {
    "not_allowed": "不允许交易",
    "conditionally_allowed": "条件满足后可人工考虑",
    "position_management_only": "只允许管理已有人工仓位",
}

LIFECYCLE_LABELS = {
    "create_new_advice": "新建建议",
    "continue_active_advice": "延续上一条建议",
    "update_active_advice": "调整上一条建议",
    "close_active_advice": "关闭建议",
    "complete_active_advice": "建议完成",
    "invalidate_active_advice": "建议失效",
    "expire_active_advice": "建议过期",
    "wait_without_active_advice": "无 active 建议，继续等待",
    "stop_trading": "暂停交易判断",
}


def render_strategy_advice_notification(review_row: Any) -> RenderedStrategyAdviceNotification:
    """Render one 21A lifecycle review into a Chinese Hermes notification."""

    payload = _payload_from_row(review_row)
    related_type, related_id = resolve_strategy_advice_notification_related_ref(review_row)
    lifecycle = _mapping(payload.get("lifecycle"))
    advice = _mapping(payload.get("advice"))
    model_review = _mapping(payload.get("model_review"))
    risk = _mapping(payload.get("risk"))
    strategy = _mapping(payload.get("strategy"))

    lifecycle_action = _text(lifecycle.get("action")) or _text_attr(review_row, "lifecycle_action")
    advice_action = _text(advice.get("advice_action")) or "wait"
    notification_level = (_text_attr(review_row, "notification_level") or _text(lifecycle.get("notification_level")) or "brief").lower()
    title = _title_for_review(review_row, lifecycle_action=lifecycle_action, advice_action=advice_action)
    severity = _severity_for_payload(
        notification_level=notification_level,
        lifecycle_action=lifecycle_action,
        model_review=model_review,
        risk=risk,
    )
    message = (
        _render_brief_message(
            review_row=review_row,
            payload=payload,
            lifecycle=lifecycle,
            advice=advice,
            model_review=model_review,
        )
        if notification_level == "brief"
        else _render_full_message(
            review_row=review_row,
            payload=payload,
            lifecycle=lifecycle,
            advice=advice,
            model_review=model_review,
            risk=risk,
            strategy=strategy,
        )
    )
    message = _bounded_message(message, max_length=1500)
    return RenderedStrategyAdviceNotification(
        title=title,
        message=message,
        notification_level=notification_level,
        severity=severity,
        related_type=related_type,
        related_id=related_id,
        payload=payload,
        lifecycle_action=lifecycle_action,
        advice_action=advice_action,
        model_status_summary=_model_status_text(model_review),
    )


def resolve_strategy_advice_notification_related_ref(review_row: Any) -> tuple[str, str]:
    """Return the required related_type/related_id fallback for 21B."""

    result_advice_id = _optional_text_attr(review_row, "result_advice_id")
    if result_advice_id:
        return RELATED_TYPE_STRATEGY_ADVICE, result_advice_id
    reviewed_advice_id = _optional_text_attr(review_row, "reviewed_advice_id")
    if reviewed_advice_id:
        return RELATED_TYPE_STRATEGY_ADVICE, reviewed_advice_id
    return RELATED_TYPE_LIFECYCLE_REVIEW, _text_attr(review_row, "review_id")


def _render_brief_message(
    *,
    review_row: Any,
    payload: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    advice: Mapping[str, Any],
    model_review: Mapping[str, Any],
) -> str:
    lifecycle_action = _text(lifecycle.get("action")) or _text_attr(review_row, "lifecycle_action")
    advice_action = _text(advice.get("advice_action")) or "wait"
    directional_bias = _text(advice.get("directional_bias")) or "unknown"
    trade_permission = _text(advice.get("trade_permission")) or "not_allowed"
    return "\n".join(
        [
            "本轮系统已完成 21A 建议生命周期复核。",
            f"生命周期：{_label(LIFECYCLE_LABELS, lifecycle_action)}",
            f"当前建议：{_label(ACTION_LABELS, advice_action)} / {_label(DIRECTION_LABELS, directional_bias)} / {_label(PERMISSION_LABELS, trade_permission)}",
            f"大模型状态：{_model_status_text(model_review)}",
            *_render_evidence_chain_lines(payload, compact=True),
            f"来源：review={_source_value(payload, 'review_aggregation_run_id')} material={_source_value(payload, 'material_pack_id')}",
            _boundary_text(),
        ]
    )


def _render_full_message(
    *,
    review_row: Any,
    payload: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    advice: Mapping[str, Any],
    model_review: Mapping[str, Any],
    risk: Mapping[str, Any],
    strategy: Mapping[str, Any],
) -> str:
    lifecycle_action = _text(lifecycle.get("action")) or _text_attr(review_row, "lifecycle_action")
    lifecycle_reason = _text(lifecycle.get("reason")) or _text_attr(review_row, "lifecycle_reason")
    advice_action = _text(advice.get("advice_action")) or "wait"
    directional_bias = _text(advice.get("directional_bias")) or "unknown"
    trade_permission = _text(advice.get("trade_permission")) or "not_allowed"
    return "\n".join(
        [
            f"生命周期：{lifecycle_action}（{_label(LIFECYCLE_LABELS, lifecycle_action)}）",
            f"原因：{_bounded(lifecycle_reason, 240)}",
            "",
            "当前建议：",
            f"- 动作：{advice_action}（{_label(ACTION_LABELS, advice_action)}）",
            f"- 方向：{directional_bias}（{_label(DIRECTION_LABELS, directional_bias)}）",
            f"- 权限：{trade_permission}（{_label(PERMISSION_LABELS, trade_permission)}）",
            "",
            "大模型状态：",
            f"- 本轮是否调用大模型：{_yes_no(model_review.get('model_review_invoked'))}",
            f"- 是否复用旧模型结果：{_yes_no(model_review.get('model_review_reused'))}",
            f"- 复用 run：{_text(model_review.get('reused_model_analysis_run_id')) or '无'}",
            f"- 审查依据：{_text(model_review.get('model_review_basis')) or '无'}",
            f"- 是否过期：{_yes_no(model_review.get('model_review_expired'))}",
            f"- chain 状态：{_text(model_review.get('model_review_chain_status')) or 'not_started'}",
            f"- 未调用原因：{_no_model_reason(model_review)}",
            f"- 阻断原因：{_text(model_review.get('model_review_block_reason')) or '无'}",
            _partial_success_notice(model_review),
            *_render_evidence_chain_lines(payload, compact=False),
            "",
            "风险状态：",
            f"- 风险可接受性：{_text(risk.get('risk_acceptability')) or '未提供'}",
            f"- 策略冲突：{_text(strategy.get('strategy_conflict')) or '未提供'}",
            f"- 风险警告：{_list_text(risk.get('risk_warnings'))}",
            f"- 缺失证据：{_list_text(risk.get('missing_evidence'))}",
            f"- 风险是否阻断：{_yes_no(risk.get('risk_blocked'))}",
            "",
            "来源追踪：",
            f"- review_aggregation_run_id：{_source_value(payload, 'review_aggregation_run_id')}",
            f"- material_pack_id：{_source_value(payload, 'material_pack_id')}",
            f"- strategy_signal_run_id：{_source_value(payload, 'strategy_signal_run_id')}",
            f"- snapshot_id：{_source_value(payload, 'snapshot_id')}",
            "",
            _boundary_text(),
        ]
    )


def _payload_from_row(review_row: Any) -> Mapping[str, Any]:
    raw_payload = getattr(review_row, "notification_payload_json", None)
    payload = load_json_text(raw_payload, {})
    return payload if isinstance(payload, Mapping) else {}


def _title_for_review(review_row: Any, *, lifecycle_action: str, advice_action: str) -> str:
    symbol = _text_attr(review_row, "symbol") or "BTCUSDT"
    base = _text_attr(review_row, "base_interval") or "4h"
    symbol_label = "BTC" if symbol.upper().startswith("BTC") else symbol.upper()
    if lifecycle_action == "continue_active_advice":
        suffix = "延续上一条建议"
    elif lifecycle_action == "update_active_advice":
        suffix = "建议已调整"
    elif lifecycle_action == "stop_trading" or advice_action == "stop_trading":
        suffix = "暂停交易判断"
    elif lifecycle_action == "wait_without_active_advice" or advice_action == "wait":
        suffix = "继续等待"
    elif lifecycle_action in {"close_active_advice", "complete_active_advice", "invalidate_active_advice", "expire_active_advice"}:
        suffix = _label(LIFECYCLE_LABELS, lifecycle_action)
    else:
        suffix = _label(ACTION_LABELS, advice_action)
    return f"{symbol_label} {base} 建议：{suffix}"


def _severity_for_payload(
    *,
    notification_level: str,
    lifecycle_action: str,
    model_review: Mapping[str, Any],
    risk: Mapping[str, Any],
) -> str:
    if notification_level == "brief":
        return "info"
    high_risk = bool(risk.get("risk_blocked")) or "high" in _text(risk.get("strategy_conflict")).lower()
    model_problem = bool(model_review.get("model_review_expired")) or _text(
        model_review.get("model_review_chain_status")
    ) == "partial_success"
    if lifecycle_action == "stop_trading" or high_risk or model_problem or _text(model_review.get("model_review_block_reason")):
        return "warning"
    return "warning"


def _model_status_text(model_review: Mapping[str, Any]) -> str:
    invoked = _yes_no(model_review.get("model_review_invoked"))
    reused = _yes_no(model_review.get("model_review_reused"))
    basis = _text(model_review.get("model_review_basis")) or "无"
    expired = _yes_no(model_review.get("model_review_expired"))
    chain_status = _text(model_review.get("model_review_chain_status")) or "not_started"
    reason = _no_model_reason(model_review)
    parts = [f"调用={invoked}", f"复用={reused}", f"依据={basis}", f"过期={expired}", f"chain={chain_status}"]
    if invoked == "否":
        parts.append(f"原因={reason}")
    if chain_status == "partial_success":
        parts.append("注意：partial_success 不是完整模型审查")
    return "；".join(parts)


def _no_model_reason(model_review: Mapping[str, Any]) -> str:
    return (
        _text(model_review.get("no_model_invocation_reason"))
        or _text(model_review.get("model_review_skip_reason"))
        or _text(model_review.get("model_review_block_reason"))
        or "未提供"
    )


def _partial_success_notice(model_review: Mapping[str, Any]) -> str:
    if _text(model_review.get("model_review_chain_status")) == "partial_success":
        return "- partial_success：本轮不是完整模型接力审查，不能伪装成完整审查"
    return "- partial_success：否"


def _render_evidence_chain_lines(payload: Mapping[str, Any], *, compact: bool) -> list[str]:
    summary = _mapping(payload.get("evidence_chain_summary"))
    strategy_chain = _mapping(payload.get("strategy_evidence_chain")) or _mapping(
        summary.get("strategy_evidence_chain")
    )
    model_review = _mapping(payload.get("model_review_summary")) or _mapping(summary.get("model_review_summary"))
    if not strategy_chain and not model_review:
        return []

    lines: list[str] = []
    strategy_source = _text(strategy_chain.get("source")) or "missing"
    if strategy_source == "missing":
        lines.append("策略证据链：缺少 23F 聚合，不能视为策略证据完整。")
    else:
        lines.append(
            "策略证据链："
            f"23F={_text(strategy_chain.get('candidate_bias')) or 'unknown'}，"
            f"readiness={_text(strategy_chain.get('decision_readiness')) or 'unknown'}，"
            f"confidence={_text(strategy_chain.get('candidate_confidence')) or 'unknown'}。"
        )
        risk_line = _risk_gate_line(_mapping(strategy_chain.get("risk_gate_summary")))
        if risk_line:
            lines.append(risk_line)
        for point in _list_value(strategy_chain.get("key_strategy_points"))[: (2 if compact else 4)]:
            point_line = _strategy_point_line(_mapping(point))
            if point_line:
                lines.append(point_line)
    lines.extend(_missing_evidence_lines(strategy_chain, compact=compact))

    if not model_review or _text(model_review.get("source")) == "missing":
        lines.append("模型审查：缺少 24C 结果，本轮不能伪装成已经完成大模型审查。")
    else:
        lines.extend(_model_review_lines(model_review, compact=compact))
    return lines


def _risk_gate_line(risk_gate: Mapping[str, Any]) -> str:
    if not risk_gate:
        return ""
    decision = _text(risk_gate.get("risk_gate_decision")) or _text(risk_gate.get("risk_level")) or "unknown"
    scope = _text(risk_gate.get("risk_scope")) or "unknown"
    reason = _text(risk_gate.get("reason_text")) or _text(risk_gate.get("summary"))
    return f"风控闸门：{decision} / {scope}。{_bounded(reason, 120)}"


def _strategy_point_line(point: Mapping[str, Any]) -> str:
    name = _text(point.get("strategy_name")) or _text(point.get("strategy_role")) or "strategy"
    decision = (
        _text(point.get("candidate_bias"))
        or _text(point.get("filter_decision"))
        or _text(point.get("risk_gate_decision"))
        or _text(point.get("participation_mode"))
    )
    summary = _text(point.get("summary")) or _list_text(point.get("reason_codes"))
    if not decision and not summary:
        return ""
    return f"关键策略：{name}：{decision or 'evidence'}，{_bounded(summary, 140)}"


def _missing_evidence_lines(strategy_chain: Mapping[str, Any], *, compact: bool) -> list[str]:
    missing = _list_value(strategy_chain.get("evidence_missing"))
    if not missing:
        return []
    texts: list[str] = []
    for item in missing[: (1 if compact else 3)]:
        mapping = _mapping(item)
        text = (
            _text(mapping.get("reason"))
            or _text(mapping.get("reason_code"))
            or _text(mapping.get("missing"))
            or _text(item)
        )
        if text:
            texts.append(_bounded(text, 100))
    return [f"缺失证据：{'；'.join(texts)}"] if texts else []


def _model_review_lines(model_review: Mapping[str, Any], *, compact: bool) -> list[str]:
    adoption = _text(model_review.get("adoption_status")) or "unknown"
    reason = _text(model_review.get("adoption_reason"))
    review_decision = _text(model_review.get("review_decision")) or "unknown"
    evidence_quality = _text(model_review.get("evidence_quality")) or "unknown"
    recommendation = _text(model_review.get("recommendation_to_advice_layer")) or "unknown"
    if bool(model_review.get("is_mock_review")):
        source_text = "mock_review，仅测试/演练展示"
    else:
        source_text = _text(model_review.get("model_key")) or _text(model_review.get("provider")) or "model"
    lines = [
        f"模型审查：{review_decision}，证据质量={evidence_quality}，建议={recommendation}，采用状态={adoption}。"
    ]
    if adoption != "adopted":
        lines.append(f"模型未作为强依据：{_bounded(reason or adoption, 140)}")
    elif source_text:
        lines.append(f"模型来源：{source_text}")
    objections = [
        _text(model_review.get("main_objection")),
        _text(model_review.get("strongest_counterargument")),
    ]
    objection_lines = [_bounded(item, 140) for item in objections if item][: (1 if compact else 2)]
    if objection_lines:
        lines.append(f"模型反驳：{'；'.join(objection_lines)}")
    flags = _list_value(model_review.get("boundary_flags")) or _list_value(model_review.get("quality_flags"))
    if flags and not compact:
        lines.append(f"模型质量/边界标记：{_list_text(flags[:3])}")
    return lines


def _source_value(payload: Mapping[str, Any], key: str) -> str:
    source = _mapping(payload.get("source"))
    return _text(source.get(key)) or "无"


def _boundary_text() -> str:
    return "边界声明：这不是自动交易，不是订单，不是强制执行指令；系统未读取账户，系统未下单。"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _label(mapping: Mapping[str, str], value: str) -> str:
    return mapping.get(value, value or "未知")


def _yes_no(value: Any) -> str:
    return "是" if bool(value) else "否"


def _list_text(value: Any) -> str:
    if not isinstance(value, (list, tuple)) or not value:
        return "无"
    return "；".join(_bounded(str(item), 120) for item in value[:5])


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Mapping):
        return [dict(value)]
    if value in (None, ""):
        return []
    return [value]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value).strip()


def _text_attr(row: Any, field_name: str) -> str:
    return _text(getattr(row, field_name, ""))


def _optional_text_attr(row: Any, field_name: str) -> str | None:
    text = _text_attr(row, field_name)
    return text or None


def _bounded(value: str, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 15]}...[truncated]"


def _bounded_message(value: str, *, max_length: int) -> str:
    text = str(value or "")
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 28]}\n...[truncated for Hermes]"


__all__ = [
    "RELATED_TYPE_LIFECYCLE_REVIEW",
    "RELATED_TYPE_STRATEGY_ADVICE",
    "render_strategy_advice_notification",
    "resolve_strategy_advice_notification_related_ref",
]
