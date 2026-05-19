"""Chinese Hermes formatter for stage-19 model analysis review gate.

This file belongs to `app/model_analysis`. It formats only compact Chinese
visible text for Hermes alerts.

Called by `app/model_analysis/service.py`.
External services: none. MySQL: none. Redis: none. Real model calls: none.
Trading execution: none.
"""

from __future__ import annotations

from app.model_analysis.types import ModelAnalysisServiceResult


def build_model_analysis_visible_body(result: ModelAnalysisServiceResult) -> str:
    """Build the user-visible Hermes body for one model review result.

    Parameters: compact service result.
    Return value: Chinese text suitable for Hermes visible body.
    Failure scenarios: none expected.
    External effects: none; this function does not send Hermes by itself.
    """

    human_review_text = "是" if result.human_review_required else "否"
    extra_human_review_line = (
        "本次审查需要人工进一步判断。"
        if result.human_review_required
        else "本次审查未标记为必须人工进一步判断。"
    )
    return "\n".join(
        [
            "【标题】BTC 大模型审查候选结果",
            "",
            "【摘要】这是大模型审查结果，不是最终交易建议。",
            "本阶段未自动交易。",
            "本阶段未生成订单。",
            "本阶段未给出仓位或杠杆。",
            "",
            "【审查结论】",
            f"- 当前 review_decision：{result.review_decision or 'unknown'}",
            f"- 证据质量：{result.evidence_quality or 'unknown'}",
            f"- 风险接受度：{result.risk_acceptability or 'unknown'}",
            f"- 策略冲突程度：{result.strategy_conflict_level or 'unknown'}",
            f"- 是否需要人工判断：{human_review_text}",
            extra_human_review_line,
            "",
            "【边界声明】",
            "这是审查门控输出，不是交易信号。",
            "这不是最终交易建议。",
            "本阶段不会生成可执行操作。",
            "",
            f"trace_id：{result.trace_id}",
        ]
    )


__all__ = ["build_model_analysis_visible_body"]
