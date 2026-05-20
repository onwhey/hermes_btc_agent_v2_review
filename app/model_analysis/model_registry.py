"""Model-review registry and profile loader for stage 19.

This file belongs to `app/model_analysis`. It loads `model_registry.yaml`,
provider configs, and per-model profiles from `configs/model_review`.

Called by: `app/model_analysis/service.py`, CLI smoke checks, and tests.
External services: none. MySQL: none. Redis: none. Hermes: none. DeepSeek:
none in this file. Trading execution: none.

19B extends the 19A flat mock config by supporting:
`providers/<provider>.yaml` and `profiles/<provider>/<model_key>.yaml`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import ROOT_DIR
from app.model_analysis.model_profile import ModelProfile, ModelProviderConfig, ModelRegistrySelection
from app.model_analysis.types import MODEL_REVIEW_PROVIDER_DEEPSEEK, MODEL_REVIEW_PROVIDER_MOCK

MODEL_REVIEW_REGISTRY_FILE = "model_registry.yaml"
MODEL_REVIEW_PROVIDERS_DIR = "providers"
MODEL_REVIEW_PROFILES_DIR = "profiles"
SUPPORTED_ANALYSIS_MODES = frozenset({"single", "relay_chain", "parallel_comparison"})
STAGE19A_EXECUTABLE_ANALYSIS_MODE = "single"
MODEL_REVIEW_REQUIRED_FIELDS = frozenset(
    {
        "model_key",
        "provider",
        "enabled",
        "model_name",
        "model_version",
        "model_role",
        "analysis_mode",
        "prompt_template_version",
        "review_schema_version",
    }
)
MODEL_PROFILE_REQUIRED_FIELDS = MODEL_REVIEW_REQUIRED_FIELDS | frozenset(
    {
        "api_style",
        "profile_version",
        "docs_checked_at",
        "docs_source",
        "capabilities",
        "request_params",
        "response_mapping",
        "unsupported_params",
        "cost_policy",
    }
)
PROVIDER_REQUIRED_FIELDS = frozenset(
    {
        "provider",
        "enabled",
        "api_base_url",
        "api_key_env",
        "timeout_seconds",
        "max_retries",
        "retry_backoff_seconds",
        "provider_version",
        "docs_checked_at",
        "docs_source",
    }
)

# Compatibility alias for the 19A tests and service code.
ModelReviewConfig = ModelProfile


class ModelRegistryError(RuntimeError):
    """Raised when registry/profile files cannot produce a usable config."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def load_enabled_model_review_configs(config_dir: str | Path) -> list[ModelReviewConfig]:
    """Load enabled model profiles from registry order.

    This function preserves the 19A API. It returns enabled profile metadata,
    including the old flat `mock_review.yaml` format.
    """

    base_dir = _resolve_config_dir(config_dir)
    enabled_models = _load_enabled_model_keys(base_dir)
    configs: list[ModelProfile] = []
    for model_key in enabled_models:
        profile = _load_model_profile(base_dir=base_dir, model_key=model_key)
        if profile.enabled:
            configs.append(profile)
    if not configs:
        raise ModelRegistryError(
            "no_enabled_model_config",
            "model registry contains no enabled model config.",
        )
    return configs


def select_stage19a_mock_model_config(configs: list[ModelReviewConfig]) -> ModelReviewConfig | None:
    """Return the first enabled mock/single profile that safe dry-runs can use."""

    for config in configs:
        if config.is_stage19a_executable_mock:
            return config
    return None


def resolve_model_review_profile(config_dir: str | Path, *, model_key: str) -> ModelRegistrySelection:
    """Resolve one registry-enabled model profile by `model_key`.

    The returned selection may still be disabled by provider/profile flags; the
    service turns those gates into blocked results before any provider call.
    """

    base_dir = _resolve_config_dir(config_dir)
    enabled_models = _load_enabled_model_keys(base_dir)
    if model_key not in enabled_models:
        raise ModelRegistryError(
            "model_key_not_enabled_in_registry",
            f"model_key is not enabled in model_registry.yaml: {model_key}",
        )
    return _load_selection_for_model_key(base_dir=base_dir, model_key=model_key)


