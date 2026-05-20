"""Provider response parsing helpers for stage-19B.

This file belongs to `app/model_analysis`. It extracts final JSON content,
usage, finish reason, and compact metadata from provider-specific response
shapes using model profile mappings.

Called by provider adapters. External services: none. MySQL: none. Redis:
none. Hermes: none. DeepSeek: none in this file. Trading execution: none.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from app.model_analysis.model_profile import ModelProfile
from app.model_analysis.providers.base import ProviderResponse


def parse_openai_style_response(raw_response: Mapping[str, Any], *, profile: ModelProfile) -> ProviderResponse:
    """Parse an OpenAI-compatible chat-completion response.

    Parameters: provider JSON response and a profile with response mappings.
    Return value: `ProviderResponse` with schema candidate output plus compact
    metadata.
    Failure scenarios: malformed content raises `ValueError` for the adapter
    to convert into provider call failure.
    External effects: none.
    """

    mapping = profile.response_mapping
    final_content = _value_at_path(raw_response, str(mapping.get("final_content_path", "")))
    if not isinstance(final_content, str):
        raise ValueError("provider response final content is missing or not text")
    output = _parse_final_content_json(final_content)
    reasoning_content = _value_at_path(raw_response, str(mapping.get("reasoning_content_path", "")))
    usage = _value_at_path(raw_response, str(mapping.get("usage_path", "")))
    finish_reason = _value_at_path(raw_response, str(mapping.get("finish_reason_path", "")))
    provider_request_id = _value_at_path(raw_response, str(mapping.get("provider_request_id_path", "")))
    raw_text = json.dumps(raw_response, ensure_ascii=False, sort_keys=True, default=str)
    content_text = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
    reasoning_text = reasoning_content if isinstance(reasoning_content, str) else ""
    return ProviderResponse(
        output=output,
        output_char_count=len(content_text),
        output_byte_count=len(content_text.encode("utf-8")),
        raw_response_text=raw_text,
        raw_response_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        raw_response_char_count=len(raw_text),
        raw_response_byte_count=len(raw_text.encode("utf-8")),
        provider_request_id=str(provider_request_id) if provider_request_id else None,
        finish_reason=str(finish_reason) if finish_reason else None,
        usage=usage if isinstance(usage, Mapping) else {},
        response_metadata={
            "finish_reason": str(finish_reason) if finish_reason else "",
            "provider_request_id": str(provider_request_id) if provider_request_id else "",
            "reasoning_content_present": bool(reasoning_text),
            "reasoning_char_count": len(reasoning_text),
            "reasoning_byte_count": len(reasoning_text.encode("utf-8")),
        },
        reasoning_char_count=len(reasoning_text),
        reasoning_byte_count=len(reasoning_text.encode("utf-8")),
    )


def build_provider_response_metadata_from_raw(
    raw_response: Mapping[str, Any],
    *,
    profile: ModelProfile,
) -> ProviderResponse:
    """Return raw-response metadata when final content cannot be parsed safely.

    The returned object intentionally contains an empty structured output. The
    service records raw-response hash/length/storage metadata on the run row and
    blocks or fails the attempt without saving full response text in business
    tables.
    """

    mapping = profile.response_mapping
    raw_text = json.dumps(raw_response, ensure_ascii=False, sort_keys=True, default=str)
    final_content = _value_at_path(raw_response, str(mapping.get("final_content_path", "")))
    reasoning_content = _value_at_path(raw_response, str(mapping.get("reasoning_content_path", "")))
    usage = _value_at_path(raw_response, str(mapping.get("usage_path", "")))
    finish_reason = _value_at_path(raw_response, str(mapping.get("finish_reason_path", "")))
    provider_request_id = _value_at_path(raw_response, str(mapping.get("provider_request_id_path", "")))
    final_text = final_content if isinstance(final_content, str) else ""
    reasoning_text = reasoning_content if isinstance(reasoning_content, str) else ""
    return ProviderResponse(
        output={},
        output_char_count=len(final_text),
        output_byte_count=len(final_text.encode("utf-8")),
        raw_response_text=raw_text,
        raw_response_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        raw_response_char_count=len(raw_text),
        raw_response_byte_count=len(raw_text.encode("utf-8")),
        provider_request_id=str(provider_request_id) if provider_request_id else None,
        finish_reason=str(finish_reason) if finish_reason else None,
        usage=usage if isinstance(usage, Mapping) else {},
        response_metadata={
            "finish_reason": str(finish_reason) if finish_reason else "",
            "provider_request_id": str(provider_request_id) if provider_request_id else "",
            "reasoning_content_present": bool(reasoning_text),
            "reasoning_char_count": len(reasoning_text),
            "reasoning_byte_count": len(reasoning_text.encode("utf-8")),
            "parse_failed": True,
        },
        reasoning_char_count=len(reasoning_text),
        reasoning_byte_count=len(reasoning_text.encode("utf-8")),
    )


def _parse_final_content_json(final_content: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(final_content)
    except json.JSONDecodeError as exc:
        raise ValueError("provider response final content is not valid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError("provider response final content must be a JSON object")
    return decoded


def _value_at_path(value: Any, path: str) -> Any:
    if not path:
        return None
    current = value
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
            continue
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
            continue
        return None
    return current


__all__ = ["build_provider_response_metadata_from_raw", "parse_openai_style_response"]
