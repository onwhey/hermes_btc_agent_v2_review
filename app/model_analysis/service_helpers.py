"""Small helpers for the stage-19 model analysis service.

This file belongs to `app/model_analysis`. It contains deterministic hashing,
size-limit error formatting, and artifact persistence payload construction for
`app/model_analysis/service.py`.

It does not call model providers, does not read or write MySQL directly, does
not read or write Redis, does not send Hermes, does not call DeepSeek, and does
not perform or suggest trading actions.
"""

from __future__ import annotations

import hashlib

from app.model_analysis.artifact_store import ArtifactWriteResult
from app.model_analysis.provider_resolution import ProviderResolution
from app.model_analysis.types import ModelProviderCallArtifactPersistencePayload


def build_limit_error(
    *,
    char_count: int,
    byte_count: int,
    max_chars: int,
    max_bytes: int,
    prefix: str,
) -> dict[str, str] | None:
    """Return a blocked-result message when a prompt or output exceeds limits."""

    if char_count > max_chars:
        return {
            "message": f"Model review {prefix} exceeds {max_chars} characters.",
            "error_code": f"{prefix}_char_limit_exceeded",
        }
    if byte_count > max_bytes:
        return {
            "message": f"Model review {prefix} exceeds {max_bytes} bytes.",
            "error_code": f"{prefix}_byte_limit_exceeded",
        }
    return None


def build_artifact_payload(
    artifact: ArtifactWriteResult,
    *,
    model_analysis_run_id: str,
    provider_resolution: ProviderResolution,
) -> ModelProviderCallArtifactPersistencePayload:
    """Convert an isolated provider artifact into a repository payload."""

    return ModelProviderCallArtifactPersistencePayload(
        artifact_id=artifact.artifact_id,
        model_analysis_run_id=model_analysis_run_id,
        artifact_type=artifact.artifact_type,
        provider=provider_resolution.provider_name,
        model_key=provider_resolution.model_key,
        model_name=provider_resolution.model_name,
        model_version=provider_resolution.model_version,
        profile_hash=provider_resolution.profile_hash,
        storage_ref=artifact.storage_ref,
        sha256_hash=artifact.sha256_hash,
        char_count=artifact.char_count,
        byte_count=artifact.byte_count,
        capture_reason=artifact.capture_reason,
    )


def sha256_text(value: str) -> str:
    """Return the UTF-8 SHA-256 hash used for prompts, requests, and responses."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["build_artifact_payload", "build_limit_error", "sha256_text"]
