"""Weak-model material summary helpers for stage-18 aggregation.

This file belongs to `app/strategy/aggregation`. It converts already persisted
27A weak-model aggregation rows and optional 27B quality-check rows into the
bounded `weak_model_summary` section stored in a stage-18 material pack.

Call chain:
scripts/run_strategy_aggregation.py::main
    -> app/strategy/aggregation/service.py::StrategyAggregationService.run_strategy_aggregation
    -> app/strategy/aggregation/repository.py::get_latest_weak_model_material
    -> app/strategy/aggregation/weak_model_material.py::build_weak_model_summary
    -> app/strategy/aggregation/material_builder.py::build_material_pack

This module does not run 27A weak models, does not run 27B quality checks, does
not request Binance, does not read/write Redis, does not send Hermes, does not
call any large model, does not read private trading state, and does not perform
trading. Database reads are performed by the repository; this file is pure
JSON shaping and validation for material-pack content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping


WEAK_MODEL_STATUS_AVAILABLE = "available"
WEAK_MODEL_STATUS_MISSING = "missing"
WEAK_MODEL_STATUS_UNCHECKED = "unchecked"
WEAK_MODEL_STATUS_WARNING = "warning"
WEAK_MODEL_STATUS_EXCLUDED = "excluded_by_quality_check"

WEAK_MODEL_QUALITY_PASSED = "passed"
WEAK_MODEL_QUALITY_WARNING = "warning"
WEAK_MODEL_QUALITY_CRITICAL = "critical"
WEAK_MODEL_QUALITY_UNCHECKED = "unchecked"
WEAK_MODEL_QUALITY_MISSING = "missing"


@dataclass(frozen=True)
class WeakModelMaterialSource:
    """Read model package selected by the stage-18 repository.

    `run` is the latest matching successful WMR, `aggregation` is its WMA,
    `quality_check` is the latest WMQC for the same WMR if present, and
    `source_config_hashes` is a bounded list of model-key/config-hash markers.
    The package intentionally excludes `weak_model_result.raw_output_json`.
    """

    run: Any
    aggregation: Any
    quality_check: Any | None = None
    source_config_hashes: tuple[str, ...] = ()


def extract_snapshot_base_slot_utc(restored_snapshot: Any) -> datetime | None:
    """Return the snapshot-bound latest base Kline slot in UTC.

    Parameters: the restored stage-15 snapshot object used by stage 18.
    Return value: a timezone-aware UTC datetime or `None` if no slot can be
    recovered. Failure scenarios: malformed millisecond fields are ignored.
    External effects: none.
    """

    snapshot = getattr(restored_snapshot, "snapshot", None)
    if snapshot is None:
        return None
    for field_name in ("latest_4h_open_time_utc", "end_4h_open_time_utc", "kline_slot_utc"):
        value = getattr(snapshot, field_name, None)
        normalized = _datetime_or_none(value)
        if normalized is not None:
            return normalized
    for field_name in ("end_4h_open_time_ms", "latest_4h_open_time_ms"):
        value = getattr(snapshot, field_name, None)
        try:
            if value is not None:
                return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
    rows_4h = tuple(getattr(restored_snapshot, "rows_4h", ()) or ())
    if rows_4h:
        return _datetime_or_none(getattr(rows_4h[-1], "open_time_utc", None))
    return None


def build_weak_model_summary(
    source: WeakModelMaterialSource | None,
    *,
    strategy_signal_run_id: str,
    snapshot_id: str | None,
    symbol: str,
    base_interval: str,
    higher_interval: str,
    kline_slot_utc: datetime | None,
) -> Mapping[str, Any]:
    """Build the stage-18 `weak_model_summary` section.

    Parameters identify the current SSR/snapshot/slot and the optional selected
    WMR/WMA/WMQC source. Return value is small JSON-ready material. Failure
    scenarios are represented as `status=missing/unchecked/warning/excluded`.
    External effects: none.
    """

    if source is None:
        return {
            "status": WEAK_MODEL_STATUS_MISSING,
            "weak_model_run_id": "",
            "weak_model_aggregation_id": "",
            "quality_check_id": "",
            "quality_status": WEAK_MODEL_QUALITY_MISSING,
            "strategy_signal_run_id": strategy_signal_run_id,
            "snapshot_id": snapshot_id or "",
            "symbol": symbol,
            "base_interval": base_interval,
            "higher_interval": higher_interval,
            "kline_slot_utc": _datetime_text(kline_slot_utc),
            "directional_bias": "",
            "directional_score": None,
            "directional_confidence": None,
            "risk_level": "",
            "trade_permission": "",
            "veto_triggered": False,
            "supporting_factors": [],
            "opposing_factors": [],
            "conflict_factors": [],
            "low_confidence_factors": [],
            "veto_factors": [],
            "context_summary": {},
            "quality_issues": [],
            "source_config_hashes": [],
            "summary_text": "未找到对应弱模型摘要，本轮材料包不包含弱模型证据。",
            "not_trading_advice": True,
        }

    aggregation = source.aggregation
    quality_check = source.quality_check
    quality_status = str(getattr(quality_check, "status", "") or "").strip()
    if not quality_status:
        status = WEAK_MODEL_STATUS_UNCHECKED
        quality_status = WEAK_MODEL_QUALITY_UNCHECKED
    elif quality_status == WEAK_MODEL_QUALITY_PASSED:
        status = WEAK_MODEL_STATUS_AVAILABLE
    elif quality_status == WEAK_MODEL_QUALITY_WARNING:
        status = WEAK_MODEL_STATUS_WARNING
    elif quality_status == WEAK_MODEL_QUALITY_CRITICAL:
        status = WEAK_MODEL_STATUS_EXCLUDED
    else:
        status = WEAK_MODEL_STATUS_UNCHECKED
        quality_status = WEAK_MODEL_QUALITY_UNCHECKED

    effective_values = {
        "directional_bias": str(getattr(aggregation, "directional_bias", "") or ""),
        "directional_score": _float_or_none(getattr(aggregation, "directional_score", None)),
        "directional_confidence": _float_or_none(getattr(aggregation, "directional_confidence", None)),
        "risk_level": str(getattr(aggregation, "risk_level", "") or ""),
        "trade_permission": str(getattr(aggregation, "trade_permission", "") or ""),
    }
    if status == WEAK_MODEL_STATUS_EXCLUDED:
        directional_bias = "excluded_by_quality_check"
        directional_score = None
        directional_confidence = None
        risk_level = "excluded_by_quality_check"
        trade_permission = "excluded_by_quality_check"
        excluded_values: Mapping[str, Any] = effective_values
    else:
        directional_bias = str(effective_values["directional_bias"])
        directional_score = effective_values["directional_score"]
        directional_confidence = effective_values["directional_confidence"]
        risk_level = str(effective_values["risk_level"])
        trade_permission = str(effective_values["trade_permission"])
        excluded_values = {}

    return {
        "status": status,
        "weak_model_run_id": str(getattr(source.run, "weak_model_run_id", "") or ""),
        "weak_model_aggregation_id": str(getattr(aggregation, "weak_model_aggregation_id", "") or ""),
        "quality_check_id": str(getattr(quality_check, "quality_check_id", "") or "") if quality_check else "",
        "quality_status": quality_status,
        "strategy_signal_run_id": str(getattr(aggregation, "strategy_signal_run_id", "") or strategy_signal_run_id),
        "snapshot_id": str(getattr(aggregation, "snapshot_id", "") or snapshot_id or ""),
        "symbol": str(getattr(aggregation, "symbol", "") or symbol),
        "base_interval": str(getattr(aggregation, "base_interval", "") or base_interval),
        "higher_interval": str(getattr(aggregation, "higher_interval", "") or higher_interval),
        "kline_slot_utc": _datetime_text(getattr(aggregation, "kline_slot_utc", None) or kline_slot_utc),
        "directional_bias": directional_bias,
        "directional_score": directional_score,
        "directional_confidence": directional_confidence,
        "risk_level": risk_level,
        "trade_permission": trade_permission,
        "veto_triggered": bool(getattr(aggregation, "veto_triggered", False)),
        "supporting_factors": _json_list(getattr(aggregation, "supporting_factors_json", None)),
        "opposing_factors": _json_list(getattr(aggregation, "opposing_factors_json", None)),
        "conflict_factors": _json_list(getattr(aggregation, "conflict_factors_json", None)),
        "low_confidence_factors": _json_list(getattr(aggregation, "low_confidence_factors_json", None)),
        "veto_factors": _json_list(getattr(aggregation, "veto_factors_json", None)),
        "context_summary": _json_mapping(getattr(aggregation, "context_summary_json", None)),
        "quality_issues": _json_list(getattr(quality_check, "issues_json", None)) if quality_check else [],
        "source_config_hashes": list(source.source_config_hashes),
        "summary_text": str(getattr(aggregation, "summary_text", "") or ""),
        "excluded_values": dict(excluded_values),
        "not_trading_advice": True,
    }


def build_legacy_math_context_summary(weak_model_summary: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a compact legacy-math marker for stage-18 material.

    The old deterministic swing/volatility/support-resistance sections remain
    in the pack for compatibility, but this marker tells downstream reviewers
    those sections may overlap with 27A weak-model factors and must not be
    counted as independent evidence.
    """

    weak_status = str(weak_model_summary.get("status") or WEAK_MODEL_STATUS_MISSING)
    weak_model_present = weak_status not in {WEAK_MODEL_STATUS_MISSING, WEAK_MODEL_STATUS_EXCLUDED}
    return {
        "source": "legacy_math_context",
        "status": "deprecated_math_material",
        "weak_model_summary_status": weak_status,
        "covered_by_weak_model_roles": (
            ["directional", "risk", "confirmation", "context"] if weak_model_present else []
        ),
        "independent_evidence_weight": "background_only" if weak_model_present else "legacy_only",
        "double_counting_warning": (
            "legacy_math_context 与 weak_model_summary 可能同源，模型审查不得把它们当作两组独立证据重复计票。"
        ),
        "not_trading_advice": True,
    }


