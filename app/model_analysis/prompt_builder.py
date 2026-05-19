"""Prompt summary builder for stage-19 model analysis review gate.

This file belongs to `app/model_analysis`. It extracts a compact, bounded
review summary from a stage-18 `analysis_material_pack` row.

Called by `app/model_analysis/service.py`.
External services: none. MySQL: none. Redis: none. Hermes: none. Real model
calls: none. Trading execution: none.

The builder intentionally does not store or persist the full prompt. The
service may pass `prompt_text` to the mock provider, while persistence keeps
only `input_summary_json` plus size counters and a hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from app.core.config import AppSettings
from app.model_analysis.types import PromptBuildResult

STRATEGY_SUMMARY_KEYS = (
    "strategy_name",
    "strategy_version",
    "strategy_role",
    "enabled",
    "status",
    "analysis_hypothesis_direction",
    "evidence_quality",
    "risk_level",
    "summary",
    "reason_codes",
    "missing_evidence",
)

REVIEW_INSTRUCTIONS = (
    "你是材料审查员，不是交易员。\n"
    "你不能给最终交易建议，不能给入场价、止损价、止盈价、仓位或杠杆。\n"
    "你只能审查材料完整性、证据充分性、逻辑自洽性、风险可接受性、冲突程度，以及是否需要人工审核。\n"
    "输出必须符合 review_schema_v1。证据不足时必须返回 require_more_evidence 或 human_review_required，不能编造结论。\n"
)


def build_model_review_prompt(material_pack: Any, *, settings: AppSettings) -> PromptBuildResult:
    """Build a bounded review prompt from one successful material pack.

    Parameters: `material_pack` is an ORM row or test object with stage-18
    material fields; `settings` supplies item and length limits.
    Return value: compact prompt input and counters.
    Failure scenarios: invalid JSON fields are tolerated as empty summaries so
    the schema/provider can require more evidence.
    External effects: none.
    """

    material_json = _json_field(material_pack, "material_json")
    summary_json = _json_field(material_pack, "summary_json")
    question_json = _json_field(material_pack, "question_json")
    validation_plan_json = _json_field(material_pack, "validation_plan_json")

    strategy_summaries, total_strategy_count = _strategy_summaries(
        (material_json, summary_json),
        max_strategy_items=settings.model_review_max_strategy_items,
        max_reason_items=settings.model_review_max_reason_items_per_strategy,
    )
    truncated_strategy_count = max(total_strategy_count - len(strategy_summaries), 0)

    input_summary: dict[str, Any] = {
        "material_pack_id": str(getattr(material_pack, "material_pack_id", "")),
        "aggregation_run_id": str(getattr(material_pack, "aggregation_run_id", "")),
        "strategy_signal_run_id": str(getattr(material_pack, "strategy_signal_run_id", "")),
        "snapshot_id": str(getattr(material_pack, "snapshot_id", "")),
        "symbol": str(getattr(material_pack, "symbol", "")),
        "base_interval": str(getattr(material_pack, "base_interval", "")),
        "higher_interval": str(getattr(material_pack, "higher_interval", "")),
        "material_status": str(getattr(material_pack, "status", "")),
        "aggregation_version": str(getattr(material_pack, "aggregation_version", "")),
        "material_schema_version": str(getattr(material_pack, "material_schema_version", "")),
        "strategy_item_count": len(strategy_summaries),
        "truncated_strategy_count": truncated_strategy_count,
        "strategy_summaries": strategy_summaries,
        "material_summary": _compact_mapping(_high_level_summary(summary_json)),
        "review_questions": _compact_list(question_json.get("questions", []) if isinstance(question_json, dict) else []),
        "validation_focus": _compact_mapping(validation_plan_json),
        "not_trading_advice": True,
    }
    prompt_input = {
        "instructions": REVIEW_INSTRUCTIONS,
        "input_summary": input_summary,
    }
    prompt_text = json.dumps(prompt_input, ensure_ascii=False, sort_keys=True, default=str)
    input_hash = hashlib.sha256(json.dumps(input_summary, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return PromptBuildResult(
        prompt_text=prompt_text,
        input_summary=input_summary,
        input_material_hash=input_hash,
        input_char_count=len(prompt_text),
        input_byte_count=len(prompt_text.encode("utf-8")),
        strategy_item_count=len(strategy_summaries),
        truncated_strategy_count=truncated_strategy_count,
    )


def _json_field(row: Any, field_name: str) -> Mapping[str, Any]:
    value = getattr(row, field_name, {})
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def _strategy_summaries(
    sources: tuple[Mapping[str, Any], ...],
    *,
    max_strategy_items: int,
    max_reason_items: int,
) -> tuple[list[dict[str, Any]], int]:
    collected: list[Mapping[str, Any]] = []
    for source in sources:
        _collect_strategy_like_items(source, collected, depth=0)

    unique_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in collected:
        compact = _compact_strategy_item(item, max_reason_items=max_reason_items)
        identity = (
            str(compact.get("strategy_name", "")),
            str(compact.get("strategy_version", "")),
            str(compact.get("strategy_role", "")),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique_items.append(compact)
    return unique_items[:max_strategy_items], len(unique_items)


def _collect_strategy_like_items(value: Any, collected: list[Mapping[str, Any]], *, depth: int) -> None:
    if depth > 8:
        return
    if isinstance(value, Mapping):
        if _looks_like_strategy_item(value):
            collected.append(value)
            return
        for child in value.values():
            _collect_strategy_like_items(child, collected, depth=depth + 1)
        return
    if isinstance(value, list):
        for child in value:
            _collect_strategy_like_items(child, collected, depth=depth + 1)


def _looks_like_strategy_item(value: Mapping[str, Any]) -> bool:
    keys = set(value.keys())
    return "strategy_name" in keys or {"strategy_version", "strategy_role"} <= keys or "analysis_hypothesis_direction" in keys


def _compact_strategy_item(value: Mapping[str, Any], *, max_reason_items: int) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in STRATEGY_SUMMARY_KEYS:
        raw_value = value.get(key)
        if key in {"reason_codes", "missing_evidence"}:
            compact_value = _compact_list(raw_value, max_items=max_reason_items)
        else:
            compact_value = _compact_scalar(raw_value)
        if key == "enabled" and compact_value is True:
            continue
        if key == "status" and compact_value == "success":
            continue
        if compact_value not in (None, "", []):
            compact[key] = compact_value
    return compact


def _high_level_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    skipped = {"strategy_summaries", "strategies", "strategy_results"}
    return {str(key): raw_value for key, raw_value in value.items() if str(key) not in skipped}


def _compact_mapping(value: Mapping[str, Any], *, max_items: int = 12) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for index, (key, raw_value) in enumerate(value.items()):
        if index >= max_items:
            compact["truncated"] = True
            break
        if isinstance(raw_value, Mapping):
            compact[str(key)] = _compact_mapping(raw_value, max_items=6)
        elif isinstance(raw_value, list):
            compact[str(key)] = _compact_list(raw_value, max_items=6)
        else:
            compact[str(key)] = _compact_scalar(raw_value)
    return compact


def _compact_list(value: Any, *, max_items: int = 5) -> list[Any]:
    if not isinstance(value, list):
        return []
    result: list[Any] = []
    for item in value[:max_items]:
        if isinstance(item, Mapping):
            result.append(_compact_mapping(item, max_items=6))
        elif isinstance(item, list):
            result.append(_compact_list(item, max_items=max_items))
        else:
            result.append(_compact_scalar(item))
    return result


def _compact_scalar(value: Any, *, max_chars: int = 300) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


__all__ = ["build_model_review_prompt"]
