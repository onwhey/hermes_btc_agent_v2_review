"""Provider protocol for stage-19 model analysis review gate.

This file defines the small interface used by the service. Stage 19A ships
only a mock implementation; no real provider client is implemented here.
"""

from __future__ import annotations

from typing import Protocol

from app.model_analysis.types import ModelProviderResult, PromptBuildResult


class ModelReviewProvider(Protocol):
    """Minimal provider contract used by the stage-19 service."""

    provider_name: str
    model_name: str
    model_version: str

    def review_material(self, prompt: PromptBuildResult) -> ModelProviderResult:
        """Return a structured review result for the compact prompt summary."""


__all__ = ["ModelReviewProvider"]
