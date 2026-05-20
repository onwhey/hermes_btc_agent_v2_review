"""Chinese Hermes formatter for stage-19 model analysis review gate.

This file belongs to `app/model_analysis`. It formats only compact Chinese
visible text for Hermes alerts.

Called by `app/model_analysis/service.py`.
External services: none. MySQL: none. Redis: none. Real model calls: none.
Trading execution: none.
"""

from __future__ import annotations

from app.model_analysis.types import ModelAnalysisServiceResult, ModelAnalysisStatus


def build_model_analysis_visible_body(result: ModelAnalysisServiceResult) -> str:
    """Build the user-visible Hermes body for one model review result."""

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


def build_model_analysis_oversized_response_visible_body(result: ModelAnalysisServiceResult) -> str:
    """Build Chinese Hermes body for oversized model-provider responses."""

    processing_result = "已阻断 / 已隔离保存 / 未生成正式审查结果"
    if result.status == ModelAnalysisStatus.SUCCESS:
        processing_result = "已隔离保存 / 已生成结构化审查结果"
    provider = result.details.get("provider", "deepseek") if result.details else "deepseek"
    return "\n".join(
        [
            "【标题】BTC 大模型审查返回过长",
            "",
            "【处理说明】模型返回内容触发长度安全边界，系统已按隔离规则处理。",
            f"- model_key：{result.model_key or 'unknown'}",
            f"- provider：{provider}",
            f"- model_name：{result.details.get('model_name', 'unknown') if result.details else 'unknown'}",
            f"- material_pack_id：{result.material_pack_id}",
            f"- model_analysis_run_id：{result.model_analysis_run_id}",
            f"- raw_response_char_count：{result.raw_response_char_count}",
            f"- raw_response_byte_count：{result.raw_response_byte_count}",
            f"- 处理结果：{processing_result}",
            f"- trace_id：{result.trace_id}",
            "",
            "这不是最终交易建议。",
            "本阶段未自动交易。",
            "本阶段未生成订单。",
            "本阶段未给出仓位或杠杆。",
        ]
    )


def build_model_analysis_artifact_write_failed_visible_body(result: ModelAnalysisServiceResult) -> str:
    """Build Chinese Hermes body for provider artifact isolation failures."""

    provider = result.details.get("provider", "deepseek") if result.details else "deepseek"
    model_name = result.details.get("model_name", "unknown") if result.details else "unknown"
    formal_result_text = "是" if result.model_analysis_result_id else "否"
    return "\n".join(
        [
            "【标题】BTC 大模型审查 artifact 写入失败",
            "",
            "【处理说明】模型返回未能完整隔离保存，系统已停止写入正式审查结果。",
            f"- 是否生成正式审查结果：{formal_result_text}",
            f"- model_key：{result.model_key or 'unknown'}",
            f"- provider：{provider}",
            f"- model_name：{model_name}",
            f"- material_pack_id：{result.material_pack_id}",
            f"- model_analysis_run_id：{result.model_analysis_run_id}",
            f"- trace_id：{result.trace_id}",
            f"- error_code：{result.error_code or 'artifact_write_failed'}",
            f"- error_message：{result.error_message or ''}",
            "",
            "这不是最终交易建议。",
            "本阶段未自动交易。",
            "本阶段未生成订单。",
            "本阶段未给出仓位或杠杆。",
        ]
    )


def build_model_analysis_provider_call_failed_visible_body(result: ModelAnalysisServiceResult) -> str:
    """Build Chinese Hermes body for real model provider request failures."""

    provider = result.details.get("provider", "unknown") if result.details else "unknown"
    model_name = result.details.get("model_name", "unknown") if result.details else "unknown"
    model_key = result.model_key or (result.details.get("model_key", "unknown") if result.details else "unknown")
    return "\n".join(
        [
            "【标题】BTC 大模型请求失败",
            "",
            "【处理说明】真实大模型请求失败，系统已记录本次失败，不写入正式审查结果。",
            f"- provider：{provider}",
            f"- model_key：{model_key}",
            f"- model_name：{model_name}",
            f"- material_pack_id：{result.material_pack_id}",
            f"- model_analysis_run_id：{result.model_analysis_run_id}",
            f"- error_code：{result.error_code or 'provider_call_failed'}",
            f"- error_message：{result.error_message or ''}",
            f"- trace_id：{result.trace_id}",
            "- 处理结果：未生成正式审查结果",
            "",
            "这不是最终交易建议。",
            "本阶段未自动交易。",
            "本阶段未生成订单。",
            "本阶段未给出仓位或杠杆。",
        ]
    )


__all__ = [
    "build_model_analysis_artifact_write_failed_visible_body",
    "build_model_analysis_oversized_response_visible_body",
    "build_model_analysis_provider_call_failed_visible_body",
    "build_model_analysis_visible_body",
]