def _load_enabled_model_keys(base_dir: Path) -> list[str]:
    registry_path = base_dir / MODEL_REVIEW_REGISTRY_FILE
    if not registry_path.exists():
        raise ModelRegistryError(
            "model_registry_not_found",
            f"{MODEL_REVIEW_REGISTRY_FILE} does not exist in {base_dir}",
        )
    registry = _parse_yaml_mapping(registry_path)
    enabled_models = registry.get("enabled_models")
    if not isinstance(enabled_models, list):
        raise ModelRegistryError(
            "model_registry_invalid",
            "model_registry.yaml must define enabled_models as a list.",
        )
    if not enabled_models:
        raise ModelRegistryError(
            "model_registry_empty",
            "model_registry.yaml enabled_models is empty.",
        )
    result: list[str] = []
    for raw_model_key in enabled_models:
        model_key = str(raw_model_key).strip()
        if not model_key:
            raise ModelRegistryError("model_registry_invalid", "enabled_models contains an empty model key.")
        result.append(model_key)
    return result


def _load_selection_for_model_key(*, base_dir: Path, model_key: str) -> ModelRegistrySelection:
    profile = _load_model_profile(base_dir=base_dir, model_key=model_key)
    provider_config = None
    if profile.provider != MODEL_REVIEW_PROVIDER_MOCK:
        provider_config = _load_provider_config(base_dir=base_dir, provider=profile.provider)
    return ModelRegistrySelection(profile=profile, provider_config=provider_config, registry_enabled=True)


def _load_model_profile(*, base_dir: Path, model_key: str) -> ModelProfile:
    legacy_path = base_dir / f"{model_key}.yaml"
    if legacy_path.exists():
        raw_config = _parse_yaml_mapping(legacy_path)
        if "model_key" not in raw_config:
            raw_config["model_key"] = model_key
        return _build_legacy_or_mock_profile(raw_config, source_path=legacy_path)

    profile_path = _find_profile_path(base_dir=base_dir, model_key=model_key)
    if profile_path is None:
        raise ModelRegistryError(
            "model_profile_not_found",
            f"model profile file does not exist for model_key: {model_key}",
        )
    raw_profile = _parse_yaml_mapping(profile_path)
    if "model_key" not in raw_profile:
        raw_profile["model_key"] = model_key
    return _build_model_profile(raw_profile, source_path=profile_path)


def _find_profile_path(*, base_dir: Path, model_key: str) -> Path | None:
    profiles_dir = base_dir / MODEL_REVIEW_PROFILES_DIR
    if not profiles_dir.exists():
        return None
    matches = sorted(profiles_dir.glob(f"*/{model_key}.yaml"))
    return matches[0] if matches else None


def _load_provider_config(*, base_dir: Path, provider: str) -> ModelProviderConfig:
    config_path = base_dir / MODEL_REVIEW_PROVIDERS_DIR / f"{provider}.yaml"
    if not config_path.exists():
        raise ModelRegistryError(
            "provider_config_not_found",
            f"provider config file does not exist: {config_path}",
        )
    raw_config = _parse_yaml_mapping(config_path)
    missing = sorted(PROVIDER_REQUIRED_FIELDS - set(raw_config.keys()))
    if missing:
        raise ModelRegistryError(
            "provider_config_missing_field",
            f"{config_path.name} missing required fields: {', '.join(missing)}",
        )
    if not isinstance(raw_config["enabled"], bool):
        raise ModelRegistryError("provider_config_invalid", f"{config_path.name} enabled must be boolean.")
    provider_name = str(raw_config["provider"]).strip().lower()
    if provider_name != provider:
        raise ModelRegistryError(
            "provider_config_invalid",
            f"{config_path.name} provider mismatch: {provider_name} != {provider}",
        )
    return ModelProviderConfig(
        provider=provider_name,
        enabled=bool(raw_config["enabled"]),
        api_base_url=str(raw_config["api_base_url"]).rstrip("/"),
        api_key_env=str(raw_config["api_key_env"]).strip(),
        timeout_seconds=float(raw_config["timeout_seconds"]),
        max_retries=int(raw_config["max_retries"]),
        retry_backoff_seconds=float(raw_config["retry_backoff_seconds"]),
        provider_version=str(raw_config["provider_version"]).strip(),
        docs_checked_at=str(raw_config["docs_checked_at"]).strip(),
        docs_source=tuple(_text_list_field(raw_config["docs_source"])),
        source_path=str(config_path),
    )


