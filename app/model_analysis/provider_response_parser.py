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
    Failure scenarios: HTTP/root-shape failures are handled by the adapter.
    Malformed final content is returned as an empty schema candidate with safe
    diagnostics so the service can block it as schema_invalid without dumping
    raw response text.
    External effects: none.
    """

    mapping = profile.response_mapping
    final_content = _value_at_path(raw_response, str(mapping.get("final_content_path", "")))
    final_text = final_content if isinstance(final_content, str) else ""
    output, parse_metadata = _parse_final_content_json(final_text)
    reasoning_content = _value_at_path(raw_response, str(mapping.get("reasoning_content_path", "")))
    usage = _value_at_path(raw_response, str(mapping.get("usage_path", "")))
    finish_reason = _value_at_path(raw_response, str(mapping.get("finish_reason_path", "")))
    provider_request_id = _value_at_path(raw_response, str(mapping.get("provider_request_id_path", "")))
    raw_text = json.dumps(raw_response, ensure_ascii=False, sort_keys=True, default=str)
    content_text = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str) if output else final_text
    reasoning_text = reasoning_content if isinstance(reasoning_content, str) else ""
    return ProviderResponse(
        output=output,
        output_char_count=len(content_text),
        output_byte_count=len(content_text.encode("utf-8")),
        final_content_text=final_text,
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
            **parse_metadata,
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
    _output, parse_metadata = _parse_final_content_json(final_text)
    reasoning_text = reasoning_content if isinstance(reasoning_content, str) else ""
    return ProviderResponse(
        output={},
        output_char_count=len(final_text),
        output_byte_count=len(final_text.encode("utf-8")),
        final_content_text=final_text,
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
            **parse_metadata,
            "parse_failed": True,
        },
        reasoning_char_count=len(reasoning_text),
        reasoning_byte_count=len(reasoning_text.encode("utf-8")),
    )


def _parse_final_content_json(final_content: str) -> tuple[Mapping[str, Any], dict[str, Any]]:
    stripped_content, code_fence_stripped = _strip_whole_json_code_fence(final_content)
    base_metadata = {
        "final_content_char_count": len(final_content),
        "final_content_byte_count": len(final_content.encode("utf-8")),
        "sanitized_content_preview": sanitize_content_preview(final_content),
        "json_code_fence_stripped": code_fence_stripped,
    }
    if not final_content:
        return {}, {
            **base_metadata,
            "parse_failed": True,
            "provider_parse_error_code": "schema_final_content_missing",
            "parsed_json_type": "missing",
        }
    try:
        decoded = json.loads(stripped_content)
    except json.JSONDecodeError:
        return {}, {
            **base_metadata,
            "parse_failed": True,
            "provider_parse_error_code": "schema_final_content_not_json",
            "parsed_json_type": "invalid_json",
        }
    if not isinstance(decoded, Mapping):
        return {}, {
            **base_metadata,
            "parse_failed": True,
            "provider_parse_error_code": "schema_final_content_not_object",
            "parsed_json_type": _json_type_name(decoded),
        }
    return decoded, {
        **base_metadata,
        "parse_failed": False,
        "parsed_json_type": "object",
    }


def sanitize_content_preview(content: str, *, max_chars: int = 500) -> str:
    """Return a bounded final-content preview without secret-bearing lines."""

    preview_lines: list[str] = []
    for line in content.replace("\r\n", "\n").replace("\r", "\n").strip().splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in ("authorization", "api_key", "apikey", "secret", "bearer ")):
            preview_lines.append("[redacted-sensitive-line]")
        else:
            preview_lines.append(line)
    preview = "\\n".join(preview_lines)
    if len(preview) > max_chars:
        return preview[:max_chars]
    return preview


def _strip_whole_json_code_fence(content: str) -> tuple[str, bool]:
    stripped = content.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return content, False
    lines = stripped.splitlines()
    if len(lines) < 2:
        return content, False
    opener = lines[0].strip()
    closer = lines[-1].strip()
    language = opener[3:].strip().lower()
    if not opener.startswith("```") or closer != "```" or language not in ("", "json"):
        return content, False
    return "\n".join(lines[1:-1]).strip(), True


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, list):
        return "array"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


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


__all__ = [
    "build_provider_response_metadata_from_raw",
    "parse_openai_style_response",
    "sanitize_content_preview",
]
