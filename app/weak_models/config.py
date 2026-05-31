"""Config loading for 27A weak model profiles.

本文件属于 `app/weak_models` 模块，负责读取 `configs/weak_models/` 下的本地
非敏感 YAML 子集配置并生成 `WeakModelProfile`。
本文件不运行弱模型，不读取数据库，不请求 Binance，不发送 Hermes，不读写 Redis，
不调用 DeepSeek/GPT/Claude，不读取账户或仓位，不生成订单，不自动交易。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from app.weak_models.types import WeakModelMaturityStage, WeakModelProfile, WeakModelRole

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEAK_MODEL_CONFIG_DIR = PROJECT_ROOT / "configs" / "weak_models"


class WeakModelConfigError(ValueError):
    """Raised when weak model configuration is missing or invalid."""


def load_weak_model_profiles(config_dir: Path | None = None) -> tuple[WeakModelProfile, ...]:
    """Load weak model profiles in registry-config order."""

    active_dir = config_dir or DEFAULT_WEAK_MODEL_CONFIG_DIR
    registry = _read_simple_yaml(active_dir / "registry.yaml")
    enabled_models = registry.get("models")
    if not isinstance(enabled_models, list) or not enabled_models:
        raise WeakModelConfigError("weak model registry.yaml must define models")
    profiles: list[WeakModelProfile] = []
    seen: set[str] = set()
    for raw_key in enabled_models:
        model_key = str(raw_key).strip()
        if not model_key:
            raise WeakModelConfigError("weak model key must not be empty")
        if model_key in seen:
            raise WeakModelConfigError(f"duplicate weak model key configured: {model_key}")
        seen.add(model_key)
        profile = load_weak_model_profile(active_dir / f"{model_key}.yaml")
        if profile.model_key != model_key:
            raise WeakModelConfigError(f"weak model key mismatch: registry={model_key}, profile={profile.model_key}")
        profiles.append(profile)
    return tuple(profiles)


def load_weak_model_profile(path: Path) -> WeakModelProfile:
    """Load and validate one weak model profile file."""

    raw = _read_simple_yaml(path)
    config_hash = _config_hash(raw)
    profile = WeakModelProfile(
        model_key=_required_text(raw, "model_key", path),
        model_name=_required_text(raw, "model_name", path),
        enabled=bool(raw.get("enabled", False)),
        maturity_stage=_required_text(raw, "maturity_stage", path),
        model_role=_required_text(raw, "model_role", path),
        model_version=_required_text(raw, "model_version", path),
        config_version=_required_text(raw, "config_version", path),
        config_hash=config_hash,
        input_intervals=tuple(str(item) for item in _required_list(raw, "input_intervals", path)),
        input_window=_mapping(raw.get("input_window")),
        static_weight=float(raw.get("static_weight", 0.0)),
        description=str(raw.get("description", "")),
        params=_mapping(raw.get("params")),
    )
    _validate_profile(profile, path)
    return profile


def _validate_profile(profile: WeakModelProfile, path: Path) -> None:
    if profile.model_role not in {item.value for item in WeakModelRole}:
        raise WeakModelConfigError(f"invalid model_role in {path}: {profile.model_role}")
    if profile.maturity_stage not in {item.value for item in WeakModelMaturityStage}:
        raise WeakModelConfigError(f"invalid maturity_stage in {path}: {profile.maturity_stage}")
    if profile.static_weight < 0 or profile.static_weight > 1:
        raise WeakModelConfigError(f"static_weight out of range in {path}: {profile.static_weight}")
    if profile.static_weight > 0.30:
        raise WeakModelConfigError(f"static_weight must not exceed 0.30 in {path}")
    if profile.maturity_stage == WeakModelMaturityStage.OBSERVE_ONLY.value and profile.static_weight != 0:
        raise WeakModelConfigError(f"observe_only weak model must use static_weight=0 in {path}")
    if not profile.input_intervals:
        raise WeakModelConfigError(f"input_intervals must not be empty in {path}")


def _read_simple_yaml(path: Path) -> dict[str, Any]:
    """Read the small YAML subset used by weak-model configs."""

    if not path.exists():
        raise WeakModelConfigError(f"weak model config file not found: {path}")
    result: dict[str, Any] = {}
    active_key: str | None = None
    active_list_item: dict[str, Any] | None = None
    active_list_indent: int | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            active_key = _parse_top_level_line(path, raw_line, stripped, result)
            active_list_item = None
            active_list_indent = None
            continue
        if active_key is None:
            raise WeakModelConfigError(f"nested config line without parent key in {path}: {raw_line}")
        active_list_item, active_list_indent = _parse_nested_line(
            path,
            raw_line,
            stripped,
            result,
            active_key,
            active_list_item=active_list_item,
            active_list_indent=active_list_indent,
            indent=indent,
        )
    return result


def _parse_top_level_line(path: Path, raw_line: str, stripped: str, result: dict[str, Any]) -> str:
    if stripped.startswith("- ") or ":" not in stripped:
        raise WeakModelConfigError(f"invalid config line in {path}: {raw_line}")
    key, value = stripped.split(":", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        raise WeakModelConfigError(f"empty config key in {path}")
    result[key] = [] if value == "" else _parse_scalar(value)
    return key


def _parse_nested_line(
    path: Path,
    raw_line: str,
    stripped: str,
    result: dict[str, Any],
    active_key: str,
    *,
    active_list_item: dict[str, Any] | None,
    active_list_indent: int | None,
    indent: int,
) -> tuple[dict[str, Any] | None, int | None]:
    parent = result.setdefault(active_key, [])
    if stripped.startswith("- "):
        if not isinstance(parent, list):
            raise WeakModelConfigError(f"list item under mapping in {path}: {raw_line}")
        item_text = stripped[2:].strip()
        if ":" in item_text and not item_text.startswith(("'", '"')):
            key, value = item_text.split(":", 1)
            item = {key.strip(): _parse_scalar(value.strip()) if value.strip() else {}}
            parent.append(item)
            return item, indent
        parent.append(_parse_scalar(item_text))
        return None, None
    if ":" not in stripped:
        raise WeakModelConfigError(f"invalid nested config line in {path}: {raw_line}")
    if isinstance(parent, list) and active_list_item is not None and active_list_indent is not None and indent > active_list_indent:
        key, value = stripped.split(":", 1)
        active_list_item[key.strip()] = _parse_scalar(value.strip()) if value.strip() else {}
        return active_list_item, active_list_indent
    if isinstance(parent, list):
        if parent:
            raise WeakModelConfigError(f"cannot mix list and mapping values in {path}: {active_key}")
        result[active_key] = {}
        parent = result[active_key]
    if not isinstance(parent, dict):
        raise WeakModelConfigError(f"nested mapping under scalar key in {path}: {active_key}")
    key, value = stripped.split(":", 1)
    parent[key.strip()] = _parse_scalar(value.strip()) if value.strip() else {}
    return None, None


def _parse_scalar(value: str) -> Any:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in normalized:
            return float(normalized)
        return int(normalized)
    except ValueError:
        return normalized.strip("'\"")


def _config_hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _required_text(raw: Mapping[str, Any], key: str, path: Path) -> str:
    value = str(raw.get(key, "")).strip()
    if not value:
        raise WeakModelConfigError(f"{key} is required in {path}")
    return value


def _required_list(raw: Mapping[str, Any], key: str, path: Path) -> tuple[Any, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise WeakModelConfigError(f"{key} must be a non-empty list in {path}")
    return tuple(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "DEFAULT_WEAK_MODEL_CONFIG_DIR",
    "WeakModelConfigError",
    "load_weak_model_profile",
    "load_weak_model_profiles",
]
