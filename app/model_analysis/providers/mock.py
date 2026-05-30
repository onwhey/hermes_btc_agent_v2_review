"""Deterministic mock provider for stage-24C model analysis.

This file belongs to `app/model_analysis/providers`. It returns a stable,
schema-shaped review result for tests and local dry-runs. The mock reviews the
public strategy-evidence bridge only; it does not call any large model, read
private strategy payloads, send Hermes, or perform trading execution.

Called by `app/model_analysis/service.py`.
External services: none. MySQL: none. Redis: none. Hermes: none. Real model
calls: none. Trading execution: none.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from app.model_analysis.types import (
    MODEL_REVIEW_MOCK_MODEL_NAME,
    MODEL_REVIEW_MOCK_MODEL_VERSION,
    MODEL_REVIEW_PROVIDER_MOCK,
    ModelProviderResult,
    PromptBuildResult,
    ReviewDecision,
)


class MockModelReviewProvider:
    """Return deterministic structured review output without external calls."""

    provider_name = MODEL_REVIEW_PROVIDER_MOCK
    model_name = MODEL_REVIEW_MOCK_MODEL_NAME
    model_version = MODEL_REVIEW_MOCK_MODEL_VERSION

    def __init__(self, *, override_response: Mapping[str, Any] | None = None) -> None:
        self._override_response = dict(override_response) if override_response is not None else None

    def review_material(self, prompt: PromptBuildResult) -> ModelProviderResult:
        """Review compact material summary using fixed local rules."""

        output = self._override_response or _build_mock_output(prompt.input_summary)
        output_text = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
        return ModelProviderResult(
            output=output,
            output_char_count=len(output_text),
            output_byte_count=len(output_text.encode("utf-8")),
        )


def build_custom_mock_provider(response_factory: Callable[[PromptBuildResult], Mapping[str, Any]]) -> Any:
    """Build a small provider object for tests without changing service code."""

    class CustomMockProvider:
        provider_name = MODEL_REVIEW_PROVIDER_MOCK
        model_name = MODEL_REVIEW_MOCK_MODEL_NAME
        model_version = MODEL_REVIEW_MOCK_MODEL_VERSION

        def review_material(self, prompt: PromptBuildResult) -> ModelProviderResult:
            output = dict(response_factory(prompt))
            output_text = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
            return ModelProviderResult(
                output=output,
                output_char_count=len(output_text),
                output_byte_count=len(output_text.encode("utf-8")),
            )

    return CustomMockProvider()


def _build_mock_output(input_summary: Mapping[str, Any]) -> dict[str, Any]:
    strategy_evidence = input_summary.get("strategy_evidence")
    if not isinstance(strategy_evidence, Mapping) or not strategy_evidence:
        return _base_output(
            agreement_with_23f="insufficient_evidence",
            review_decision="need_more_evidence",
            evidence_quality="insufficient",
            logic_consistency="unknown",
            risk_acceptability="unknown",
            strategy_conflict_level="unknown",
            missing_evidence=["缺少可审查的 23F 策略证据链。"],
            validation_focus=["补齐 material_json.strategy_evidence 后再审查。"],
            recommendation_to_advice_layer="need_more_evidence",
        )

    decision_readiness = str(
        strategy_evidence.get("decision_readiness", "") or input_summary.get("decision_readiness", "")
    )
    evidence_missing = input_summary.get("evidence_missing")
    conflicts = input_summary.get("strategy_conflict_summary")
    risk_gate = input_summary.get("risk_gate_summary")
    risk_gate_text = json.dumps(risk_gate, ensure_ascii=False, sort_keys=True, default=str).lower()
    if "block" in risk_gate_text or "reject" in risk_gate_text:
        return _base_output(
            agreement_with_23f="partial",
            review_decision="risk_reject",
            evidence_quality="moderate",
            logic_consistency="minor_conflict",
            risk_acceptability="unacceptable",
            strategy_conflict_level="high",
            risk_warnings=["23F 风控摘要存在阻断或拒绝信号，建议后续建议层不要采纳为可执行方向。"],
            disputed_strategy_points=["risk_gate_summary"],
            validation_focus=["复核 23F 风控阻断范围和证据引用。"],
            recommendation_to_advice_layer="risk_reject",
        )
    if conflicts:
        return _base_output(
            agreement_with_23f="partial",
            review_decision="downgrade",
            evidence_quality="moderate",
            logic_consistency="minor_conflict",
            risk_acceptability="caution",
            strategy_conflict_level="medium",
            risk_warnings=["23F 策略冲突摘要不为空，需要后续层保守处理。"],
            disputed_strategy_points=["strategy_conflict_summary"],
            validation_focus=["复核冲突来源、参与策略权重和否决范围。"],
            recommendation_to_advice_layer="downgrade",
        )
    if evidence_missing or decision_readiness in {"needs_more_evidence", "insufficient_evidence"}:
        return _base_output(
            agreement_with_23f="insufficient_evidence",
            review_decision="need_more_evidence",
            evidence_quality="weak",
            logic_consistency="unknown",
            risk_acceptability="unknown",
            strategy_conflict_level="unknown",
            missing_evidence=["23F evidence_missing 不为空或 readiness 表示证据不足。"],
            validation_focus=["补齐缺失角色证据后再进入建议层。"],
            recommendation_to_advice_layer="need_more_evidence",
        )
    return _base_output(
        agreement_with_23f="partial",
        review_decision=ReviewDecision.WAIT.value,
        evidence_quality="moderate",
        logic_consistency="consistent",
        risk_acceptability="caution",
        strategy_conflict_level="low",
        validation_focus=["继续人工检查 23F 证据链完整性、冲突点和风控摘要。"],
        recommendation_to_advice_layer="wait",
    )


def _base_output(
    *,
    agreement_with_23f: str,
    review_decision: str,
    evidence_quality: str,
    logic_consistency: str,
    risk_acceptability: str,
    strategy_conflict_level: str,
    missing_evidence: list[str] | None = None,
    risk_warnings: list[str] | None = None,
    human_review_questions: list[str] | None = None,
    validation_focus: list[str] | None = None,
    human_review_required: bool | None = None,
    disputed_strategy_points: list[str] | None = None,
    recommendation_to_advice_layer: str | None = None,
) -> dict[str, Any]:
    strongest_counterargument = "23F 的候选偏向仍可能来自不完整证据，不能直接当成事实或交易方向。"
    main_objection = "需要先确认 23F 的缺失证据、冲突摘要和风控范围是否足以支撑后续建议层。"
    recommendation = recommendation_to_advice_layer or (
        "need_more_evidence"
        if review_decision in {"need_more_evidence", ReviewDecision.REQUIRE_MORE_EVIDENCE.value}
        else "wait"
    )
    return {
        "agreement_with_23f": agreement_with_23f,
        "review_decision": review_decision,
        "human_review_required": (
            review_decision
            in {
                ReviewDecision.HUMAN_REVIEW_REQUIRED.value,
                ReviewDecision.REQUIRE_MORE_EVIDENCE.value,
                "need_more_evidence",
                "downgrade",
                "risk_reject",
            }
            if human_review_required is None
            else human_review_required
        ),
        "main_objection": main_objection,
        "strongest_counterargument": strongest_counterargument,
        "missing_evidence": missing_evidence or [],
        "disputed_strategy_points": disputed_strategy_points or [],
        "overestimated_evidence": ["23F candidate_bias 只能作为策略域候选，不是事实结论。"],
        "underestimated_evidence": ["risk_gate_summary 和 evidence_missing 应优先进入建议层审查。"],
        "scenario_review": {
            "main_scenario": "若 23F 证据链完整且冲突可解释，后续建议层可继续审查候选场景。",
            "opposite_scenario": "若相反证据更强，候选偏向需要降级为 wait。",
            "risk_scenario": "若风控摘要阻断当前候选，后续建议层不得生成可执行结构。",
            "no_trade_scenario": "若证据缺失或时间锚点异常，应保持 wait 或 need_more_evidence。",
        },
        "discipline_check": {
            "chasing_risk": "caution",
            "risk_reward_quality": "unclear",
            "stop_condition_clarity": "unclear",
            "overtrading_risk": "caution",
        },
        "recommendation_to_advice_layer": recommendation,
        "evidence_refs": [
            "strategy_evidence.strategy_evidence_summary",
            "strategy_evidence.decision_source_chain",
            "strategy_evidence.risk_gate_summary",
        ],
        "time_freshness_assessment": "已基于 material pack 中的时间锚点审查，范围限定在材料包内。",
        "boundary_flags": [],
        "quality_flags": [],
        "confidence": "medium" if evidence_quality in {"moderate", "strong"} else "low",
        "summary": "这是 24C mock 审查结果，只审查 23F 策略证据链，不生成最终交易建议。",
        "evidence_quality": evidence_quality,
        "logic_consistency": logic_consistency,
        "risk_acceptability": risk_acceptability,
        "strategy_conflict_level": strategy_conflict_level,
        "rejection_reasons": [],
        "risk_warnings": risk_warnings or [],
        "conditions_to_reconsider": ["需要后续建议层和人工复核后再考虑。"],
        "human_review_questions": human_review_questions or [],
        "validation_focus": validation_focus or [],
        "summary_text": "这是 24C mock provider 的策略证据链审查结果，不是最终交易建议。",
        "not_trading_advice": True,
        "not_trading_advice_text": "这是大模型审查结果，不是最终交易建议，也不是可执行交易信号。",
        "is_final_trading_advice": False,
        "is_trading_signal": False,
        "is_executable": False,
        "auto_trading_allowed": False,
    }


__all__ = ["MockModelReviewProvider", "build_custom_mock_provider"]