def weak_model_summary_fingerprint_fields(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return bounded weak-model fields for material hash/fingerprint inputs."""

    return {
        "status": summary.get("status"),
        "weak_model_run_id": summary.get("weak_model_run_id"),
        "weak_model_aggregation_id": summary.get("weak_model_aggregation_id"),
        "quality_check_id": summary.get("quality_check_id"),
        "quality_status": summary.get("quality_status"),
        "directional_bias": summary.get("directional_bias"),
        "directional_score": summary.get("directional_score"),
        "directional_confidence": summary.get("directional_confidence"),
        "risk_level": summary.get("risk_level"),
        "trade_permission": summary.get("trade_permission"),
        "veto_triggered": summary.get("veto_triggered"),
        "veto_factors": _bounded_list(summary.get("veto_factors"), max_items=6),
        "context_summary": _bounded_mapping(summary.get("context_summary"), max_items=8),
        "quality_issues": _bounded_quality_issue_codes(summary.get("quality_issues")),
        "source_config_hashes": _bounded_list(summary.get("source_config_hashes"), max_items=8),
    }


def _json_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _bounded_list(value: Any, *, max_items: int) -> list[Any]:
    items = _json_list(value)
    return items[:max_items]


def _bounded_mapping(value: Any, *, max_items: int) -> Mapping[str, Any]:
    mapping = _json_mapping(value)
    return {str(key): item for key, item in list(mapping.items())[:max_items]}


def _bounded_quality_issue_codes(value: Any) -> list[str]:
    issues = _json_list(value)
    codes: list[str] = []
    for item in issues[:8]:
        if isinstance(item, Mapping):
            code = str(item.get("error_code", "") or "")
            if code:
                codes.append(code)
        elif item:
            codes.append(str(item)[:80])
    return codes


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _datetime_text(value: Any) -> str:
    dt_value = _datetime_or_none(value)
    if dt_value is None:
        return ""
    return dt_value.isoformat().replace("+00:00", "Z")


__all__ = [
    "WEAK_MODEL_QUALITY_CRITICAL",
    "WEAK_MODEL_QUALITY_MISSING",
    "WEAK_MODEL_QUALITY_PASSED",
    "WEAK_MODEL_QUALITY_UNCHECKED",
    "WEAK_MODEL_QUALITY_WARNING",
    "WEAK_MODEL_STATUS_AVAILABLE",
    "WEAK_MODEL_STATUS_EXCLUDED",
    "WEAK_MODEL_STATUS_MISSING",
    "WEAK_MODEL_STATUS_UNCHECKED",
    "WEAK_MODEL_STATUS_WARNING",
    "WeakModelMaterialSource",
    "build_legacy_math_context_summary",
    "build_weak_model_summary",
    "extract_snapshot_base_slot_utc",
    "weak_model_summary_fingerprint_fields",
]