def _build_legacy_or_mock_profile(raw_config: dict[str, Any], *, source_path: Path) -> ModelProfile:
    missing = sorted(MODEL_REVIEW_REQUIRED_FIELDS - set(raw_config.keys()))
    if missing:
        raise ModelRegistryError(
            "model_config_missing_field",
            f"{source_path.name} missing required fields: {', '.join(missing)}",
        )
    profile = ModelProfile(
        model_key=str(raw_config["model_key"]).strip(),
        provider=str(raw_config["provider"]).strip().lower(),
        enabled=_bool_field(raw_config["enabled"], field_name="enabled", source_path=source_path),
        api_style=str(raw_config.get("api_style") or "local_mock"),
        model_name=str(raw_config["model_name"]).strip(),
        model_version=str(raw_config["model_version"]).strip(),
        profile_version=str(raw_config.get("profile_version") or "profile_v1"),
        model_role=str(raw_config["model_role"]).strip(),
        analysis_mode=_analysis_mode(raw_config["analysis_mode"], source_path=source_path),
        prompt_template_version=str(raw_config["prompt_template_version"]).strip(),
        review_schema_version=str(raw_config["review_schema_version"]).strip(),
        capabilities=dict(raw_config.get("capabilities") or {"json_output": True}),
        request_params=dict(raw_config.get("request_params") or {}),
        response_mapping=dict(raw_config.get("response_mapping") or {}),
        unsupported_params=tuple(str(item) for item in raw_config.get("unsupported_params", [])),
        ignored_params_in_thinking_mode=tuple(
            str(item) for item in raw_config.get("ignored_params_in_thinking_mode", [])
        ),
        cost_policy=dict(raw_config.get("cost_policy") or {"track_token_usage": False}),
        docs_checked_at=str(raw_config.get("docs_checked_at") or ""),
        docs_source=tuple(_text_list_field(raw_config.get("docs_source", []))),
        source_path=str(source_path),
    )
    return profile.with_hash()


def _build_model_profile(raw_config: dict[str, Any], *, source_path: Path) -> ModelProfile:
    missing = sorted(MODEL_PROFILE_REQUIRED_FIELDS - set(raw_config.keys()))
    if missing:
        raise ModelRegistryError(
            "model_profile_missing_field",
            f"{source_path.name} missing required fields: {', '.join(missing)}",
        )
    profile = ModelProfile(
        model_key=str(raw_config["model_key"]).strip(),
        provider=str(raw_config["provider"]).strip().lower(),
        enabled=_bool_field(raw_config["enabled"], field_name="enabled", source_path=source_path),
        api_style=str(raw_config["api_style"]).strip(),
        model_name=str(raw_config["model_name"]).strip(),
        model_version=str(raw_config["model_version"]).strip(),
        profile_version=str(raw_config["profile_version"]).strip(),
        model_role=str(raw_config["model_role"]).strip(),
        analysis_mode=_analysis_mode(raw_config["analysis_mode"], source_path=source_path),
        prompt_template_version=str(raw_config["prompt_template_version"]).strip(),
        review_schema_version=str(raw_config["review_schema_version"]).strip(),
        capabilities=_mapping_field(raw_config["capabilities"], field_name="capabilities", source_path=source_path),
        request_params=_mapping_field(raw_config["request_params"], field_name="request_params", source_path=source_path),
        response_mapping=_mapping_field(
            raw_config["response_mapping"],
            field_name="response_mapping",
            source_path=source_path,
        ),
        unsupported_params=tuple(str(item) for item in _list_field(raw_config["unsupported_params"])),
        ignored_params_in_thinking_mode=tuple(
            str(item) for item in _list_field(raw_config.get("ignored_params_in_thinking_mode", []))
        ),
        cost_policy=_mapping_field(raw_config["cost_policy"], field_name="cost_policy", source_path=source_path),
        docs_checked_at=str(raw_config["docs_checked_at"]).strip(),
        docs_source=tuple(_text_list_field(raw_config["docs_source"])),
        source_path=str(source_path),
    )
    _validate_real_model_profile(profile, source_path=source_path)
    return profile.with_hash()


def _resolve_config_dir(config_dir: str | Path) -> Path:
    path = Path(config_dir)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _parse_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        entries = _preprocess_yaml_lines(path.read_text(encoding="utf-8").splitlines())
    except OSError as exc:
        raise ModelRegistryError("model_config_read_failed", f"cannot read model config: {path}") from exc
    if not entries:
        return {}
    data, next_index = _parse_mapping(entries, 0, entries[0][0], path=path)
    if next_index != len(entries):
        line_no = entries[next_index][2]
        raise ModelRegistryError("model_config_invalid", f"{path.name}:{line_no} unexpected YAML content.")
    return data


def _preprocess_yaml_lines(lines: list[str]) -> list[tuple[int, str, int]]:
    entries: list[tuple[int, str, int]] = []
    for line_no, raw_line in enumerate(lines, start=1):
        without_comment = raw_line.split("#", 1)[0].rstrip()
        if not without_comment.strip():
            continue
        indent = len(without_comment) - len(without_comment.lstrip(" "))
        entries.append((indent, without_comment.strip(), line_no))
    return entries


