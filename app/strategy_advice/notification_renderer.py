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

REVIEW_DECISION_LABELS = {
    "accept_23f": "模型认可 23F",
    "reject_23f": "模型反对 23F",
    "require_more_evidence": "模型要求更多证据",
    "need_more_evidence": "模型要求更多证据",
    "wait": "模型建议等待",
    "no_trade": "模型建议不交易",
    "blocked": "模型阻断",
    "unknown": "模型结论不明确",
}

RECOMMENDATION_LABELS = {
    "allow_conditional": "允许后续建议层谨慎评估",
    "accept_for_further_review": "可进入后续人工审查",
    "wait": "等待",
    "reject": "拒绝",
    "risk_reject": "风险拒绝",
    "downgrade": "降级",
    "require_more_evidence": "要求更多证据",
    "need_more_evidence": "要求更多证据",
    "human_review_required": "需要人工复核",
    "unknown": "不明确",
}

EVIDENCE_QUALITY_LABELS = {
    "strong": "证据较强",
    "moderate": "证据中等",
    "weak": "证据偏弱",
    "insufficient": "证据不足",
    "unknown": "证据质量不明确",
}

REASON_CODE_LABELS = {
    "volume_confirmation_missing": "成交量确认缺失",
    "strategy_evidence_missing": "缺少 23F 策略证据",
    "model_review_missing": "缺少 24C 模型审查",
    "trigger_not_confirmed": "触发条件未确认",
    "context_wait": "市场背景仍需等待",
    "countertrend_candidate_blocked": "逆势候选被风控阻断",
    "insufficient_key_levels": "关键价位证据不足",
    "common_payload_parse_failed": "策略公共证据解析失败",
    "low_quality": "模型输出质量不足",
    "boundary_violation": "模型输出越界",
    "parse_failed": "模型输出解析失败",
    "schema_invalid": "模型输出结构异常",
}

