"""Chinese Hermes formatter for stage-19 model analysis review gate.

This file belongs to `app/model_analysis`. It formats a compact, Chinese
visible body for Hermes alerts.

Called by `app/model_analysis/service.py`.
External services: none. MySQL: none. Redis: none. Real model calls: none.
Trading execution: none.
"""

from __future__ import annotations

from app.model_analysis.types import ModelAnalysisServiceResult


def build_model_analysis_visible_body(result: ModelAnalysisServiceResult) -> str:
    """Build the user-visible Hermes body for one model review result.

    Parameters: compact service result.
    Return value: Chinese text suitable for `WECHAT_VISIBLE_BODY_DETAIL_KEY`.
    Failure scenarios: none expected.
    External effects: none.
    """

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
            f"- 是否需要人工审核：{'是' if result.human_review_required else '否'}",
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
