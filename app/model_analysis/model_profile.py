"""Model profile value objects for stage-19B model analysis.

This file belongs to `app/model_analysis`. It defines provider and model
profile metadata used to route model-review calls by `model_key`.

Called by `app/model_analysis/model_registry.py`, provider adapters, the
service, and tests.
External services: none. MySQL: none. Redis: none. Hermes: none. DeepSeek:
none in this file. Trading execution: none.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.model_analysis.types import MODEL_REVIEW_PROVIDER_MOCK


@dataclass(frozen=True)
class ModelProviderConfig:
    """Provider-level configuration loaded from `configs/model_review/providers`.

    Parameters: stable provider key, enabled flag, endpoint settings, and API
    key environment name.
    Return value: immutable provider metadata.
    Failure scenarios: validation happens in the registry loader.
    External services/database/Redis/Hermes/trading: none.
    """

    provider: str
    enabled: bool
    api_base_url: str
    api_key_env: str
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    provider_version: str = ""
    docs_checked_at: str = ""
    docs_source: tuple[str, ...] = field(default_factory=tuple)
    source_path: str = ""


@dataclass(frozen=True)
class ModelProfile:
    """One concrete model-version profile.

    `model_key` is the business-facing identifier. The service must not choose
    a real model by hard-coded model names; it resolves `model_key -> profile`
    through the registry.
    """

    model_key: str
    provider: str
    enabled: bool
    api_style: str
    model_name: str
    model_version: str
    profile_version: str
    model_role: str
    analysis_mode: str
    prompt_template_version: str
    review_schema_version: str
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    request_params: Mapping[str, Any] = field(default_factory=dict)
    response_mapping: Mapping[str, Any] = field(default_factory=dict)
    unsupported_params: tuple[str, ...] = field(default_factory=tuple)
    ignored_params_in_thinking_mode: tuple[str, ...] = field(default_factory=tuple)
    cost_policy: Mapping[str, Any] = field(default_factory=dict)
    docs_checked_at: str = ""
    docs_source: tuple[str, ...] = field(default_factory=tuple)
    source_path: str = ""
    profile_hash: str = ""

    @property
    def is_stage19a_executable_mock(self) -> bool:
        """Return whether this profile is the 19A-compatible mock profile."""

        return self.enabled and self.provider == MODEL_REVIEW_PROVIDER_MOCK and self.analysis_mode == "single"

    def with_hash(self) -> "ModelProfile":
        """Return a profile copy with deterministic `profile_hash` filled in."""

        if self.profile_hash:
            return self
        canonical = _canonical_profile_payload(self)
        profile_hash = hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return ModelProfile(
            model_key=self.model_key,
            provider=self.provider,
            enabled=self.enabled,
            api_style=self.api_style,
            model_name=self.model_name,
            model_version=self.model_version,
            profile_version=self.profile_version,
            model_role=self.model_role,
            analysis_mode=self.analysis_mode,
            prompt_template_version=self.prompt_template_version,
            review_schema_version=self.review_schema_version,
            capabilities=dict(self.capabilities),
            request_params=dict(self.request_params),
            response_mapping=dict(self.response_mapping),
            unsupported_params=tuple(self.unsupported_params),
            ignored_params_in_thinking_mode=tuple(self.ignored_params_in_thinking_mode),
            cost_policy=dict(self.cost_policy),
            docs_checked_at=self.docs_checked_at,
            docs_source=tuple(self.docs_source),
            source_path=self.source_path,
            profile_hash=profile_hash,
        )


@dataclass(frozen=True)
class ModelRegistrySelection:
    """Resolved model config used by service provider selection."""

    profile: ModelProfile
    provider_config: ModelProviderConfig | None
    registry_enabled: bool


def _canonical_profile_payload(profile: ModelProfile) -> dict[str, Any]:
    return {
        "model_key": profile.model_key,
        "provider": profile.provider,
        "enabled": profile.enabled,
        "api_style": profile.api_style,
        "model_name": profile.model_name,
        "model_version": profile.model_version,
        "profile_version": profile.profile_version,
        "model_role": profile.model_role,
        "analysis_mode": profile.analysis_mode,
        "prompt_template_version": profile.prompt_template_version,
        "review_schema_version": profile.review_schema_version,
        "capabilities": profile.capabilities,
        "request_params": profile.request_params,
        "response_mapping": profile.response_mapping,
        "unsupported_params": list(profile.unsupported_params),
        "ignored_params_in_thinking_mode": list(profile.ignored_params_in_thinking_mode),
        "cost_policy": profile.cost_policy,
        "docs_checked_at": profile.docs_checked_at,
        "docs_source": list(profile.docs_source),
    }


__all__ = ["ModelProfile", "ModelProviderConfig", "ModelRegistrySelection"]
