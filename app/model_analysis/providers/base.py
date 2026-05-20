"""Provider adapter contracts for stage-19 model analysis.

This file belongs to `app/model_analysis/providers`. It defines the common
request/response structures that keep business service code independent from
provider-specific HTTP details.

Called by: mock and DeepSeek providers plus `app/model_analysis/service.py`.
External services: none in this file. MySQL: none. Redis: none. Hermes: none.
DeepSeek: no call here. Trading execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from app.model_analysis.model_profile import ModelProfile, ModelProviderConfig
from app.model_analysis.types import ModelProviderResult, PromptBuildResult


@dataclass(frozen=True)
class ProviderRequest:
    """Unified request passed into real provider adapters."""

    prompt: PromptBuildResult
    profile: ModelProfile
    provider_config: ModelProviderConfig
    api_key: str
    trace_id: str
    material_pack_id: str
    model_analysis_run_id: str


@dataclass(frozen=True)
class ProviderResponse(ModelProviderResult):
    """Provider output plus compact metadata safe for business persistence."""

    raw_response_text: str = ""
    raw_response_hash: str | None = None
    raw_response_char_count: int = 0
    raw_response_byte_count: int = 0
    provider_request_id: str | None = None
    finish_reason: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    response_metadata: Mapping[str, Any] = field(default_factory=dict)
    reasoning_char_count: int = 0
    reasoning_byte_count: int = 0


class ModelReviewProvider(Protocol):
    """Minimal provider contract used by the stage-19 service."""

    provider_name: str
    model_name: str
    model_version: str

    def review_material(self, prompt: PromptBuildResult) -> ModelProviderResult:
        """Return a structured review result for the compact prompt summary."""


class RealModelReviewProvider(Protocol):
    """Protocol implemented by real provider adapters such as DeepSeek."""

    provider_name: str

    def call_review_model(self, request: ProviderRequest) -> ProviderResponse:
        """Call a real model and return a normalized provider response."""


class ProviderCallError(RuntimeError):
    """Raised when a provider request fails after adapter-level handling."""


__all__ = [
    "ModelReviewProvider",
    "ProviderCallError",
    "ProviderRequest",
    "ProviderResponse",
    "RealModelReviewProvider",
]
