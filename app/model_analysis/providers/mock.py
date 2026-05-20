"""Deterministic mock provider for stage-19A model analysis.

This file belongs to `app/model_analysis/providers`. It returns a stable,
schema-shaped review result for tests and local dry-runs.

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
        """Review compact material summary using fixed local rules.

        Parameters: bounded prompt summary from `prompt_builder`.
        Return value: structured provider result with size counters.
        Failure scenarios: none expected; tests may inject oversized or invalid
        output through `override_response`.
        External services and trading execution: none.
        """

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
    strategies = input_summary.get("strategy_summaries", [])
    if not isinstance(strategies, list) or not strategies:
        return _base_output(
            review_decision=ReviewDecision.REQUIRE_MORE_EVIDENCE.value,
            evidence_quality="insufficient",
            logic_consistency="unknown",
            risk_acceptability="unknown",
            strategy_conflict_level="unknown",
            missing_evidence=["缺少可审查的策略摘要。"],
            validation_focus=["补齐第 18 阶段材料包中的动态策略摘要。"],
        )

    risk_values = {str(item.get("risk_level", "")).lower() for item in strategies if isinstance(item, Mapping)}
    evidence_values = {
        str(item.get("evidence_quality", "")).lower()
        for item in strategies
        if isinstance(item, Mapping)
    }
    hypothesis_directions = {
        str(item.get("analysis_hypothesis_direction", "")).lower()
        for item in strategies
        if isinstance(item, Mapping) and item.get("analysis_hypothesis_direction")
    }
    missing_evidence = [
        item.get("missing_evidence")
        for item in strategies
        if isinstance(item, Mapping) and item.get("missing_evidence")
    ]
    if len(hypothesis_directions) > 1:
        return _base_output(
            review_decision=ReviewDecision.HUMAN_REVIEW_REQUIRED.value,
            evidence_quality="moderate",
            logic_consistency="conflicting",
            risk_acceptability="caution",
            strategy_conflict_level="high",
            risk_warnings=["材料中的分析假设方向存在明显冲突，需要人工判断冲突解释。"],
            human_review_questions=["这些分析假设冲突是否来自不同周期、不同证据质量或材料缺口？"],
            validation_focus=["复核分析假设冲突、证据来源和风险说明。"],
        )
    if {"high", "extreme"} & risk_values:
        return _base_output(
            review_decision=ReviewDecision.HUMAN_REVIEW_REQUIRED.value,
            evidence_quality="moderate",
            logic_consistency="minor_conflict",
            risk_acceptability="caution",
            strategy_conflict_level="medium",
            risk_warnings=["材料中存在高风险标记，需要人工复核。"],
            human_review_questions=["高风险来源是否有独立证据支持？"],
            validation_focus=["复核风险来源、证据质量和冲突解释。"],
        )
    if {"weak", "insufficient"} & evidence_values or missing_evidence:
        return _base_output(
            review_decision=ReviewDecision.REQUIRE_MORE_EVIDENCE.value,
            evidence_quality="weak",
            logic_consistency="unknown",
            risk_acceptability="unknown",
            strategy_conflict_level="unknown",
            missing_evidence=["存在证据较弱或缺失的策略摘要。"],
            validation_focus=["补齐缺失证据后再进入后续审查。"],
        )
    return _base_output(
        review_decision=ReviewDecision.WAIT.value,
        evidence_quality="moderate",
        logic_consistency="consistent",
        risk_acceptability="caution",
        strategy_conflict_level="low",
        validation_focus=["继续人工检查材料完整性、冲突点和风险说明。"],
    )


def _base_output(
    *,
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
) -> dict[str, Any]:
    return {
        "review_decision": review_decision,
        "human_review_required": (
            review_decision == ReviewDecision.HUMAN_REVIEW_REQUIRED.value
            if human_review_required is None
            else human_review_required
        ),
        "evidence_quality": evidence_quality,
        "logic_consistency": logic_consistency,
        "risk_acceptability": risk_acceptability,
        "strategy_conflict_level": strategy_conflict_level,
        "missing_evidence": missing_evidence or [],
        "rejection_reasons": [],
        "risk_warnings": risk_warnings or [],
        "conditions_to_reconsider": ["需要未来策略阶段和人工复核后再考虑。"],
        "human_review_questions": human_review_questions or [],
        "validation_focus": validation_focus or [],
        "summary_text": "这是 19A mock provider 的材料审查结果，不是最终交易建议。",
        "not_trading_advice": True,
        "not_trading_advice_text": "这是大模型审查结果，不是最终交易建议，也不是可执行交易信号。",
        "is_final_trading_advice": False,
        "is_trading_signal": False,
        "is_executable": False,
        "auto_trading_allowed": False,
    }


__all__ = ["MockModelReviewProvider", "build_custom_mock_provider"]
