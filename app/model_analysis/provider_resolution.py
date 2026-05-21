"""Provider/profile resolution for stage-19 model analysis.

This file belongs to `app/model_analysis`. It turns a service request into a
provider resolution without calling any model.

Called by `app/model_analysis/service.py`. External services: none. MySQL:
none. Redis: none. Hermes: none. DeepSeek: no call here. Trading execution:
none.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.core.config import AppSettings
from app.model_analysis.model_profile import ModelProfile, ModelProviderConfig
from app.model_analysis.model_registry import (
    ModelRegistryError,
    load_enabled_model_review_configs,
    resolve_model_review_profile,
    select_stage19a_mock_model_config,
)
from app.model_analysis.prompt_builder import PROMPT_TEMPLATE_HASH
from app.model_analysis.providers.deepseek import DeepSeekReviewProvider
from app.model_analysis.providers.mock import MockModelReviewProvider
from app.model_analysis.schema_validator import (
    SCHEMA_NORMALIZATION_POLICY_HASH,
    SCHEMA_NORMALIZATION_POLICY_VERSION,
)
from app.model_analysis.types import (
    MODEL_REVIEW_MODEL_KEY_DEFAULT,
    MODEL_REVIEW_MODEL_ROLE_DEFAULT,
    MODEL_REVIEW_MODE_DEFAULT,
    MODEL_REVIEW_PROVIDER_DEEPSEEK,
    MODEL_REVIEW_PROVIDER_MOCK,
    ModelAnalysisRequest,
)


class ProviderResolution:
    """Resolved provider metadata and late-bound call summaries."""

    def __init__(
        self,
        *,
        provider: Any,
        provider_name: str,
        model_name: str,
        model_version: str,
        model_key: str,
        model_role: str,
        analysis_mode: str,
        prompt_template_version: str,
        review_schema_version: str,
        profile_version: str,
        profile_hash: str,
        prompt_template_hash: str,
        schema_normalization_policy_version: str,
        schema_normalization_policy_hash: str,
        api_style: str,
        profile: ModelProfile | None,
        provider_config: ModelProviderConfig | None,
        api_key: str,
        blocked_message: str | None,
        blocked_error_code: str | None,
    ) -> None:
        self.provider = provider
        self.provider_name = provider_name
        self.model_name = model_name
        self.model_version = model_version
        self.model_key = model_key
        self.model_role = model_role
        self.analysis_mode = analysis_mode
        self.prompt_template_version = prompt_template_version
        self.review_schema_version = review_schema_version
        self.profile_version = profile_version
        self.profile_hash = profile_hash
        self.api_style = api_style
        self.profile = profile
        self.provider_config = provider_config
        self.api_key = api_key
        self.prompt_template_hash = prompt_template_hash
        self.schema_normalization_policy_version = schema_normalization_policy_version
        self.schema_normalization_policy_hash = schema_normalization_policy_hash
        self.chain_id = None
        self.chain_step = None
        self.parent_model_analysis_run_id = None
        self.comparison_group_id = None
        self.blocked_message = blocked_message
        self.blocked_error_code = blocked_error_code
        self.request_payload_hash = None
        self.raw_request_hash = None
        self.raw_request_storage_ref = None
        self.raw_response_storage_ref = None
        self.request_params_summary_json: Mapping[str, Any] = {}
        self.capabilities_json: Mapping[str, Any] = {}
        self.provider_usage_json: Mapping[str, Any] = {}
        self.input_token_count: int | None = None
        self.output_token_count: int | None = None
        self.total_token_count: int | None = None
        self.estimated_cost: str | None = None
        self.cost_currency: str | None = None

    @property
    def is_real_model(self) -> bool:
        return self.provider_name != MODEL_REVIEW_PROVIDER_MOCK


def resolve_provider_for_request(
    *,
    settings: AppSettings,
    request: ModelAnalysisRequest,
    injected_provider: Any | None,
) -> ProviderResolution:
    """Resolve mock or real provider metadata without external calls."""

    if request.use_real_model:
        return _resolve_real_model_provider(settings=settings, request=request, injected_provider=injected_provider)
    return _resolve_mock_provider(settings=settings, injected_provider=injected_provider)


def _resolve_mock_provider(*, settings: AppSettings, injected_provider: Any | None) -> ProviderResolution:
    try:
        configs = load_enabled_model_review_configs(settings.model_review_config_dir)
    except ModelRegistryError as exc:
        return _base_resolution(settings=settings, blocked_message=exc.message, blocked_error_code=exc.error_code)
    selected_config = select_stage19a_mock_model_config(configs)
    if selected_config is None:
        return _base_resolution(
            settings=settings,
            blocked_message="no enabled mock model config is executable in stage 19A",
            blocked_error_code="no_enabled_mock_model_config",
        )
    provider = injected_provider or MockModelReviewProvider()
    injected_provider_name = str(getattr(provider, "provider_name", selected_config.provider)).strip().lower()
    if injected_provider_name != MODEL_REVIEW_PROVIDER_MOCK:
        return ProviderResolution(
            provider=None,
            provider_name=selected_config.provider,
            model_name=selected_config.model_name,
            model_version=selected_config.model_version,
            model_key=selected_config.model_key,
            model_role=selected_config.model_role,
            analysis_mode=selected_config.analysis_mode,
            prompt_template_version=selected_config.prompt_template_version,
            review_schema_version=selected_config.review_schema_version,
            profile_version=selected_config.profile_version,
            profile_hash=selected_config.profile_hash,
            prompt_template_hash=PROMPT_TEMPLATE_HASH,
            schema_normalization_policy_version=SCHEMA_NORMALIZATION_POLICY_VERSION,
            schema_normalization_policy_hash=SCHEMA_NORMALIZATION_POLICY_HASH,
            api_style=selected_config.api_style,
            profile=selected_config,
            provider_config=None,
            api_key="",
            blocked_message="real model provider is not implemented in stage 19A",
            blocked_error_code="provider_not_supported",
        )
    return ProviderResolution(
        provider=provider,
        provider_name=selected_config.provider,
        model_name=selected_config.model_name,
        model_version=selected_config.model_version,
        model_key=selected_config.model_key,
        model_role=selected_config.model_role,
        analysis_mode=selected_config.analysis_mode,
        prompt_template_version=selected_config.prompt_template_version,
        review_schema_version=selected_config.review_schema_version,
        profile_version=selected_config.profile_version,
        profile_hash=selected_config.profile_hash,
        prompt_template_hash=PROMPT_TEMPLATE_HASH,
        schema_normalization_policy_version=SCHEMA_NORMALIZATION_POLICY_VERSION,
        schema_normalization_policy_hash=SCHEMA_NORMALIZATION_POLICY_HASH,
        api_style=selected_config.api_style,
        profile=selected_config,
        provider_config=None,
        api_key="",
        blocked_message=None,
        blocked_error_code=None,
    )


def _resolve_real_model_provider(
    *,
    settings: AppSettings,
    request: ModelAnalysisRequest,
    injected_provider: Any | None,
) -> ProviderResolution:
    base = ProviderResolution(
        provider=None,
        provider_name=MODEL_REVIEW_PROVIDER_DEEPSEEK,
        model_name="",
        model_version="",
        model_key=request.model_key or "",
        model_role=MODEL_REVIEW_MODEL_ROLE_DEFAULT,
        analysis_mode=MODEL_REVIEW_MODE_DEFAULT,
        prompt_template_version=settings.model_review_prompt_template_version,
        review_schema_version=settings.model_review_schema_version,
        profile_version="",
        profile_hash="",
        prompt_template_hash=PROMPT_TEMPLATE_HASH,
        schema_normalization_policy_version=SCHEMA_NORMALIZATION_POLICY_VERSION,
        schema_normalization_policy_hash=SCHEMA_NORMALIZATION_POLICY_HASH,
        api_style="",
        profile=None,
        provider_config=None,
        api_key="",
        blocked_message=None,
        blocked_error_code=None,
    )
    if not request.confirm_real_model_cost:
        base.blocked_message = "--confirm-real-model-cost is required for real model calls."
        base.blocked_error_code = "real_model_cost_not_confirmed"
        return base
    if not request.model_key:
        base.blocked_message = "--model-key is required for real model calls."
        base.blocked_error_code = "real_model_model_key_required"
        return base
    if not settings.model_review_real_model_enabled:
        base.blocked_message = "MODEL_REVIEW_REAL_MODEL_ENABLED=false blocks real model calls."
        base.blocked_error_code = "real_model_disabled"
        return base
    try:
        selection = resolve_model_review_profile(settings.model_review_config_dir, model_key=request.model_key)
    except ModelRegistryError as exc:
        base.blocked_message = exc.message
        base.blocked_error_code = exc.error_code
        return base
    profile = selection.profile
    provider_config = selection.provider_config
    base.provider_name = profile.provider
    base.model_name = profile.model_name
    base.model_version = profile.model_version
    base.model_key = profile.model_key
    base.model_role = profile.model_role
    base.analysis_mode = profile.analysis_mode
    base.prompt_template_version = profile.prompt_template_version
    base.review_schema_version = profile.review_schema_version
    base.profile_version = profile.profile_version
    base.profile_hash = profile.profile_hash
    base.prompt_template_hash = PROMPT_TEMPLATE_HASH
    base.schema_normalization_policy_version = SCHEMA_NORMALIZATION_POLICY_VERSION
    base.schema_normalization_policy_hash = SCHEMA_NORMALIZATION_POLICY_HASH
    base.api_style = profile.api_style
    base.profile = profile
    base.provider_config = provider_config
    if not profile.enabled:
        base.blocked_message = f"model profile is disabled: {profile.model_key}"
        base.blocked_error_code = "model_profile_disabled"
        return base
    if provider_config is None:
        base.blocked_message = f"provider config is missing for model_key: {profile.model_key}"
        base.blocked_error_code = "provider_config_missing"
        return base
    if not provider_config.enabled:
        base.blocked_message = f"model provider is disabled: {provider_config.provider}"
        base.blocked_error_code = "model_provider_disabled"
        return base
    if profile.provider != MODEL_REVIEW_PROVIDER_DEEPSEEK:
        base.blocked_message = f"real provider is not implemented in stage 19B: {profile.provider}"
        base.blocked_error_code = "provider_not_supported"
        return base
    api_key = _api_key_for_provider(settings, provider_config)
    if not api_key:
        base.blocked_message = f"API key is missing for provider: {provider_config.provider}"
        base.blocked_error_code = "provider_api_key_missing"
        return base
    base.api_key = api_key
    base.provider = injected_provider or DeepSeekReviewProvider()
    return base


def _base_resolution(
    *,
    settings: AppSettings,
    blocked_message: str,
    blocked_error_code: str,
) -> ProviderResolution:
    return ProviderResolution(
        provider=None,
        provider_name=MODEL_REVIEW_PROVIDER_MOCK,
        model_name="",
        model_version="",
        model_key=MODEL_REVIEW_MODEL_KEY_DEFAULT,
        model_role=MODEL_REVIEW_MODEL_ROLE_DEFAULT,
        analysis_mode=MODEL_REVIEW_MODE_DEFAULT,
        prompt_template_version=settings.model_review_prompt_template_version,
        review_schema_version=settings.model_review_schema_version,
        profile_version="",
        profile_hash="",
        prompt_template_hash=PROMPT_TEMPLATE_HASH,
        schema_normalization_policy_version=SCHEMA_NORMALIZATION_POLICY_VERSION,
        schema_normalization_policy_hash=SCHEMA_NORMALIZATION_POLICY_HASH,
        api_style="",
        profile=None,
        provider_config=None,
        api_key="",
        blocked_message=blocked_message,
        blocked_error_code=blocked_error_code,
    )


def _api_key_for_provider(settings: AppSettings, provider_config: ModelProviderConfig) -> str:
    if provider_config.api_key_env == "DEEPSEEK_API_KEY":
        return settings.deepseek_api_key.strip()
    return ""


__all__ = ["ProviderResolution", "resolve_provider_for_request"]
