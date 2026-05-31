"""Role-separated aggregation for 27A weak model outputs.

本文件属于 `app/weak_models` 模块，负责把 active 弱模型结果按 directional、
risk、confirmation、context 分层聚合。
本文件不读取数据库，不请求 Binance，不发送 Hermes，不调用大模型，不读取账户或仓位，
不生成订单，不自动交易。
"""

from __future__ import annotations

from app.weak_models.types import (
    WeakModelAggregationSummary,
    WeakModelEvaluationInput,
    WeakModelOutput,
    WeakModelProfile,
    WeakModelResultStatus,
    WeakModelRole,
    build_weak_model_aggregation_id,
)

RISK_ORDER = {"low": 1, "medium": 2, "high": 3, "extreme": 4}


class WeakModelAggregator:
    """Aggregate only enabled active weak model outputs into a compact summary."""

    def aggregate(
        self,
        *,
        weak_model_run_id: str,
        input_data: WeakModelEvaluationInput,
        outputs: tuple[WeakModelOutput, ...],
        profiles_by_key: dict[str, WeakModelProfile],
    ) -> WeakModelAggregationSummary:
        """Return role-separated aggregation without mutating any data."""

        active_outputs = tuple(
            output
            for output in outputs
            if output.status == WeakModelResultStatus.SUCCESS
            and profiles_by_key.get(output.model_key) is not None
            and profiles_by_key[output.model_key].participates_in_aggregation
        )
        directional_score, directional_confidence, directional_bias = _directional_summary(active_outputs)
        risk_level, trade_permission, veto_triggered, veto_factors = _risk_summary(active_outputs)
        supporting, opposing, conflict = _confirmation_summary(active_outputs, directional_bias)
        low_confidence = tuple(output.model_key for output in active_outputs if output.confidence < 0.50)
        context_summary = _context_summary(outputs, profiles_by_key)
        summary_text = (
            f"弱模型摘要：方向={directional_bias}({directional_score:.2f})，"
            f"风险={risk_level}，权限={trade_permission}，背景={context_summary.get('regime', 'unknown')}。"
            "该摘要不是交易建议。"
        )
        return WeakModelAggregationSummary(
            weak_model_aggregation_id=build_weak_model_aggregation_id(weak_model_run_id),
            weak_model_run_id=weak_model_run_id,
            pipeline_run_id=input_data.pipeline_run_id,
            strategy_signal_run_id=input_data.strategy_signal_run_id,
            snapshot_id=input_data.snapshot_id,
            symbol=input_data.symbol,
            base_interval=input_data.base_interval,
            higher_interval=input_data.higher_interval,
            kline_slot_utc=input_data.kline_slot_utc,
            directional_score=directional_score,
            directional_bias=directional_bias,
            directional_confidence=directional_confidence,
            risk_level=risk_level,
            trade_permission=trade_permission,
            veto_triggered=veto_triggered,
            supporting_factors=supporting,
            opposing_factors=opposing,
            conflict_factors=conflict,
            low_confidence_factors=low_confidence,
            veto_factors=veto_factors,
            context_summary=context_summary,
            summary_text=summary_text,
            details={
                "active_output_count": len(active_outputs),
                "observe_only_output_count": len(outputs) - len(active_outputs),
                "not_trading_advice": True,
            },
        )


def _directional_summary(outputs: tuple[WeakModelOutput, ...]) -> tuple[float, float, str]:
    numerator = 0.0
    denominator = 0.0
    for output in outputs:
        if output.model_role != WeakModelRole.DIRECTIONAL.value or output.signal_score is None:
            continue
        weight = output.confidence * output.static_weight
        numerator += output.signal_score * weight
        denominator += weight
    if denominator <= 0:
        return 0.0, 0.0, "neutral"
    score = numerator / denominator
    bias = "bullish" if score >= 0.35 else "bearish" if score <= -0.35 else "neutral"
    return score, min(1.0, denominator), bias


def _risk_summary(outputs: tuple[WeakModelOutput, ...]) -> tuple[str, str, bool, tuple[str, ...]]:
    risk_outputs = tuple(output for output in outputs if output.model_role == WeakModelRole.RISK.value)
    veto_factors = tuple(output.model_key for output in risk_outputs if output.can_veto and output.veto_triggered)
    if veto_factors:
        return "extreme", "block", True, veto_factors
    highest = "low"
    for output in risk_outputs:
        level = output.risk_level or "low"
        if RISK_ORDER.get(level, 0) > RISK_ORDER.get(highest, 0):
            highest = level
    permission = "caution" if highest == "high" else "block" if highest == "extreme" else "allow"
    return highest, permission, False, ()


def _confirmation_summary(outputs: tuple[WeakModelOutput, ...], directional_bias: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    target = "long" if directional_bias == "bullish" else "short" if directional_bias == "bearish" else "neutral"
    supporting: list[str] = []
    opposing: list[str] = []
    missing: list[str] = []
    for output in outputs:
        if output.model_role != WeakModelRole.CONFIRMATION.value:
            continue
        direction = output.supports_direction or "none"
        if direction in {"none", "neutral"} or target == "neutral":
            missing.append(output.model_key)
        elif direction == target:
            supporting.append(output.model_key)
        else:
            opposing.append(output.model_key)
    return tuple(supporting), tuple(opposing), tuple(missing + opposing)


def _context_summary(
    outputs: tuple[WeakModelOutput, ...],
    profiles_by_key: dict[str, WeakModelProfile],
) -> dict[str, object]:
    contexts = tuple(
        output
        for output in outputs
        if output.status == WeakModelResultStatus.SUCCESS
        and output.model_role == WeakModelRole.CONTEXT.value
        and profiles_by_key.get(output.model_key) is not None
        and (
            profiles_by_key[output.model_key].participates_in_aggregation
            or profiles_by_key[output.model_key].maturity_stage == "observe_only"
        )
    )
    if not contexts:
        return {
            "regime": "unknown",
            "context_score": 0.0,
            "confidence": 0.0,
            "source_model_key": "",
            "source_maturity_stage": "",
        }
    best = max(contexts, key=lambda output: output.confidence * (output.context_score or 0.0))
    best_profile = profiles_by_key[best.model_key]
    return {
        "regime": best.context_regime or "unknown",
        "context_score": best.context_score or 0.0,
        "confidence": best.confidence,
        "source_model_key": best.model_key,
        "source_maturity_stage": best_profile.maturity_stage,
        "source_participation_mode": best_profile.participation_mode,
    }


__all__ = ["WeakModelAggregator"]