def _parse_mapping(
    entries: list[tuple[int, str, int]],
    index: int,
    indent: int,
    *,
    path: Path,
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(entries):
        current_indent, text, line_no = entries[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ModelRegistryError("model_config_invalid", f"{path.name}:{line_no} unexpected indentation.")
        if text.startswith("- "):
            break
        if ":" not in text:
            raise ModelRegistryError("model_config_invalid", f"{path.name}:{line_no} must use key: value.")
        key, raw_value = text.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise ModelRegistryError("model_config_invalid", f"{path.name}:{line_no} has an empty key.")
        index += 1
        if raw_value:
            result[key] = _parse_scalar(raw_value)
            continue
        if index >= len(entries) or entries[index][0] <= current_indent:
            result[key] = (
                []
                if key
                in {"enabled_models", "manual_only_models", "unsupported_params", "ignored_params_in_thinking_mode", "docs_source"}
                else {}
            )
            continue
        child_indent, child_text, _child_line = entries[index]
        if child_text.startswith("- "):
            value, index = _parse_list(entries, index, child_indent, path=path)
        else:
            value, index = _parse_mapping(entries, index, child_indent, path=path)
        result[key] = value
    return result, index


def _parse_list(
    entries: list[tuple[int, str, int]],
    index: int,
    indent: int,
    *,
    path: Path,
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(entries):
        current_indent, text, line_no = entries[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ModelRegistryError("model_config_invalid", f"{path.name}:{line_no} unexpected indentation.")
        if not text.startswith("- "):
            break
        item_text = text[2:].strip()
        if not item_text:
            raise ModelRegistryError("model_config_invalid", f"{path.name}:{line_no} empty list item.")
        result.append(_parse_scalar(item_text))
        index += 1
    return result, index


def _parse_scalar(raw_value: str) -> Any:
    if raw_value in {"true", "True"}:
        return True
    if raw_value in {"false", "False"}:
        return False
    if raw_value in {"null", "None", "~"}:
        return None
    if (raw_value.startswith('"') and raw_value.endswith('"')) or (
        raw_value.startswith("'") and raw_value.endswith("'")
    ):
        return raw_value[1:-1]
    try:
        if "." in raw_value:
            return float(raw_value)
        return int(raw_value)
    except ValueError:
        return raw_value


def _bool_field(value: Any, *, field_name: str, source_path: Path) -> bool:
    if not isinstance(value, bool):
        raise ModelRegistryError("model_config_invalid", f"{source_path.name} {field_name} must be boolean.")
    return value


def _analysis_mode(value: Any, *, source_path: Path) -> str:
    analysis_mode = str(value).strip()
    if analysis_mode not in SUPPORTED_ANALYSIS_MODES:
        raise ModelRegistryError(
            "model_config_invalid",
            f"{source_path.name} analysis_mode is invalid: {analysis_mode}",
        )
    return analysis_mode


def _mapping_field(value: Any, *, field_name: str, source_path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelRegistryError("model_profile_invalid", f"{source_path.name} {field_name} must be mapping.")
    return dict(value)


def _list_field(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text_list_field(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip() if value is not None else ""
    return [text] if text else []


def _validate_real_model_profile(profile: ModelProfile, *, source_path: Path) -> None:
    """Validate only provider-agnostic real-profile fields.

    Provider-specific request-shape rules, such as DeepSeek thinking mode
    parameters, are delegated by provider name so GPT/Claude-style profiles are
    not rejected by DeepSeek-only rules.
    """

    if profile.provider == MODEL_REVIEW_PROVIDER_MOCK:
        return
    if not profile.docs_checked_at:
        raise ModelRegistryError("model_profile_missing_docs_checked_at", f"{source_path.name} docs_checked_at is required.")
    if not profile.docs_source:
        raise ModelRegistryError("model_profile_missing_docs_source", f"{source_path.name} docs_source is required.")
    if not profile.profile_version:
        raise ModelRegistryError("model_profile_missing_profile_version", f"{source_path.name} profile_version is required.")
    if profile.provider == MODEL_REVIEW_PROVIDER_DEEPSEEK:
        from app.model_analysis.providers.deepseek import validate_deepseek_model_profile

        validation_error = validate_deepseek_model_profile(profile)
        if validation_error is not None:
            error_code, error_message = validation_error
            raise ModelRegistryError(error_code, f"{source_path.name} {error_message}")


__all__ = [
    "MODEL_REVIEW_REGISTRY_FILE",
    "ModelRegistryError",
    "ModelReviewConfig",
    "STAGE19A_EXECUTABLE_ANALYSIS_MODE",
    "load_enabled_model_review_configs",
    "resolve_model_review_profile",
    "select_stage19a_mock_model_config",
]
