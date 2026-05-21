"""Review-input fingerprint helpers for stage-20A reuse checks.

This file belongs to `app/model_review_aggregation`. It extracts compact,
structured facts from stage-18 material packs and stage-19 run metadata so the
service can decide whether an old model review is reusable.

Called by `app/model_review_aggregation/service.py`.
External services: none. MySQL: none in this file. Redis: none. Hermes: none.
Large-model calls: none. Trading execution: none.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from app.market_data.kline_constants import KLINE_1D_INTERVAL_MS, KLINE_4H_INTERVAL_MS
from app.model_review_aggregation.schema import REVIEW_INPUT_FINGERPRINT_VERSION

MAX_CANDIDATE_SUMMARY_ITEMS = 8
MAX_CANDIDATE_FIELD_CHARS = 48
MAX_CANDIDATE_ITEM_CHARS = 160
MAX_CANDIDATE_SUMMARY_CHARS = 640
DIRECT_PRICE_FIELDS = ("price", "level", "low", "high", "lower", "upper", "start", "end")
ZONE_FIELDS = ("zone", "range", "interval", "area")


@dataclass(frozen=True)
class MaterialFingerprint:
    """Compact material fingerprint used to compare stage-18 packs."""

    fingerprint: str
    details: Mapping[str, Any]
    base_open_time_end_ms: int | None


def build_material_fingerprint(material_pack: Any) -> MaterialFingerprint:
    """Return the stage-20A material fingerprint for one material pack.

    Parameters: one `analysis_material_pack` row or row-like test object.
    Return value: fingerprint hash, compact details, and latest base Kline open
    time used by the pack.
    Failure scenarios: malformed JSON is treated as an empty object, making the
    fingerprint conservative rather than crashing reuse evaluation.
    External effects: none.
    """

    material_json = _json_mapping(getattr(material_pack, "material_json", None))
    summary_json = _json_mapping(getattr(material_pack, "summary_json", None))
    data_window_json = _json_mapping(getattr(material_pack, "data_window_json", None))
    support_resistance = _mapping(material_json.get("support_resistance"))
    support_candidates = _list_value(support_resistance.get("support_candidates"))
    resistance_candidates = _list_value(support_resistance.get("resistance_candidates"))
    support_candidates_summary = _summarize_price_candidates(support_candidates)
    resistance_candidates_summary = _summarize_price_candidates(resistance_candidates)
    details = {
        "fingerprint_version": REVIEW_INPUT_FINGERPRINT_VERSION,
        "symbol": str(getattr(material_pack, "symbol", "") or material_json.get("symbol", "")),
        "base_interval": str(getattr(material_pack, "base_interval", "") or material_json.get("base_interval", "")),
        "higher_interval": str(getattr(material_pack, "higher_interval", "") or material_json.get("higher_interval", "")),
        "analysis_hypothesis_direction": _first_text(
            summary_json.get("analysis_hypothesis_direction"),
            material_json.get("analysis_hypothesis_direction"),
        ),
        "risk_gate_status": _first_text(summary_json.get("risk_gate_status"), material_json.get("risk_gate_status")),
        "risk_level": _first_text(summary_json.get("risk_level"), material_json.get("risk_level")),
        "conflict_level": _first_text(
            summary_json.get("conflict_level"),
            _mapping(material_json.get("strategy_conflict_points")).get("conflict_level"),
        ),
        "structure_state": _first_text(
            summary_json.get("structure_state"),
            _mapping(material_json.get("swing")).get("structure_state"),
        ),
        "volatility_state": _first_text(
            summary_json.get("volatility_state"),
            _mapping(material_json.get("volatility")).get("volatility_state"),
        ),
        "support_candidate_count": len(support_candidates),
        "support_candidates_summary": support_candidates_summary,
        "resistance_candidate_count": len(resistance_candidates),
        "resistance_candidates_summary": resistance_candidates_summary,
        "hypothesis_invalidation_check": _bounded_text(material_json.get("hypothesis_invalidation_check")),
        "hypothesis_target_observation_zone": _bounded_text(material_json.get("hypothesis_target_observation_zone")),
        "base_open_time_end_ms": _optional_int(data_window_json.get("base_open_time_end_ms")),
    }
    fingerprint_details = dict(details)
    fingerprint_details.pop("base_open_time_end_ms", None)
    return MaterialFingerprint(
        fingerprint=_hash_mapping(fingerprint_details),
        details=details,
        base_open_time_end_ms=_optional_int(data_window_json.get("base_open_time_end_ms")),
    )


def build_review_input_fingerprint(material_pack: Any, model_run: Any | None) -> tuple[str, Mapping[str, Any]]:
    """Return the combined material + model metadata fingerprint.

    Parameters: stage-18 material row and optional stage-19 run row.
    Return value: SHA-256 hash plus compact JSON-safe details.
    Failure scenarios: none expected; missing model metadata is represented by
    empty strings so reuse can stay conservative at the service layer.
    External effects: none.
    """

    material = build_material_fingerprint(material_pack)
    details = {
        "fingerprint_version": REVIEW_INPUT_FINGERPRINT_VERSION,
        "material_summary_hash": material.fingerprint,
        "material": dict(material.details),
        "model_key": _text_attr(model_run, "model_key"),
        "model_role": _text_attr(model_run, "model_role"),
        "profile_hash": _text_attr(model_run, "profile_hash"),
        "prompt_template_hash": _text_attr(model_run, "prompt_template_hash"),
        "prompt_template_version": _text_attr(model_run, "prompt_template_version"),
        "review_schema_version": _text_attr(model_run, "review_schema_version"),
    }
    return _hash_mapping(details), details


def base_interval_to_ms(interval_value: str) -> int | None:
    """Return known base interval length in milliseconds."""

    value = str(interval_value).strip().lower()
    if value == "4h":
        return KLINE_4H_INTERVAL_MS
    if value == "1d":
        return KLINE_1D_INTERVAL_MS
    if value.endswith("h") and value[:-1].isdigit():
        return int(value[:-1]) * 60 * 60 * 1000
    if value.endswith("d") and value[:-1].isdigit():
        return int(value[:-1]) * 24 * 60 * 60 * 1000
    return None


def calculate_reuse_base_bars(*, current_open_time_ms: int | None, previous_open_time_ms: int | None, interval_ms: int | None) -> int | None:
    """Return how many base interval bars passed between two material packs."""

    if current_open_time_ms is None or previous_open_time_ms is None or interval_ms is None or interval_ms <= 0:
        return None
    delta = int(current_open_time_ms) - int(previous_open_time_ms)
    if delta < 0:
        return None
    return delta // interval_ms


def _summarize_price_candidates(candidates: list[Any]) -> list[str]:
    """Return bounded, stable summaries for support/resistance candidates.

    The fingerprint must react to key price-zone changes, but it must not copy
    full strategy JSON into a hash input. This helper extracts only common
    price or interval fields, bounds every text fragment, sorts the resulting
    item summaries, and caps the final list length.
    """

    summaries = [_candidate_summary(candidate) for candidate in candidates]
    filtered = sorted(summary for summary in summaries if summary)
    return _bound_summary_list(filtered)


def _candidate_summary(candidate: Any) -> str:
    if isinstance(candidate, Mapping):
        parts: list[str] = []
        for field_name in DIRECT_PRICE_FIELDS:
            if field_name in candidate:
                parts.append(f"{field_name}={_compact_value(candidate.get(field_name))}")
        for field_name in ZONE_FIELDS:
            if field_name in candidate:
                zone_summary = _zone_summary(candidate.get(field_name))
                if zone_summary:
                    parts.append(f"{field_name}={zone_summary}")
        if not parts:
            parts.append(f"value={_bounded_json(candidate)}")
        return _bounded_text("|".join(parts), max_chars=MAX_CANDIDATE_ITEM_CHARS)
    return _bounded_text(f"value={_compact_value(candidate)}", max_chars=MAX_CANDIDATE_ITEM_CHARS)


def _zone_summary(value: Any) -> str:
    if isinstance(value, Mapping):
        parts = [
            f"{field_name}:{_compact_value(value.get(field_name))}"
            for field_name in DIRECT_PRICE_FIELDS
            if field_name in value
        ]
        if parts:
            return _bounded_text(",".join(parts), max_chars=MAX_CANDIDATE_ITEM_CHARS)
        return _bounded_json(value)
    if isinstance(value, list):
        values = [_compact_value(item) for item in value[:MAX_CANDIDATE_SUMMARY_ITEMS]]
        return _bounded_text(",".join(values), max_chars=MAX_CANDIDATE_ITEM_CHARS)
    return _compact_value(value)


def _bound_summary_list(values: list[str]) -> list[str]:
    result: list[str] = []
    used_chars = 0
    for value in values:
        if len(result) >= MAX_CANDIDATE_SUMMARY_ITEMS:
            break
        bounded = _bounded_text(value, max_chars=MAX_CANDIDATE_ITEM_CHARS)
        next_chars = used_chars + len(bounded)
        if result and next_chars > MAX_CANDIDATE_SUMMARY_CHARS:
            break
        result.append(bounded)
        used_chars = next_chars
    return result


def _compact_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return _bounded_json(value)
    if isinstance(value, list):
        return _bounded_text(
            ",".join(_compact_value(item) for item in value[:MAX_CANDIDATE_SUMMARY_ITEMS]),
            max_chars=MAX_CANDIDATE_FIELD_CHARS,
        )
    if isinstance(value, (int, float, Decimal)):
        return _normalize_number(value)
    if isinstance(value, str):
        normalized = _normalize_numeric_text(value)
        if normalized is not None:
            return normalized
    return _bounded_text(value, max_chars=MAX_CANDIDATE_FIELD_CHARS)


def _normalize_number(value: Any) -> str:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return _bounded_text(value, max_chars=MAX_CANDIDATE_FIELD_CHARS)
    if not decimal_value.is_finite():
        return _bounded_text(value, max_chars=MAX_CANDIDATE_FIELD_CHARS)
    text = format(decimal_value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _normalize_numeric_text(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    try:
        Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return _normalize_number(text)


def _bounded_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return _bounded_text(raw, max_chars=MAX_CANDIDATE_FIELD_CHARS)


def _json_mapping(raw_value: Any) -> Mapping[str, Any]:
    if isinstance(raw_value, Mapping):
        return raw_value
    if not raw_value:
        return {}
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_value(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _first_text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _text_attr(value: Any | None, name: str) -> str:
    if value is None:
        return ""
    return str(getattr(value, name, "") or "")


def _bounded_text(value: Any, *, max_chars: int = 160) -> str:
    text = "" if value is None else " ".join(str(value).split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hash_mapping(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "MaterialFingerprint",
    "base_interval_to_ms",
    "build_material_fingerprint",
    "build_review_input_fingerprint",
    "calculate_reuse_base_bars",
]
