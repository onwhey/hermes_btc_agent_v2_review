"""ID and hash helpers for stage-20B model review chains.

This file belongs to `app/model_review_chain`. It creates deterministic,
bounded IDs and hashes used by the chain service. It does not read/write
databases, call external services, touch Redis, send Hermes, call model
providers, or perform trading.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid5, NAMESPACE_URL

from app.model_review_chain.schema import json_text


def build_chain_id(*, material_pack_id: str, chain_key: str, trace_id: str) -> str:
    """Build one bounded chain id for a create request."""

    digest = uuid5(NAMESPACE_URL, f"stage20b-chain:{material_pack_id}:{chain_key}:{trace_id}").hex[:24].upper()
    return f"CHAIN-{digest}"


def build_chain_step_id(*, chain_id: str, step_no: int) -> str:
    """Build one deterministic step id for a chain/step pair."""

    digest = uuid5(NAMESPACE_URL, f"stage20b-chain-step:{chain_id}:{step_no}").hex[:24].upper()
    return f"CHSTEP-{digest}"


def build_chain_model_analysis_run_id(*, chain_id: str, step_no: int, attempt_no: int) -> str:
    """Build one mock `model_analysis_run_id` for a chain step attempt."""

    digest = uuid5(NAMESPACE_URL, f"stage20b-model-analysis-run:{chain_id}:{step_no}:{attempt_no}").hex[
        :24
    ].upper()
    return f"MAR-CHAIN-{digest}"


def build_review_version_key(*, chain_id: str, step_no: int, attempt_no: int) -> str:
    """Build a compact review version key for a mock chain step attempt."""

    return stable_sha256_text(
        {
            "attempt_no": attempt_no,
            "chain_id": chain_id,
            "step_no": step_no,
            "type": "stage20b_mock_step_attempt",
        }
    )


def stable_sha256_text(value: Any) -> str:
    """Return deterministic SHA-256 text for a bounded JSON-serializable value."""

    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


__all__ = [
    "build_chain_id",
    "build_chain_model_analysis_run_id",
    "build_chain_step_id",
    "build_review_version_key",
    "stable_sha256_text",
]
