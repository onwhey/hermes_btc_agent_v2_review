"""Model-review configuration registry for stage 19A.

This file belongs to `app/model_analysis`. It reads compact YAML-like model
configuration files from `configs/model_review` and returns enabled model
metadata for the review gate service.

Called by: `app/model_analysis/service.py` and tests.
External services: none. MySQL: none. Redis: none. Hermes: none. Real model
calls: none. Trading execution: none.

Stage 19A executes only `provider=mock` with `analysis_mode=single`. Other
providers or modes may be described by config files for future wiring, but this
module does not create clients or perform network calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import ROOT_DIR
from app.model_analysis.types import MODEL_REVIEW_PROVIDER_MOCK

MODEL_REVIEW_REGISTRY_FILE = "model_registry.yaml"
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


@dataclass(frozen=True)
class ModelReviewConfig:
    """One enabled model-review config entry.

    Parameters come from `configs/model_review/*.yaml`.
    Return value: immutable metadata consumed by the service.
    Failure scenarios: invalid or missing fields are raised by the loader.
    External effects: none.
    """

    model_key: str
    provider: str
    enabled: bool
    model_name: str
    model_version: str
    model_role: str
    analysis_mode: str
    prompt_template_version: str
    review_schema_version: str

    @property
    def is_stage19a_executable_mock(self) -> bool:
        """Return whether this config may be executed in stage 19A."""

        return (
            self.enabled
            and self.provider == MODEL_REVIEW_PROVIDER_MOCK
            and self.analysis_mode == STAGE19A_EXECUTABLE_ANALYSIS_MODE
        )


class ModelRegistryError(RuntimeError):
    """Raised when model-review registry files cannot produce a runnable config."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def load_enabled_model_review_configs(config_dir: str | Path) -> list[ModelReviewConfig]:
    """Load enabled model-review configs from the registry directory.

    Parameters: `config_dir` may be absolute or relative to project root.
    Return value: enabled model metadata in registry order.
    Failure scenarios: missing registry, empty `enabled_models`, missing config
    file, invalid fields, or no enabled configs raise `ModelRegistryError`.
    External services/database/Redis/Hermes/model calls: none.
    """

    base_dir = _resolve_config_dir(config_dir)
    registry_path = base_dir / MODEL_REVIEW_REGISTRY_FILE
    if not registry_path.exists():
        raise ModelRegistryError(
            "model_registry_not_found",
            f"{MODEL_REVIEW_REGISTRY_FILE} does not exist in {base_dir}",
        )

    registry = _parse_simple_yaml_mapping(registry_path)
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

    configs: list[ModelReviewConfig] = []
    for raw_model_key in enabled_models:
        model_key = str(raw_model_key).strip()
        if not model_key:
            raise ModelRegistryError("model_registry_invalid", "enabled_models contains an empty model key.")
        model_config = _load_model_config(base_dir=base_dir, model_key=model_key)
        if model_config.enabled:
            configs.append(model_config)

    if not configs:
        raise ModelRegistryError(
            "no_enabled_model_config",
            "model registry contains no enabled model config.",
        )
    return configs


def select_stage19a_mock_model_config(configs: list[ModelReviewConfig]) -> ModelReviewConfig | None:
    """Return the first enabled mock/single config that stage 19A can execute."""

    for config in configs:
        if config.is_stage19a_executable_mock:
            return config
    return None


def _load_model_config(*, base_dir: Path, model_key: str) -> ModelReviewConfig:
    config_path = base_dir / f"{model_key}.yaml"
    if not config_path.exists():
        raise ModelRegistryError(
            "model_config_not_found",
            f"model config file does not exist: {config_path}",
        )
    raw_config = _parse_simple_yaml_mapping(config_path)
    if "model_key" not in raw_config:
        raw_config["model_key"] = model_key
    return _build_model_config(raw_config, source_path=config_path)


def _build_model_config(raw_config: dict[str, Any], *, source_path: Path) -> ModelReviewConfig:
    missing = sorted(MODEL_REVIEW_REQUIRED_FIELDS - set(raw_config.keys()))
    if missing:
        raise ModelRegistryError(
            "model_config_missing_field",
            f"{source_path.name} missing required fields: {', '.join(missing)}",
        )
    enabled = raw_config["enabled"]
    if not isinstance(enabled, bool):
        raise ModelRegistryError("model_config_invalid", f"{source_path.name} enabled must be boolean.")
    analysis_mode = str(raw_config["analysis_mode"]).strip()
    if analysis_mode not in SUPPORTED_ANALYSIS_MODES:
        raise ModelRegistryError(
            "model_config_invalid",
            f"{source_path.name} analysis_mode is invalid: {analysis_mode}",
        )
    provider = str(raw_config["provider"]).strip().lower()
    return ModelReviewConfig(
        model_key=str(raw_config["model_key"]).strip(),
        provider=provider,
        enabled=enabled,
        model_name=str(raw_config["model_name"]).strip(),
        model_version=str(raw_config["model_version"]).strip(),
        model_role=str(raw_config["model_role"]).strip(),
        analysis_mode=analysis_mode,
        prompt_template_version=str(raw_config["prompt_template_version"]).strip(),
        review_schema_version=str(raw_config["review_schema_version"]).strip(),
    )


def _resolve_config_dir(config_dir: str | Path) -> Path:
    path = Path(config_dir)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _parse_simple_yaml_mapping(path: Path) -> dict[str, Any]:
    """Parse the small YAML subset used by model-review config files.

    The parser intentionally supports only top-level scalar values and one
    level of lists. This keeps stage 19A free of an additional dependency while
    making unsupported config shapes fail clearly instead of being interpreted
    loosely.
    """

    result: dict[str, Any] = {}
    active_list_key: str | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ModelRegistryError("model_config_read_failed", f"cannot read model config: {path}") from exc

    for line_no, raw_line in enumerate(lines, start=1):
        stripped = raw_line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        if stripped.lstrip().startswith("- "):
            if active_list_key is None:
                raise ModelRegistryError(
                    "model_config_invalid",
                    f"{path.name}:{line_no} list item without a parent key.",
                )
            result.setdefault(active_list_key, []).append(_parse_scalar(stripped.lstrip()[2:].strip()))
            continue
        if raw_line[: len(raw_line) - len(raw_line.lstrip())].strip():
            raise ModelRegistryError(
                "model_config_invalid",
                f"{path.name}:{line_no} nested mappings are not supported in stage 19A config.",
            )
        if ":" not in stripped:
            raise ModelRegistryError("model_config_invalid", f"{path.name}:{line_no} must use key: value.")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise ModelRegistryError("model_config_invalid", f"{path.name}:{line_no} has an empty key.")
        if raw_value == "":
            result[key] = []
            active_list_key = key
        else:
            result[key] = _parse_scalar(raw_value)
            active_list_key = None
    return result


def _parse_scalar(raw_value: str) -> Any:
    if raw_value in {"true", "True"}:
        return True
    if raw_value in {"false", "False"}:
        return False
    if (raw_value.startswith('"') and raw_value.endswith('"')) or (
        raw_value.startswith("'") and raw_value.endswith("'")
    ):
        return raw_value[1:-1]
    return raw_value


__all__ = [
    "MODEL_REVIEW_REGISTRY_FILE",
    "ModelRegistryError",
    "ModelReviewConfig",
    "STAGE19A_EXECUTABLE_ANALYSIS_MODE",
    "load_enabled_model_review_configs",
    "select_stage19a_mock_model_config",
]
