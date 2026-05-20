"""DeepSeek provider adapter for stage-19B model analysis.

This file belongs to `app/model_analysis/providers`. It constructs and sends
DeepSeek OpenAI-compatible chat-completion requests using model profile
metadata, then maps responses into the unified provider response structure.

Called by `app/model_analysis/service.py`.
External services: may call DeepSeek only when the service has already passed
all real-model gates. MySQL: none. Redis: none. Hermes: none. Trading
execution: none. It never calls Binance or trading endpoints.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Mapping

from app.model_analysis.model_profile import ModelProfile, ModelProviderConfig
from app.model_analysis.provider_response_parser import (
    build_provider_response_metadata_from_raw,
    parse_openai_style_response,
)
from app.model_analysis.providers.base import ProviderCallError, ProviderRequest, ProviderResponse

SUPPORTED_DEEPSEEK_API_STYLES = frozenset({"openai_chat_completion"})
DEEPSEEK_THINKING_IGNORED_PARAMS = frozenset(
    {"temperature", "top_p", "presence_penalty", "frequency_penalty"}
)


def validate_deepseek_model_profile(
    profile: ModelProfile,
    *,
    provider_config: ModelProviderConfig,
) -> tuple[str, str] | None:
    """Validate DeepSeek-only profile rules without constraining other providers.

    Parameters: one loaded model profile and its provider YAML config.
    Return value: `(error_code, message)` when invalid, otherwise `None`.
    Failure scenarios: the caller turns the returned error into registry
    blocked status. External services/MySQL/Redis/Hermes/trading: none.
    """

    supported_model_names = set(provider_config.supported_model_names)
    if not supported_model_names:
        return (
            "deepseek_provider_supported_models_missing",
            "provider config supported_model_names is required for DeepSeek profiles.",
        )
    if profile.model_name not in supported_model_names:
        return (
            "deepseek_profile_model_name_unsupported",
            f"model_name is not a supported DeepSeek API model string: {profile.model_name}",
        )
    for field_name in ("max_tokens", "response_format"):
        if field_name not in profile.request_params:
            return (
                "deepseek_profile_missing_request_param",
                f"request_params.{field_name} is required for DeepSeek profiles.",
            )
    if bool(profile.capabilities.get("thinking")):
        extra_body = profile.request_params.get("extra_body", {})
        if not isinstance(extra_body, dict):
            return (
                "deepseek_profile_missing_thinking_mode",
                "request_params.extra_body.thinking.type=enabled is required.",
            )
        thinking_body = extra_body.get("thinking", {})
        if not isinstance(thinking_body, dict) or thinking_body.get("type") != "enabled":
            return (
                "deepseek_profile_missing_thinking_mode",
                "request_params.extra_body.thinking.type=enabled is required.",
            )
        if "reasoning_effort" not in profile.request_params:
            return (
                "deepseek_profile_missing_reasoning_effort",
                "request_params.reasoning_effort is required for thinking mode.",
            )
        ignored = set(profile.ignored_params_in_thinking_mode)
        missing_ignored = sorted(DEEPSEEK_THINKING_IGNORED_PARAMS - ignored)
        if missing_ignored:
            return (
                "deepseek_profile_missing_ignored_thinking_params",
                f"ignored_params_in_thinking_mode missing: {', '.join(missing_ignored)}",
            )
    return None


class UrllibJsonHttpClient:
    """Small JSON HTTP client used by the DeepSeek adapter.

    Tests inject a fake client so default pytest never accesses the network.
    """

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        """POST JSON and return JSON without logging secrets or payload dumps."""

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={**dict(headers), "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderCallError(f"DeepSeek request failed: {exc}") from exc
        try:
            decoded = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise ProviderCallError("DeepSeek response is not valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise ProviderCallError("DeepSeek response root is not a JSON object")
        return decoded


class DeepSeekReviewProvider:
    """DeepSeek adapter that is driven by Model Profile metadata only."""

    provider_name = "deepseek"

    def __init__(self, *, http_client: Any | None = None) -> None:
        self._http_client = http_client or UrllibJsonHttpClient()

    def call_review_model(self, request: ProviderRequest) -> ProviderResponse:
        """Call DeepSeek and parse a unified review result.

        Parameters: a gated provider request from the service.
        Return value: schema-candidate output plus compact provider metadata.
        Failure scenarios: unsupported profile api style, HTTP errors, invalid
        provider JSON, or unmappable response content raise `ProviderCallError`.
        External services: DeepSeek HTTPS API.
        """

        profile = request.profile
        if profile.api_style not in SUPPORTED_DEEPSEEK_API_STYLES:
            raise ProviderCallError(f"DeepSeek api_style is not supported: {profile.api_style}")
        payload = self.build_request_payload(request)
        url = f"{request.provider_config.api_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {request.api_key}",
            "X-Trace-Id": request.trace_id,
        }
        attempts = max(request.provider_config.max_retries, 0) + 1
        last_error: ProviderCallError | None = None
        for attempt in range(attempts):
            raw_response: Mapping[str, Any] | None = None
            try:
                raw_response = self._http_client.post_json(
                    url=url,
                    headers=headers,
                    payload=payload,
                    timeout_seconds=request.provider_config.timeout_seconds,
                )
                return parse_openai_style_response(raw_response, profile=profile)
            except ProviderCallError as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - adapter converts to provider failure.
                provider_response = (
                    build_provider_response_metadata_from_raw(raw_response, profile=profile)
                    if raw_response is not None
                    else None
                )
                last_error = ProviderCallError(str(exc), provider_response=provider_response)
            if attempt < attempts - 1:
                time.sleep(max(request.provider_config.retry_backoff_seconds, 0))
        raise last_error or ProviderCallError("DeepSeek request failed")

    def build_request_payload(self, request: ProviderRequest) -> dict[str, Any]:
        """Build the OpenAI-compatible payload from profile request params."""

        profile = request.profile
        payload: dict[str, Any] = {
            "model": profile.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是策略材料审查员，不是交易员。你不能给最终交易建议，不能给入场价、"
                        "止损价、止盈价、仓位或杠杆。你只能审查材料完整性、证据质量、逻辑一致性、"
                        "风险接受度、策略冲突和是否需要人工审核。必须输出 JSON，且 "
                        "not_trading_advice 必须为 true。"
                    ),
                },
                {"role": "user", "content": request.prompt.prompt_text},
            ],
        }
        thinking_enabled = (
            isinstance(profile.request_params.get("extra_body"), Mapping)
            and isinstance(profile.request_params["extra_body"].get("thinking"), Mapping)
            and profile.request_params["extra_body"]["thinking"].get("type") == "enabled"
        )
        ignored_params = set(profile.ignored_params_in_thinking_mode) if thinking_enabled else set()
        for key, value in profile.request_params.items():
            if key in profile.unsupported_params:
                continue
            if key in ignored_params:
                continue
            if key == "extra_body" and isinstance(value, Mapping):
                payload.update(dict(value))
                continue
            payload[key] = value
        return payload


__all__ = [
    "DeepSeekReviewProvider",
    "SUPPORTED_DEEPSEEK_API_STYLES",
    "UrllibJsonHttpClient",
    "validate_deepseek_model_profile",
]
