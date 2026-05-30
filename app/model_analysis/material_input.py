"""Material-pack input extraction helpers for stage-24C model review.

This file belongs to `app/model_analysis`. It extracts the public
`strategy_evidence` bridge and UTC/PRC time anchors from stage-18 material
packs before any model call.

Called by `app/model_analysis/prompt_builder.py` and
`app/model_analysis/material_pack_reviewability.py`.
External services: none. MySQL: none in this file. Redis: none. Hermes: none.
DeepSeek/large models: none. Trading execution: none. This file never reads
`strategy_payload_json` or any strategy-private payload.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.core.time_utils import ensure_utc_aware, utc_aware_to_prc_aware

UTC_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
REQUIRED_STRATEGY_EVIDENCE_FIELDS = (
    "source",
    "aggregation_id",
    "strategy_signal_run_id",
    "candidate_bias",
    "decision_readiness",
    "strategy_evidence_summary",
    "decision_source_chain",
    "role_coverage_matrix",
    "evidence_missing",
    "strategy_conflict_summary",
    "risk_gate_summary",
    "model_review_focus",
)
REQUIRED_TIME_ANCHOR_FIELDS = (
    "analysis_time_utc",
    "analysis_time_prc",
    "latest_base_kline_open_time_utc",
    "latest_base_kline_close_time_utc",
    "latest_higher_kline_open_time_utc",
    "latest_higher_kline_close_time_utc",
    "data_freshness_status",
)


def material_json_mapping(material_pack: Any) -> Mapping[str, Any]:
    """Return `material_json` as a mapping without touching private payloads."""

    return _json_field(material_pack, "material_json")


def extract_strategy_evidence(material_pack: Any) -> Mapping[str, Any]:
    """Extract the public 23F strategy-evidence bridge from material JSON."""

    material_json = material_json_mapping(material_pack)
    strategy_evidence = material_json.get("strategy_evidence")
    return strategy_evidence if isinstance(strategy_evidence, Mapping) else {}


def strategy_evidence_missing_fields(strategy_evidence: Mapping[str, Any]) -> tuple[str, ...]:
    """Return required public evidence fields missing from one material pack."""

    missing: list[str] = []
    for field_name in REQUIRED_STRATEGY_EVIDENCE_FIELDS:
        value = strategy_evidence.get(field_name)
        if value is None or value == "":
            missing.append(field_name)
    return tuple(missing)


def build_time_anchor_summary(material_pack: Any) -> Mapping[str, Any]:
    """Build model-review time anchors from material-pack metadata and Klines.

    Parameters: one stage-18 material pack row or test object.
    Return value: JSON-ready mapping containing UTC/PRC analysis time, latest
    base/higher Kline open/close times, and data freshness status.
    Failure scenarios: invalid or missing fields are represented inside the
    returned mapping; callers decide whether to block.
    External effects: none.
    """

    material_json = material_json_mapping(material_pack)
    kline_window = _mapping(material_json.get("kline_window_summary"))
    latest_base = _mapping(kline_window.get("latest_base_kline"))
    latest_higher = _mapping(kline_window.get("latest_higher_kline"))
    base_interval = str(getattr(material_pack, "base_interval", "") or material_json.get("base_interval") or "")
    higher_interval = str(
        getattr(material_pack, "higher_interval", "") or material_json.get("higher_interval") or ""
    )

    missing: list[str] = []
    analysis_time = _analysis_time_utc(material_pack)
    if analysis_time is None:
        missing.append("analysis_time_utc")

    base_open = _open_time_from_kline(latest_base)
    higher_open = _open_time_from_kline(latest_higher)
    base_interval_delta = _interval_delta(base_interval)
    higher_interval_delta = _interval_delta(higher_interval)
    if base_open is None:
        missing.append("latest_base_kline_open_time_utc")
    if higher_open is None:
        missing.append("latest_higher_kline_open_time_utc")
    if base_interval_delta is None:
        missing.append("base_interval")
    if higher_interval_delta is None:
        missing.append("higher_interval")

    base_close = base_open + base_interval_delta if base_open and base_interval_delta else None
    higher_close = higher_open + higher_interval_delta if higher_open and higher_interval_delta else None
    if base_close is None:
        missing.append("latest_base_kline_close_time_utc")
    if higher_close is None:
        missing.append("latest_higher_kline_close_time_utc")

    future_guard = _json_field(material_pack, "future_leakage_guard_json")
    uses_future = bool(future_guard.get("uses_future_klines") is True)
    data_freshness_status = "fresh"
    if missing:
        data_freshness_status = "time_anchor_missing"
    elif uses_future:
        data_freshness_status = "future_leakage_detected"
    elif analysis_time and (base_close and base_close > analysis_time or higher_close and higher_close > analysis_time):
        data_freshness_status = "future_kline_after_analysis_time"
    elif analysis_time and (
        _is_stale(analysis_time=analysis_time, close_time=base_close, interval_delta=base_interval_delta)
        or _is_stale(analysis_time=analysis_time, close_time=higher_close, interval_delta=higher_interval_delta)
    ):
        data_freshness_status = "stale_data"

    return {
        "analysis_time_utc": _iso_utc(analysis_time),
        "analysis_time_prc": _iso_prc(analysis_time),
        "latest_base_kline_open_time_utc": _iso_utc(base_open),
        "latest_base_kline_close_time_utc": _iso_utc(base_close),
        "latest_higher_kline_open_time_utc": _iso_utc(higher_open),
        "latest_higher_kline_close_time_utc": _iso_utc(higher_close),
        "data_freshness_status": data_freshness_status,
        "missing_time_anchor_fields": tuple(sorted(set(missing))),
    }


def time_anchor_missing_fields(time_anchors: Mapping[str, Any]) -> tuple[str, ...]:
    """Return required time-anchor fields missing from an extracted summary."""

    explicit_missing = time_anchors.get("missing_time_anchor_fields")
    if isinstance(explicit_missing, (list, tuple)):
        return tuple(str(item) for item in explicit_missing)
    return tuple(
        field_name
        for field_name in REQUIRED_TIME_ANCHOR_FIELDS
        if time_anchors.get(field_name) in (None, "")
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


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _analysis_time_utc(material_pack: Any) -> datetime | None:
    for field_name in ("created_at_utc", "updated_at_utc"):
        value = getattr(material_pack, field_name, None)
        if isinstance(value, datetime):
            return ensure_utc_aware(value)
    return None


def _open_time_from_kline(kline: Mapping[str, Any]) -> datetime | None:
    open_time_ms = kline.get("open_time_ms")
    if open_time_ms not in (None, ""):
        try:
            return datetime.fromtimestamp(int(open_time_ms) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
    open_time_utc = kline.get("open_time_utc")
    if isinstance(open_time_utc, str) and open_time_utc.strip():
        text = open_time_utc.strip()
        try:
            if text.endswith("Z"):
                return datetime.fromisoformat(text[:-1] + "+00:00").astimezone(timezone.utc)
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return ensure_utc_aware(parsed)
    return None


def _interval_delta(interval_value: str) -> timedelta | None:
    text = interval_value.strip().lower()
    if len(text) < 2:
        return None
    unit = text[-1]
    try:
        amount = int(text[:-1])
    except ValueError:
        return None
    if amount <= 0:
        return None
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    return None


def _is_stale(
    *,
    analysis_time: datetime,
    close_time: datetime | None,
    interval_delta: timedelta | None,
) -> bool:
    if close_time is None or interval_delta is None:
        return True
    allowed_lag = max(interval_delta * 3, timedelta(hours=12))
    return analysis_time - close_time > allowed_lag


def _iso_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    aware = ensure_utc_aware(value)
    if aware is None:
        return ""
    return aware.strftime(UTC_ISO_FORMAT)


def _iso_prc(value: datetime | None) -> str:
    if value is None:
        return ""
    aware = ensure_utc_aware(value)
    if aware is None:
        return ""
    return utc_aware_to_prc_aware(aware).isoformat()


__all__ = [
    "REQUIRED_STRATEGY_EVIDENCE_FIELDS",
    "REQUIRED_TIME_ANCHOR_FIELDS",
    "build_time_anchor_summary",
    "extract_strategy_evidence",
    "material_json_mapping",
    "strategy_evidence_missing_fields",
    "time_anchor_missing_fields",
]