ENGLISH_PHRASE_LABELS = (
    ("insufficient evidence from multiple strategies", "多个策略证据不足"),
    ("support/resistance missing", "支撑压力证据缺失"),
    ("detailed output from all decision participant strategies", "缺少决策参与策略的详细输出"),
    ("support and resistance levels", "缺少支撑压力位"),
    ("no confirmed conditional setup is available", "尚无确认的条件交易方案"),
    ("waiting for confirmation", "等待确认"),
)


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
    del model_review
    return _render_compact_message(
        review_row=review_row,
        payload=payload,
        lifecycle=lifecycle,
        advice=advice,
        include_lifecycle_reason=False,
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
    del model_review, risk, strategy
    return _render_compact_message(
        review_row=review_row,
        payload=payload,
        lifecycle=lifecycle,
        advice=advice,
        include_lifecycle_reason=True,
    )


def _render_compact_message(
    *,
    review_row: Any,
    payload: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    advice: Mapping[str, Any],
    include_lifecycle_reason: bool,
) -> str:
    """Render a short Chinese 24D message without raw JSON or dict dumps."""

    lifecycle_action = _text(lifecycle.get("action")) or _text_attr(review_row, "lifecycle_action")
    lifecycle_reason = _text(lifecycle.get("reason")) or _text_attr(review_row, "lifecycle_reason")
    advice_action = _text(advice.get("advice_action")) or "wait"
    directional_bias = _text(advice.get("directional_bias")) or "unknown"
    trade_permission = _text(advice.get("trade_permission")) or "not_allowed"
    lines = [
        f"生命周期：{_label(LIFECYCLE_LABELS, lifecycle_action)}",
    ]
    if include_lifecycle_reason and lifecycle_reason:
        lines.append(f"原因：{_bounded(_readable_text(lifecycle_reason), 90)}")
    lines.extend(
        [
            "当前建议：",
            f"- 动作：{advice_action}（{_label(ACTION_LABELS, advice_action)}）",
            f"- 方向：{directional_bias}（{_label(DIRECTION_LABELS, directional_bias)}）",
            f"- 交易许可：{trade_permission}（{_label(PERMISSION_LABELS, trade_permission)}）",
        ]
    )
    lines.extend(_format_strategy_evidence_summary(payload))
    lines.extend(_format_risk_gate_summary(payload))
    lines.extend(_format_model_review_status(payload))
    lines.extend(_format_model_objections(payload))
    lines.extend(_format_missing_evidence(payload))
    lines.append(_boundary_text())
    return "\n".join(line for line in lines if line)


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


def _format_strategy_evidence_summary(payload: Mapping[str, Any]) -> list[str]:
    """Return a bounded public 23F summary for Hermes display."""

    strategy_chain = _strategy_chain_from_payload(payload)
    if not strategy_chain:
        return []

    lines = ["策略证据："]
    strategy_source = _text(strategy_chain.get("source")) or "missing"
    if strategy_source == "missing":
        lines.append("- 23F：未找到策略证据聚合结果，证据链不完整。")
        return lines

    candidate_bias = _text(strategy_chain.get("candidate_bias")) or "unknown"
    readiness = _text(strategy_chain.get("decision_readiness")) or "unknown"
    aggregation_id = _text(strategy_chain.get("aggregation_id"))
    summary = f"- 23F：candidate_bias={candidate_bias}，decision_readiness={readiness}"
    if aggregation_id:
        summary += f"，aggregation_id={aggregation_id}"
    lines.append(f"{summary}。")

    for point in _list_value(strategy_chain.get("key_strategy_points"))[:3]:
        point_line = _strategy_point_line(_mapping(point))
        if point_line:
            lines.append(point_line)
    return lines


def _format_risk_gate_summary(payload: Mapping[str, Any]) -> list[str]:
    """Return at most two risk-gate lines from the 23F public summary."""

    risk_gate = _mapping(_strategy_chain_from_payload(payload).get("risk_gate_summary"))
    if not risk_gate:
        return []
    line = _risk_gate_line(risk_gate)
    return ["风控：", f"- {line}"] if line else []


def _format_model_review_status(payload: Mapping[str, Any]) -> list[str]:
    """Render model-review adoption status without implying a new 21 call."""

    model_review = _model_review_from_payload(payload)
    if not model_review or _text(model_review.get("source")) == "missing":
        return ["大模型审查：本轮未找到可用模型审查结果。"]

    adoption = _text(model_review.get("adoption_status")) or "unknown"
    reason = _text(model_review.get("adoption_reason"))
    review_decision = _text(model_review.get("review_decision")) or "unknown"
    evidence_quality = _text(model_review.get("evidence_quality")) or "unknown"
    recommendation = _text(model_review.get("recommendation_to_advice_layer")) or "unknown"
    lines = [_model_adoption_line(model_review, adoption=adoption, reason=reason)]
    lines.append(f"- review_decision={review_decision}（{_label(REVIEW_DECISION_LABELS, review_decision)}）")
    lines.append(
        f"- evidence_quality={evidence_quality}（{_label(EVIDENCE_QUALITY_LABELS, evidence_quality)}）；"
        f"recommendation={_recommendation_text(recommendation)}"
    )
    return lines


def _format_model_objections(payload: Mapping[str, Any]) -> list[str]:
    """Render at most two model objections, never raw JSON/Python dict text."""

    model_review = _model_review_from_payload(payload)
    if not model_review or _text(model_review.get("source")) == "missing":
        return []
    objections: list[str] = []
    for item in (model_review.get("main_objection"), model_review.get("strongest_counterargument")):
        text = _readable_text(item)
        if text and text not in objections:
            objections.append(text)
        if len(objections) >= 2:
            break
    if not objections:
        return []
    return ["主要反驳：", *[f"- {_bounded(item, 90)}" for item in objections]]


def _format_missing_evidence(payload: Mapping[str, Any]) -> list[str]:
    """Render missing evidence from 23F and 24C with a hard display limit."""

    strategy_chain = _strategy_chain_from_payload(payload)
    model_review = _model_review_from_payload(payload)
    missing: list[str] = []
    for item in _list_value(strategy_chain.get("evidence_missing")):
        text = _readable_text(item)
        if text and text not in missing:
            missing.append(text)
        if len(missing) >= 3:
            break
    if len(missing) < 3:
        for item in _list_value(model_review.get("missing_evidence")):
            text = _readable_text(item)
            if text and text not in missing:
                missing.append(text)
            if len(missing) >= 3:
                break
    if not missing:
        return []
    return ["缺失证据：", *[f"- {_bounded(item, 80)}" for item in missing[:3]]]


def _strategy_chain_from_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    summary = _mapping(payload.get("evidence_chain_summary"))
    return _mapping(payload.get("strategy_evidence_chain")) or _mapping(summary.get("strategy_evidence_chain"))


def _model_review_from_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    summary = _mapping(payload.get("evidence_chain_summary"))
    return _mapping(payload.get("model_review_summary")) or _mapping(summary.get("model_review_summary"))


def _risk_gate_line(risk_gate: Mapping[str, Any]) -> str:
    if not risk_gate:
        return ""
    decision = _text(risk_gate.get("risk_gate_decision")) or _text(risk_gate.get("risk_level")) or "unknown"
    scope = _text(risk_gate.get("risk_scope")) or "unknown"
    reason = _readable_text(risk_gate.get("reason_text") or risk_gate.get("summary"))
    codes = _list_text(risk_gate.get("reason_codes"), limit=2)
    suffix = reason or codes
    return f"风险闸门={decision}，scope={scope}。{_bounded(suffix, 90)}"


def _strategy_point_line(point: Mapping[str, Any]) -> str:
    name = _text(point.get("strategy_name")) or _text(point.get("strategy_role")) or "strategy"
    decision = (
        _text(point.get("candidate_bias"))
        or _text(point.get("filter_decision"))
        or _text(point.get("risk_gate_decision"))
        or _text(point.get("participation_mode"))
    )
    summary = _readable_text(point.get("summary")) or _list_text(point.get("reason_codes"), limit=2)
    if not decision and not summary:
        return ""
    return f"- 关键策略：{name}：{decision or 'evidence'}，{_bounded(summary, 70)}"


def _model_adoption_line(model_review: Mapping[str, Any], *, adoption: str, reason: str) -> str:
    if _is_mock_review_summary(model_review):
        return "大模型审查：仅测试模型结果，不作为真实模型审查依据。"
    if adoption == "adopted":
        return (
            f"大模型审查：已采用当前材料包已有 {_model_display_name(model_review)} "
            "审查结果，本轮 21 未新调用大模型。"
        )
    if adoption == "low_weight":
        return "大模型审查：结果质量不足，仅低权重展示，不作为强依据。"
    if adoption == "rejected":
        return f"大模型审查：结果不可采用，原因：{_adoption_reason_label(reason)}。"
    if adoption == "missing":
        return "大模型审查：本轮未找到可用模型审查结果。"
    return f"大模型审查：采用状态={adoption}，原因：{_adoption_reason_label(reason or adoption)}。"


def _model_display_name(model_review: Mapping[str, Any]) -> str:
    raw = " ".join(
        _text(model_review.get(key)).lower()
        for key in ("provider", "model_key", "model_name")
        if _text(model_review.get(key))
    )
    if "deepseek" in raw:
        return "DeepSeek"
    if "openai" in raw:
        return "OpenAI"
    if "claude" in raw:
        return "Claude"
    return "真实模型"


def _is_mock_review_summary(model_review: Mapping[str, Any]) -> bool:
    raw = " ".join(
        _text(model_review.get(key)).lower()
        for key in ("provider", "model_key", "model_name")
        if _text(model_review.get(key))
    )
    return bool(model_review.get("is_mock_review")) or "mock_review" in raw or raw.startswith("mock")


def _adoption_reason_label(reason: str) -> str:
    if not reason:
        return "未提供"
    if reason in REASON_CODE_LABELS:
        return REASON_CODE_LABELS[reason]
    if reason.startswith("schema_"):
        return "结构异常"
    if reason.startswith("model_review_run_"):
        return "模型审查运行未成功"
    if reason == "real_model_disabled":
        return "真实模型关闭"
    if reason == "model_review_24c_payload_missing":
        return "缺少 24C 结构化审查摘要"
    if reason == "usable_model_review":
        return "可用模型审查"
    if reason == "mock_review_is_test_only":
        return "测试模型结果"
    return _bounded(_readable_text(reason), 60)


def _recommendation_text(recommendation: str) -> str:
    if not recommendation:
        return "不明确"
    return _label(RECOMMENDATION_LABELS, recommendation)


def _source_value(payload: Mapping[str, Any], key: str) -> str:
    source = _mapping(payload.get("source"))
    return _text(source.get(key)) or "无"


def _boundary_text() -> str:
    return "边界：本消息不是交易指令；系统不自动交易；是否执行由用户人工决定。"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _label(mapping: Mapping[str, str], value: str) -> str:
    return mapping.get(value, value or "未知")


def _yes_no(value: Any) -> str:
    return "是" if bool(value) else "否"


def _list_text(value: Any, *, limit: int = 5) -> str:
    if not isinstance(value, (list, tuple)) or not value:
        return "无"
    return "；".join(_bounded(_readable_text(item), 70) for item in value[:limit])


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


def _readable_text(value: Any) -> str:
    """Return a short user-readable Chinese-ish summary, never raw dict JSON."""

    if isinstance(value, Mapping):
        for key in (
            "reason_text",
            "reason",
            "summary",
            "context_summary",
            "main_objection",
            "strongest_counterargument",
            "missing",
            "field",
        ):
            text = _readable_text(value.get(key))
            if text:
                return text
        code = _text(value.get("reason_code") or value.get("code"))
        if code:
            return _reason_code_label(code)
        name = _text(value.get("strategy_name") or value.get("strategy_role"))
        if name:
            return f"{name} 的结构化摘要已压缩。"
        return "结构化摘要已压缩。"
    if isinstance(value, (list, tuple)):
        texts = [_readable_text(item) for item in value[:3]]
        return "；".join(item for item in texts if item)

    text = _text(value).replace("\n", " ").strip()
    if not text:
        return ""
    if _looks_like_raw_mapping_text(text):
        return "结构化摘要已压缩。"
    if text in REASON_CODE_LABELS:
        return _reason_code_label(text)
    translated = _translate_common_english_summary(text)
    return translated or text


def _reason_code_label(code: str) -> str:
    return REASON_CODE_LABELS.get(code, code)


def _looks_like_raw_mapping_text(text: str) -> bool:
    stripped = text.strip()
    return (
        (stripped.startswith("{") and ":" in stripped)
        or (stripped.startswith("[{") and ":" in stripped)
        or "'discipline_check'" in stripped
        or '"discipline_check"' in stripped
    )


def _translate_common_english_summary(text: str) -> str:
    lower = text.lower()
    if _ascii_ratio(text) < 0.75:
        return ""
    fragments: list[str] = []
    for phrase, label in ENGLISH_PHRASE_LABELS:
        if phrase in lower:
            fragments.append(label)
    if "volume" in lower:
        fragments.append("成交量确认不足")
    if "key-level" in lower or "key level" in lower:
        fragments.append("关键价位仍需确认")
    if "breakout" in lower:
        fragments.append("突破确认仍需观察")
    if "pullback" in lower:
        fragments.append("回踩确认仍需观察")
    if "risk control" in lower or "risk" in lower:
        fragments.append("风险条件仍需确认")
    if "wait" in lower or "waiting" in lower:
        fragments.append("等待确认")
    if "confirmation" in lower or "confirmed" in lower:
        fragments.append("确认信号不足")
    if not fragments:
        return "模型原文提示存在未翻译内容，请查看模型审查原文。" if len(text) >= 24 else ""
    unique = list(dict.fromkeys(fragments))
    return "，".join(unique) + "。"


def _ascii_ratio(text: str) -> float:
    if not text:
        return 0.0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    return ascii_chars / len(text)


def _bounded(value: str, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    if max_length <= 1:
        return text[:max_length]
    return f"{text[: max_length - 1]}…"


def _bounded_message(value: str, *, max_length: int) -> str:
    text = "\n".join(line.rstrip() for line in str(value or "").splitlines() if line.strip())
    if len(text) <= max_length:
        return text
    lines = text.splitlines()
    boundary = _boundary_text()
    essential_prefixes = (
        "生命周期",
        "当前建议",
        "- 动作",
        "- 方向",
        "- 交易许可",
        "策略证据",
        "- 23F",
        "大模型审查",
        "- review_decision",
        "- evidence_quality",
        "风控",
        "- 风险闸门",
        "边界",
    )
    essential = [line for line in lines if line.startswith(essential_prefixes)]
    if boundary not in essential:
        essential.append(boundary)
    compact = "\n".join(_bounded(line, 140) for line in essential)
    if len(compact) <= max_length:
        return compact
    emergency = "\n".join(
        [
            next((line for line in essential if line.startswith("当前建议")), "当前建议："),
            next((line for line in essential if line.startswith("- 动作")), "- 动作：wait"),
            next((line for line in essential if line.startswith("- 23F")), "- 23F：unknown"),
            next((line for line in essential if line.startswith("大模型审查")), "大模型审查：摘要已压缩。"),
            boundary,
        ]
    )
    return emergency if len(emergency) <= max_length else emergency[:max_length]


__all__ = [
    "RELATED_TYPE_LIFECYCLE_REVIEW",
    "RELATED_TYPE_STRATEGY_ADVICE",
    "render_strategy_advice_notification",
    "resolve_strategy_advice_notification_related_ref",
]
