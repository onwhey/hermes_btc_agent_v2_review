"""Artifact isolation for stage-19B provider payloads.

This file belongs to `app/model_analysis`. It writes optional or oversized
provider payloads into an isolated local artifact directory and returns only
hash/length/reference metadata for database persistence.

Called by `app/model_analysis/service.py`. External services: none. MySQL:
none in this file. Redis: none. Hermes: none. DeepSeek: none. Trading
execution: none.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import ROOT_DIR, AppSettings
from app.core.time_utils import now_utc


@dataclass(frozen=True)
class ArtifactWriteResult:
    """Metadata for an isolated model-provider artifact."""

    artifact_id: str
    artifact_type: str
    storage_ref: str
    sha256_hash: str
    char_count: int
    byte_count: int
    capture_reason: str


def write_model_provider_artifact(
    *,
    settings: AppSettings,
    artifact_type: str,
    content: str,
    capture_reason: str,
) -> ArtifactWriteResult:
    """Write one provider artifact and return metadata only.

    Parameters: bounded project settings, artifact type, raw text, and reason.
    Return value: artifact metadata suitable for the artifact table.
    Failure scenarios: filesystem errors or byte-limit breaches raise
    `RuntimeError`; caller decides whether that blocks the review.
    External effects: writes under `MODEL_REVIEW_ARTIFACT_DIR`.
    """

    byte_count = len(content.encode("utf-8"))
    if byte_count > settings.model_review_raw_artifact_max_bytes:
        raise RuntimeError("model provider artifact exceeds MODEL_REVIEW_RAW_ARTIFACT_MAX_BYTES")
    artifact_id = f"MPCA-{uuid4().hex}"
    today = now_utc().strftime("%Y%m%d")
    base_dir = Path(settings.model_review_artifact_dir)
    if not base_dir.is_absolute():
        base_dir = ROOT_DIR / base_dir
    artifact_dir = base_dir / today
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{artifact_id}.json"
    sha256_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    payload: dict[str, Any] = {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "capture_reason": capture_reason,
        "sha256_hash": sha256_hash,
        "char_count": len(content),
        "byte_count": byte_count,
        "content": content,
    }
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    try:
        storage_ref = str(artifact_path.relative_to(ROOT_DIR))
    except ValueError:
        storage_ref = str(artifact_path)
    return ArtifactWriteResult(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        storage_ref=storage_ref,
        sha256_hash=sha256_hash,
        char_count=len(content),
        byte_count=byte_count,
        capture_reason=capture_reason,
    )


__all__ = ["ArtifactWriteResult", "write_model_provider_artifact"]
