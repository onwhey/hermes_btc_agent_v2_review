from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from app.core.config import AppSettings
from app.model_analysis.hermes_formatter import (
    build_model_analysis_artifact_write_failed_visible_body,
    build_model_analysis_oversized_response_visible_body,
    build_model_analysis_provider_call_failed_visible_body,
)
from app.model_analysis.model_registry import ModelRegistryError, resolve_model_review_profile
from app.model_analysis.providers.deepseek import DeepSeekReviewProvider
from app.model_analysis.providers.base import ProviderCallError, ProviderRequest, ProviderResponse
from app.model_analysis.service import ModelAnalysisService
from app.model_analysis.types import (
    ModelAnalysisRequest,
    ModelAnalysisStatus,
    format_model_analysis_result_lines,
)
from tests.model_analysis.test_model_analysis_service import (
    FakeAlertSender,
    FakeModelAnalysisRepository,
    FakeSession,
    _valid_provider_output,
    material_pack,
)


class ArtifactRepository(FakeModelAnalysisRepository):
    def __init__(self, *, artifact_error: Exception | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.artifact_rows: list[Any] = []
        self.artifact_error = artifact_error

    def create_model_provider_call_artifact(self, _db_session: Any, *, payload: Any) -> Any:
        if self.artifact_error is not None:
            raise self.artifact_error
        row = SimpleNamespace(**payload.__dict__)
        row.id = len(self.artifact_rows) + 1
        self.artifact_rows.append(row)
        return row


class FakeDeepSeekProvider:
    provider_name = "deepseek"

    def __init__(
        self,
        *,
        output: Mapping[str, Any] | None = None,
        raw_padding: int = 0,
        raise_error: Exception | None = None,
        repo_to_assert_running: ArtifactRepository | None = None,
    ) -> None:
        self.output = dict(output or _valid_provider_output())
        self.raw_padding = raw_padding
        self.raise_error = raise_error
        self.repo_to_assert_running = repo_to_assert_running
        self.calls = 0
        self.last_request: ProviderRequest | None = None

    def call_review_model(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        self.last_request = request
        if self.repo_to_assert_running is not None:
            assert len(self.repo_to_assert_running.run_rows) == 1
            assert self.repo_to_assert_running.run_rows[0].status == "running"
            assert self.repo_to_assert_running.result_rows == []
        if self.raise_error is not None:
            raise self.raise_error
        content_text = json.dumps(self.output, ensure_ascii=False, sort_keys=True)
        raw_response = {
            "id": "ds-test-request",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": content_text,
                        "reasoning_content": "compact hidden reasoning summary",
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            "padding": "x" * self.raw_padding,
        }
        raw_text = json.dumps(raw_response, ensure_ascii=False, sort_keys=True)
        return ProviderResponse(
            output=self.output,
            output_char_count=len(content_text),
            output_byte_count=len(content_text.encode("utf-8")),
            raw_response_text=raw_text,
            raw_response_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            raw_response_char_count=len(raw_text),
            raw_response_byte_count=len(raw_text.encode("utf-8")),
            provider_request_id="ds-test-request",
            finish_reason="stop",
            usage=raw_response["usage"],
            response_metadata={
                "finish_reason": "stop",
                "provider_request_id": "ds-test-request",
                "reasoning_content_present": True,
            },
            reasoning_char_count=32,
            reasoning_byte_count=32,
        )

    def build_request_payload(self, request: ProviderRequest) -> dict[str, Any]:
        return {
            "model": request.profile.model_name,
            "messages": [{"role": "user", "content": request.prompt.prompt_text}],
            **dict(request.profile.request_params),
        }


class FakeDeepSeekHttpClient:
    def __init__(self, raw_response: Mapping[str, Any]) -> None:
        self.raw_response = raw_response
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.raw_response


def test_real_model_gates_block_before_provider_call(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    provider = FakeDeepSeekProvider()
    repo = ArtifactRepository(material_pack=material_pack())

    cases = (
        (
            ModelAnalysisRequest(
                material_pack_id="AMP-stage19",
                trigger_source="cli",
                use_real_model=True,
                model_key="deepseek_v4_pro_review",
            ),
            AppSettings(model_review_config_dir=str(config_dir), model_review_real_model_enabled=True),
            "real_model_cost_not_confirmed",
        ),
        (
            ModelAnalysisRequest(
                material_pack_id="AMP-stage19",
                trigger_source="cli",
                use_real_model=True,
                confirm_real_model_cost=True,
            ),
            AppSettings(model_review_config_dir=str(config_dir), model_review_real_model_enabled=True),
            "real_model_model_key_required",
        ),
        (
            _real_request(),
            AppSettings(model_review_config_dir=str(config_dir), model_review_real_model_enabled=False),
            "real_model_disabled",
        ),
        (
            _real_request(),
            AppSettings(model_review_config_dir=str(config_dir), model_review_real_model_enabled=True),
            "provider_api_key_missing",
        ),
    )

    for request, settings, error_code in cases:
        result = ModelAnalysisService(settings=settings, repository=repo, provider=provider).run_model_analysis(
            FakeSession(),
            request=request,
        )
        assert result.status == ModelAnalysisStatus.BLOCKED
        assert result.error_code == error_code

    assert provider.calls == 0


def test_provider_profile_registry_switches_are_independent_gates(tmp_path: Path) -> None:
    provider_disabled_dir = _write_deepseek_config(tmp_path / "provider_disabled", provider_enabled=False)
    profile_disabled_dir = _write_deepseek_config(tmp_path / "profile_disabled", profile_enabled=False)
    registry_disabled_dir = _write_deepseek_config(tmp_path / "registry_disabled", registry_models=["mock_review"])
    provider = FakeDeepSeekProvider()

    cases = (
        (provider_disabled_dir, "model_provider_disabled"),
        (profile_disabled_dir, "model_profile_disabled"),
        (registry_disabled_dir, "model_key_not_enabled_in_registry"),
    )
    for config_dir, error_code in cases:
        result = ModelAnalysisService(
            settings=_real_settings(config_dir),
            repository=ArtifactRepository(material_pack=material_pack()),
            provider=provider,
        ).run_model_analysis(FakeSession(), request=_real_request())

        assert result.status == ModelAnalysisStatus.BLOCKED
        assert result.error_code == error_code

    assert provider.calls == 0


def test_deepseek_profile_hash_and_disabled_flash_profile(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config", include_flash=True)

    pro = resolve_model_review_profile(str(config_dir), model_key="deepseek_v4_pro_review")
    flash = resolve_model_review_profile(str(config_dir), model_key="deepseek_v4_flash_review")

    assert pro.profile.enabled is True
    assert pro.profile.provider == "deepseek"
    assert pro.provider_config is not None
    assert "deepseek-v4-pro" in pro.provider_config.supported_model_names
    assert pro.profile.profile_hash
    assert pro.profile.docs_checked_at == "2026-05-20T00:00:00Z"
    assert pro.profile.docs_source
    assert pro.profile.request_params["reasoning_effort"] == "high"
    assert pro.profile.request_params["extra_body"]["thinking"]["type"] == "enabled"
    assert "temperature" in pro.profile.ignored_params_in_thinking_mode
    assert "top_p" in pro.profile.ignored_params_in_thinking_mode
    assert flash.profile.enabled is False
    assert flash.profile.model_name == "deepseek-v4-flash"


def test_deepseek_supported_model_names_missing_blocks_profile(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config", include_supported_model_names=False)

    try:
        resolve_model_review_profile(str(config_dir), model_key="deepseek_v4_pro_review")
    except ModelRegistryError as exc:
        assert exc.error_code == "deepseek_provider_supported_models_missing"
    else:  # pragma: no cover - explicit failure keeps the regression visible.
        raise AssertionError("DeepSeek provider without supported_model_names must be rejected")


def test_deepseek_supported_model_names_empty_blocks_profile(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config", provider_supported_model_names=[])

    try:
        resolve_model_review_profile(str(config_dir), model_key="deepseek_v4_pro_review")
    except ModelRegistryError as exc:
        assert exc.error_code == "deepseek_provider_supported_models_missing"
    else:  # pragma: no cover - explicit failure keeps the regression visible.
        raise AssertionError("DeepSeek provider with empty supported_model_names must be rejected")


def test_deepseek_profile_model_name_outside_provider_yaml_blocks(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(
        tmp_path / "config",
        provider_supported_model_names=["deepseek-v4-flash"],
    )

    try:
        resolve_model_review_profile(str(config_dir), model_key="deepseek_v4_pro_review")
    except ModelRegistryError as exc:
        assert exc.error_code == "deepseek_profile_model_name_unsupported"
        assert "deepseek-v4-pro" in exc.message
    else:  # pragma: no cover - explicit failure keeps the regression visible.
        raise AssertionError("DeepSeek profile model_name outside provider YAML must be rejected")


def test_deepseek_provider_yaml_can_allow_new_model_without_python_whitelist(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(
        tmp_path / "config",
        provider_supported_model_names=["deepseek-v4-pro", "deepseek-v4-new"],
        extra_model_key="deepseek_new_review",
        extra_model_version="v4_new",
        extra_model_name="deepseek-v4-new",
    )

    selection = resolve_model_review_profile(str(config_dir), model_key="deepseek_new_review")
    provider = DeepSeekReviewProvider(http_client=object())
    prompt = SimpleNamespace(prompt_text="compact prompt")

    payload = provider.build_request_payload(
        ProviderRequest(
            prompt=prompt,  # type: ignore[arg-type]
            profile=selection.profile,
            provider_config=selection.provider_config,
            api_key="secret-test-key",
            trace_id="trace-new-model-name",
            material_pack_id="AMP-stage19",
            model_analysis_run_id="MAR-stage19",
        )
    )

    assert selection.profile.model_key == "deepseek_new_review"
    assert selection.profile.model_name == "deepseek-v4-new"
    assert payload["model"] == "deepseek-v4-new"
    assert payload["model"] != selection.profile.model_key


def test_generic_registry_does_not_apply_deepseek_thinking_rules_to_other_providers(tmp_path: Path) -> None:
    config_dir = _write_future_provider_config(tmp_path / "future_config")

    selection = resolve_model_review_profile(str(config_dir), model_key="future_thinking_review")

    assert selection.profile.provider == "future_provider"
    assert selection.profile.capabilities["thinking"] is True
    assert selection.profile.ignored_params_in_thinking_mode == ()
    assert selection.profile.request_params == {"max_output_tokens": 1024}


def test_deepseek_profile_validator_requires_explicit_thinking_mode(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    profile_path = config_dir / "profiles" / "deepseek" / "deepseek_v4_pro_review.yaml"
    profile_text = profile_path.read_text(encoding="utf-8")
    profile_text = profile_text.replace(
        "  extra_body:\n    thinking:\n      type: enabled\n",
        "",
    )
    profile_path.write_text(profile_text, encoding="utf-8")

    try:
        resolve_model_review_profile(str(config_dir), model_key="deepseek_v4_pro_review")
    except ModelRegistryError as exc:
        assert exc.error_code == "deepseek_profile_missing_thinking_mode"
    else:  # pragma: no cover - explicit failure keeps the regression visible.
        raise AssertionError("DeepSeek profile without explicit thinking mode must be rejected")


def test_deepseek_request_payload_uses_profile_model_name_not_model_key(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    selection = resolve_model_review_profile(str(config_dir), model_key="deepseek_v4_pro_review")
    provider = DeepSeekReviewProvider(http_client=object())
    prompt = SimpleNamespace(prompt_text="compact prompt")

    payload = provider.build_request_payload(
        ProviderRequest(
            prompt=prompt,  # type: ignore[arg-type]
            profile=selection.profile,
            provider_config=selection.provider_config,
            api_key="secret-test-key",
            trace_id="trace-model-name",
            material_pack_id="AMP-stage19",
            model_analysis_run_id="MAR-stage19",
        )
    )

    assert payload["model"] == selection.profile.model_name
    assert payload["model"] != selection.profile.model_key


def test_real_deepseek_strict_json_output_passes_schema(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    output = _valid_provider_output()
    client = FakeDeepSeekHttpClient(_deepseek_raw_response(json.dumps(output, ensure_ascii=False)))

    result = ModelAnalysisService(
        settings=_real_settings(config_dir),
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=DeepSeekReviewProvider(http_client=client),
    ).run_model_analysis(FakeSession(), request=_real_request())

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert result.review_decision == "wait"
    assert client.calls


def test_real_deepseek_markdown_code_fence_json_is_stripped(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    output = _valid_provider_output()
    fenced_content = "```json\n" + json.dumps(output, ensure_ascii=False) + "\n```"
    client = FakeDeepSeekHttpClient(_deepseek_raw_response(fenced_content))

    result = ModelAnalysisService(
        settings=_real_settings(config_dir),
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=DeepSeekReviewProvider(http_client=client),
    ).run_model_analysis(FakeSession(), request=_real_request())

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert result.review_decision == "wait"


def test_real_deepseek_extra_text_is_schema_invalid_not_guessed(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    output = _valid_provider_output()
    content = "Here is the JSON:\n```json\n" + json.dumps(output, ensure_ascii=False) + "\n```"
    client = FakeDeepSeekHttpClient(_deepseek_raw_response(content))

    result = ModelAnalysisService(
        settings=_real_settings(config_dir),
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=DeepSeekReviewProvider(http_client=client),
    ).run_model_analysis(FakeSession(), request=_real_request())

    assert result.status == ModelAnalysisStatus.BLOCKED
    assert result.error_code == "schema_missing_required_field"
    assert result.details["parsed_json_type"] == "invalid_json"
    assert result.details["provider_parse_error_code"] == "schema_final_content_not_json"


def test_real_deepseek_missing_required_field_returns_safe_cli_diagnostics(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    output = _valid_provider_output()
    del output["review_decision"]
    client = FakeDeepSeekHttpClient(_deepseek_raw_response(json.dumps(output, ensure_ascii=False)))

    result = ModelAnalysisService(
        settings=_real_settings(config_dir),
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=DeepSeekReviewProvider(http_client=client),
    ).run_model_analysis(FakeSession(), request=_real_request())

    cli_output = dict(line.split("=", 1) for line in format_model_analysis_result_lines(result))

    assert result.status == ModelAnalysisStatus.BLOCKED
    assert result.error_code == "schema_missing_required_field"
    assert result.details["schema_error_code"] == "schema_missing_required_field"
    assert "review_decision" in result.details["schema_missing_fields"]
    assert len(result.details["sanitized_content_preview"]) <= 500
    assert cli_output["schema_error_code"] == "schema_missing_required_field"
    assert "review_decision" in cli_output["schema_missing_fields"]
    assert len(cli_output["sanitized_content_preview"]) <= 500
    assert cli_output["parsed_json_type"] == "object"
    assert int(cli_output["final_content_char_count"]) > 0
    assert int(cli_output["final_content_byte_count"]) > 0


def test_real_deepseek_schema_invalid_preserves_usage_without_writing_or_hermes(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    output = _valid_provider_output()
    del output["review_decision"]
    usage = {
        "prompt_tokens": 101,
        "completion_tokens": 7,
        "total_tokens": 108,
        "prompt_cache_hit_tokens": 13,
        "prompt_cache_miss_tokens": 88,
    }
    client = FakeDeepSeekHttpClient(_deepseek_raw_response(json.dumps(output, ensure_ascii=False), usage=usage))
    repo = ArtifactRepository(material_pack=material_pack())
    alert = FakeAlertSender()

    result = ModelAnalysisService(
        settings=_real_settings(config_dir, enabled=True, hermes_enabled=True),
        repository=repo,
        provider=DeepSeekReviewProvider(http_client=client),
        alert_sender=alert,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=False))

    assert result.status == ModelAnalysisStatus.BLOCKED
    assert result.error_code == "schema_missing_required_field"
    assert result.details["input_token_count"] == 101
    assert result.details["output_token_count"] == 7
    assert result.details["total_token_count"] == 108
    assert result.details["provider_usage_json"]["prompt_cache_hit_tokens"] == 13
    assert result.estimated_cost is not None
    assert repo.run_rows == []
    assert repo.result_rows == []
    assert alert.calls == []


def test_real_deepseek_forbidden_and_safety_flags_block(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")

    forbidden = _valid_provider_output()
    forbidden["entry_price"] = "60000"
    forbidden_result = ModelAnalysisService(
        settings=_real_settings(config_dir),
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=DeepSeekReviewProvider(
            http_client=FakeDeepSeekHttpClient(_deepseek_raw_response(json.dumps(forbidden, ensure_ascii=False)))
        ),
    ).run_model_analysis(FakeSession(), request=_real_request())

    not_advice = _valid_provider_output()
    not_advice["not_trading_advice"] = False
    not_advice_result = ModelAnalysisService(
        settings=_real_settings(config_dir),
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=DeepSeekReviewProvider(
            http_client=FakeDeepSeekHttpClient(_deepseek_raw_response(json.dumps(not_advice, ensure_ascii=False)))
        ),
    ).run_model_analysis(FakeSession(), request=_real_request())

    not_boolean = _valid_provider_output()
    not_boolean["human_review_required"] = "false"
    not_boolean_result = ModelAnalysisService(
        settings=_real_settings(config_dir),
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=DeepSeekReviewProvider(
            http_client=FakeDeepSeekHttpClient(_deepseek_raw_response(json.dumps(not_boolean, ensure_ascii=False)))
        ),
    ).run_model_analysis(FakeSession(), request=_real_request())

    safety = _valid_provider_output()
    safety["is_executable"] = True
    safety_result = ModelAnalysisService(
        settings=_real_settings(config_dir),
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=DeepSeekReviewProvider(
            http_client=FakeDeepSeekHttpClient(_deepseek_raw_response(json.dumps(safety, ensure_ascii=False)))
        ),
    ).run_model_analysis(FakeSession(), request=_real_request())

    assert forbidden_result.error_code == "schema_forbidden_trading_field"
    assert "***REDACTED_FORBIDDEN_TRADING_FIELD***" in forbidden_result.details["sanitized_content_preview"]
    assert not_advice_result.error_code == "schema_not_trading_advice_false"
    assert not_boolean_result.error_code == "schema_human_review_required_not_boolean"
    assert safety_result.error_code == "schema_safety_flag_not_false"


def test_deepseek_profile_request_params_and_response_mapping_drive_provider_payload(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    client = FakeDeepSeekHttpClient(_deepseek_raw_response(json.dumps(_valid_provider_output(), ensure_ascii=False)))

    result = ModelAnalysisService(
        settings=_real_settings(config_dir),
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=DeepSeekReviewProvider(http_client=client),
    ).run_model_analysis(FakeSession(), request=_real_request())

    payload = client.calls[0]["payload"]
    assert result.status == ModelAnalysisStatus.SUCCESS
    assert payload["model"] == "deepseek-v4-pro"
    assert payload["model"] != "deepseek_v4_pro_review"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["reasoning_effort"] == "high"
    assert payload["thinking"]["type"] == "enabled"


def test_enabled_deepseek_fake_flow_persists_profile_usage_cost_and_hashes(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    repo = ArtifactRepository(material_pack=material_pack())
    provider = FakeDeepSeekProvider()

    result = ModelAnalysisService(
        settings=_real_settings(config_dir, enabled=True),
        repository=repo,
        provider=provider,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=True))

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert provider.calls == 1
    assert len(repo.run_rows) == 1
    assert len(repo.result_rows) == 1
    run = repo.run_rows[0]
    assert run.model_key == "deepseek_v4_pro_review"
    assert run.profile_version == "profile_v1"
    assert run.profile_hash
    assert run.api_style == "openai_chat_completion"
    assert run.provider_request_id == "ds-test-request"
    assert run.input_token_count == 100
    assert run.output_token_count == 50
    assert run.total_token_count == 150
    assert run.estimated_cost is not None
    assert run.cost_currency == "USD"
    assert run.raw_response_hash
    assert not hasattr(run, "raw_response_text")
    assert run.profile_hash in result.details["profile_hash"]


def test_confirm_write_real_model_creates_running_run_before_provider_call(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    repo = ArtifactRepository(material_pack=material_pack())
    provider = FakeDeepSeekProvider(repo_to_assert_running=repo)

    result = ModelAnalysisService(
        settings=_real_settings(config_dir, enabled=True),
        repository=repo,
        provider=provider,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=True))

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert provider.calls == 1
    assert len(repo.run_rows) == 1
    assert repo.run_rows[0].status == "success"
    assert len(repo.result_rows) == 1


def test_real_provider_exception_updates_running_run_failed_without_result(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    repo = ArtifactRepository(material_pack=material_pack())
    provider = FakeDeepSeekProvider(raise_error=ProviderCallError("fake deepseek failure"))

    result = ModelAnalysisService(
        settings=_real_settings(config_dir, enabled=True),
        repository=repo,
        provider=provider,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=True))

    assert result.status == ModelAnalysisStatus.FAILED
    assert result.error_code == "provider_call_failed"
    assert repo.run_rows[0].status == "failed"
    assert repo.result_rows == []


def test_provider_call_failed_dry_run_does_not_send_hermes(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    repo = ArtifactRepository(material_pack=material_pack())
    provider = FakeDeepSeekProvider(raise_error=ProviderCallError("fake provider failure"))
    alert = FakeAlertSender()

    result = ModelAnalysisService(
        settings=_real_settings(config_dir, enabled=True, hermes_enabled=True),
        repository=repo,
        provider=provider,
        alert_sender=alert,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=False))

    assert result.status == ModelAnalysisStatus.FAILED
    assert result.error_code == "provider_call_failed"
    assert result.hermes_status.value == "skipped_dry_run"
    assert repo.run_rows == []
    assert repo.result_rows == []
    assert alert.calls == []


def test_provider_call_failed_confirm_write_sends_generic_chinese_alert(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    repo = ArtifactRepository(material_pack=material_pack())
    provider = FakeDeepSeekProvider(raise_error=ProviderCallError("fake provider failure"))
    alert = FakeAlertSender()

    result = ModelAnalysisService(
        settings=_real_settings(config_dir, enabled=True, hermes_enabled=True),
        repository=repo,
        provider=provider,
        alert_sender=alert,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=True))

    assert result.status == ModelAnalysisStatus.FAILED
    assert result.error_code == "provider_call_failed"
    assert result.hermes_status.value == "sent"
    assert repo.run_rows[0].status == "failed"
    assert repo.run_rows[0].error_code == "provider_call_failed"
    assert repo.run_rows[0].trace_id == "trace-stage19b"
    assert repo.run_rows[0].model_provider == "deepseek"
    assert repo.run_rows[0].model_key == "deepseek_v4_pro_review"
    assert repo.run_rows[0].model_name == "deepseek-v4-pro"
    assert repo.result_rows == []
    assert len(alert.calls) == 1
    assert alert.calls[0].title == "BTC 大模型请求失败"
    body = build_model_analysis_provider_call_failed_visible_body(result)
    assert "BTC 大模型请求失败" in body
    assert "未生成正式审查结果" in body
    assert "不是最终交易建议" in body
    assert "本阶段未自动交易" in body


def test_raw_response_artifact_write_failure_updates_run_and_alerts(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    repo = ArtifactRepository(material_pack=material_pack())
    provider = FakeDeepSeekProvider(raw_padding=12_000)
    alert = FakeAlertSender()

    result = ModelAnalysisService(
        settings=_real_settings(
            config_dir,
            enabled=True,
            artifact_dir=tmp_path / "artifacts",
            raw_artifact_max_bytes=200,
        ),
        repository=repo,
        provider=provider,
        alert_sender=alert,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=True))

    assert result.status == ModelAnalysisStatus.FAILED
    assert result.error_code == "artifact_write_failed"
    assert repo.run_rows[0].status == "failed"
    assert repo.run_rows[0].error_code == "artifact_write_failed"
    assert repo.run_rows[0].raw_response_hash
    assert repo.run_rows[0].raw_response_char_count > 10_000
    assert repo.result_rows == []
    assert len(alert.calls) == 1
    body = build_model_analysis_artifact_write_failed_visible_body(result)
    assert "BTC 大模型审查 artifact 写入失败" in body
    assert "模型返回未能完整隔离保存" in body
    assert "不是最终交易建议" in body
    assert "本阶段未自动交易" in body


def test_raw_response_artifact_write_failure_dry_run_has_no_hermes_side_effect(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    repo = ArtifactRepository(material_pack=material_pack())
    provider = FakeDeepSeekProvider(raw_padding=12_000)
    alert = FakeAlertSender()

    result = ModelAnalysisService(
        settings=_real_settings(
            config_dir,
            enabled=True,
            artifact_dir=tmp_path / "artifacts",
            raw_artifact_max_bytes=200,
        ),
        repository=repo,
        provider=provider,
        alert_sender=alert,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=False))

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert result.hermes_status.value == "skipped_dry_run"
    assert repo.run_rows == []
    assert repo.result_rows == []
    assert repo.artifact_rows == []
    assert alert.calls == []


def test_oversized_raw_response_without_safe_schema_blocks_without_result(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    repo = ArtifactRepository(material_pack=material_pack())
    provider = FakeDeepSeekProvider(output={"review_decision": "wait"}, raw_padding=12_000)
    alert = FakeAlertSender()

    result = ModelAnalysisService(
        settings=_real_settings(config_dir, enabled=True, artifact_dir=tmp_path / "artifacts"),
        repository=repo,
        provider=provider,
        alert_sender=alert,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=True))

    assert result.status == ModelAnalysisStatus.BLOCKED
    assert result.error_code == "model_output_too_large"
    assert repo.run_rows[0].status == "blocked"
    assert repo.run_rows[0].error_code == "model_output_too_large"
    assert repo.run_rows[0].raw_response_hash
    assert repo.run_rows[0].raw_response_char_count > 10_000
    assert repo.result_rows == []
    assert not hasattr(repo.run_rows[0], "raw_response_text")
    assert len(alert.calls) == 1


def test_capture_raw_request_writes_secret_free_artifact(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    artifact_dir = tmp_path / "artifacts"
    repo = ArtifactRepository(material_pack=material_pack())

    result = ModelAnalysisService(
        settings=_real_settings(config_dir, enabled=True, artifact_dir=artifact_dir),
        repository=repo,
        provider=FakeDeepSeekProvider(),
    ).run_model_analysis(
        FakeSession(),
        request=_real_request(confirm=True, capture_raw_request=True),
    )

    assert result.status == ModelAnalysisStatus.SUCCESS
    request_artifacts = [row for row in repo.artifact_rows if row.artifact_type == "raw_request"]
    assert len(request_artifacts) == 1
    assert repo.run_rows[0].raw_request_hash == request_artifacts[0].sha256_hash
    assert repo.run_rows[0].raw_request_storage_ref == request_artifacts[0].storage_ref
    artifact_text = (Path.cwd() / request_artifacts[0].storage_ref).read_text(encoding="utf-8")
    assert "test-deepseek-key" not in artifact_text
    assert "Authorization" not in artifact_text


def test_review_version_key_includes_profile_hash_and_model_key(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(
        tmp_path / "config",
        extra_model_key="deepseek_other_review",
        extra_model_version="v4_other",
    )
    provider = FakeDeepSeekProvider()
    settings = _real_settings(config_dir)

    first = ModelAnalysisService(
        settings=settings,
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=provider,
    ).run_model_analysis(FakeSession(), request=_real_request())
    second = ModelAnalysisService(
        settings=settings,
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=provider,
    ).run_model_analysis(
        FakeSession(),
        request=ModelAnalysisRequest(
            material_pack_id="AMP-stage19",
            trigger_source="cli",
            use_real_model=True,
            model_key="deepseek_other_review",
            confirm_real_model_cost=True,
        ),
    )

    assert first.status == ModelAnalysisStatus.SUCCESS
    assert second.status == ModelAnalysisStatus.SUCCESS
    assert first.review_version_key != second.review_version_key


def test_oversized_raw_response_is_artifacted_and_warned_without_main_raw_text(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    repo = ArtifactRepository(material_pack=material_pack())
    provider = FakeDeepSeekProvider(raw_padding=12_000)
    alert = FakeAlertSender()

    result = ModelAnalysisService(
        settings=_real_settings(config_dir, enabled=True, artifact_dir=tmp_path / "artifacts"),
        repository=repo,
        provider=provider,
        alert_sender=alert,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=True))

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert repo.artifact_rows
    assert repo.artifact_rows[0].artifact_type == "oversized_response"
    assert repo.run_rows[0].raw_response_storage_ref
    assert repo.run_rows[0].raw_response_char_count > 10_000
    assert len(alert.calls) == 1
    body = build_model_analysis_oversized_response_visible_body(result)
    assert "BTC 大模型审查返回过长" in body
    assert "不是最终交易建议" in body
    assert "本阶段未自动交易" in body


def test_oversized_raw_response_dry_run_has_no_artifact_or_hermes_side_effect(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    repo = ArtifactRepository(material_pack=material_pack())
    provider = FakeDeepSeekProvider(raw_padding=12_000)
    alert = FakeAlertSender()

    result = ModelAnalysisService(
        settings=_real_settings(config_dir, enabled=True, artifact_dir=tmp_path / "artifacts"),
        repository=repo,
        provider=provider,
        alert_sender=alert,
    ).run_model_analysis(FakeSession(), request=_real_request(confirm=False))

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert result.hermes_status.value == "skipped_dry_run"
    assert not result.details["raw_response_storage_ref"]
    assert not repo.run_rows
    assert not repo.result_rows
    assert not repo.artifact_rows
    assert alert.calls == []


def test_real_provider_schema_forbidden_trading_fields_are_blocked(tmp_path: Path) -> None:
    config_dir = _write_deepseek_config(tmp_path / "config")
    output = _valid_provider_output()
    output["entry_price"] = "forbidden"
    provider = FakeDeepSeekProvider(output=output)

    result = ModelAnalysisService(
        settings=_real_settings(config_dir),
        repository=ArtifactRepository(material_pack=material_pack()),
        provider=provider,
    ).run_model_analysis(FakeSession(), request=_real_request())

    assert result.status == ModelAnalysisStatus.BLOCKED
    assert result.error_code == "schema_forbidden_trading_field"
    assert provider.calls == 1


def _real_request(*, confirm: bool = False, capture_raw_request: bool = False) -> ModelAnalysisRequest:
    return ModelAnalysisRequest(
        material_pack_id="AMP-stage19",
        trigger_source="cli",
        dry_run=not confirm,
        confirm_write=confirm,
        trace_id="trace-stage19b",
        use_real_model=True,
        model_key="deepseek_v4_pro_review",
        confirm_real_model_cost=True,
        capture_raw_request=capture_raw_request,
    )


def _real_settings(
    config_dir: Path,
    *,
    enabled: bool = False,
    artifact_dir: Path | None = None,
    raw_artifact_max_bytes: int = 1048576,
    hermes_enabled: bool = False,
) -> AppSettings:
    return AppSettings(
        model_review_config_dir=str(config_dir),
        model_review_real_model_enabled=True,
        model_review_enabled=enabled,
        model_review_hermes_enabled=hermes_enabled,
        deepseek_api_key="test-deepseek-key",
        model_review_artifact_dir=str(artifact_dir or (config_dir / "artifacts")),
        model_review_raw_artifact_max_bytes=raw_artifact_max_bytes,
    )


def _deepseek_raw_response(content: str, *, usage: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return {
        "id": "ds-http-test-request",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": content,
                    "reasoning_content": "compact reasoning metadata only",
                },
            }
        ],
        "usage": dict(
            usage
            or {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            }
        ),
    }


def _write_deepseek_config(
    base_dir: Path,
    *,
    provider_enabled: bool = True,
    profile_enabled: bool = True,
    registry_models: list[str] | None = None,
    include_flash: bool = False,
    extra_model_key: str | None = None,
    extra_model_version: str = "v4_other",
    extra_model_name: str | None = None,
    include_supported_model_names: bool = True,
    provider_supported_model_names: list[str] | None = None,
) -> Path:
    (base_dir / "providers").mkdir(parents=True, exist_ok=True)
    (base_dir / "profiles" / "deepseek").mkdir(parents=True, exist_ok=True)
    models = registry_models or ["deepseek_v4_pro_review"]
    if include_flash:
        models.append("deepseek_v4_flash_review")
    if extra_model_key:
        models.append(extra_model_key)
    (base_dir / "model_registry.yaml").write_text(
        "\n".join(["enabled_models:", *(f"  - {model}" for model in models), "", "default_mode: single"]),
        encoding="utf-8",
    )
    provider_lines = [
        "provider: deepseek",
        f"enabled: {str(provider_enabled).lower()}",
        "provider_version: deepseek_openai_compatible_v1",
        'docs_checked_at: "2026-05-20T00:00:00Z"',
        "docs_source:",
        "  - DeepSeek official API documentation for stage 19B tests.",
    ]
    if include_supported_model_names:
        supported_model_names = (
            ["deepseek-v4-pro", "deepseek-v4-flash"]
            if provider_supported_model_names is None
            else list(provider_supported_model_names)
        )
        provider_lines.append("supported_model_names:")
        provider_lines.extend(f"  - {model_name}" for model_name in supported_model_names)
    provider_lines.extend(
        [
            "api_base_url: https://api.deepseek.com",
            "api_key_env: DEEPSEEK_API_KEY",
            "timeout_seconds: 60",
            "max_retries: 0",
            "retry_backoff_seconds: 0",
        ]
    )
    (base_dir / "providers" / "deepseek.yaml").write_text("\n".join(provider_lines), encoding="utf-8")
    _write_profile(base_dir, "deepseek_v4_pro_review", enabled=profile_enabled, model_version="v4_pro")
    if include_flash:
        _write_profile(base_dir, "deepseek_v4_flash_review", enabled=False, model_version="v4_flash")
    if extra_model_key:
        _write_profile(
            base_dir,
            extra_model_key,
            enabled=True,
            model_version=extra_model_version,
            model_name=extra_model_name,
        )
    return base_dir


def _write_future_provider_config(base_dir: Path) -> Path:
    (base_dir / "providers").mkdir(parents=True, exist_ok=True)
    (base_dir / "profiles" / "future_provider").mkdir(parents=True, exist_ok=True)
    (base_dir / "model_registry.yaml").write_text(
        "\n".join(["enabled_models:", "  - future_thinking_review", "", "default_mode: single"]),
        encoding="utf-8",
    )
    (base_dir / "providers" / "future_provider.yaml").write_text(
        "\n".join(
            [
                "provider: future_provider",
                "enabled: true",
                "provider_version: future_provider_profile_v1",
                'docs_checked_at: "2026-05-20T00:00:00Z"',
                "docs_source:",
                "  - Future provider official docs fixture.",
                "api_base_url: https://future-provider.example",
                "api_key_env: FUTURE_PROVIDER_API_KEY",
                "timeout_seconds: 60",
                "max_retries: 0",
                "retry_backoff_seconds: 0",
            ]
        ),
        encoding="utf-8",
    )
    (base_dir / "profiles" / "future_provider" / "future_thinking_review.yaml").write_text(
        "\n".join(
            [
                "model_key: future_thinking_review",
                "provider: future_provider",
                "enabled: true",
                "api_style: future_chat_completion",
                "model_name: future-thinking-real-model",
                "model_version: future_v1",
                "profile_version: profile_v1",
                'docs_checked_at: "2026-05-20T00:00:00Z"',
                "docs_source:",
                "  - Future provider official docs fixture.",
                "model_role: future_review",
                "analysis_mode: single",
                "prompt_template_version: review_gate_v1",
                "review_schema_version: review_schema_v1",
                "",
                "capabilities:",
                "  json_output: true",
                "  thinking: true",
                "",
                "request_params:",
                "  max_output_tokens: 1024",
                "",
                "response_mapping:",
                "  final_content_path: result.content",
                "  usage_path: usage",
                "  finish_reason_path: result.finish_reason",
                "",
                "unsupported_params:",
                "  - tools",
                "",
                "cost_policy:",
                "  track_token_usage: true",
                "  require_cost_confirmation: true",
                "  currency: USD",
            ]
        ),
        encoding="utf-8",
    )
    return base_dir


def _write_profile(
    base_dir: Path,
    model_key: str,
    *,
    enabled: bool,
    model_version: str,
    model_name: str | None = None,
) -> None:
    resolved_model_name = model_name or ("deepseek-v4-flash" if "flash" in model_key else "deepseek-v4-pro")
    if model_key == "deepseek_other_review" and model_name is None:
        resolved_model_name = "deepseek-v4-pro"
    (base_dir / "profiles" / "deepseek" / f"{model_key}.yaml").write_text(
        "\n".join(
            [
                f"model_key: {model_key}",
                "provider: deepseek",
                f"enabled: {str(enabled).lower()}",
                "api_style: openai_chat_completion",
                f"model_name: {resolved_model_name}",
                f"model_version: {model_version}",
                "profile_version: profile_v1",
                'docs_checked_at: "2026-05-20T00:00:00Z"',
                "docs_source:",
                "  - DeepSeek official API documentation for stage 19B tests.",
                "model_role: mathematical_structure_review",
                "analysis_mode: single",
                "prompt_template_version: review_gate_v1",
                "review_schema_version: review_schema_v1",
                "",
                "capabilities:",
                "  json_output: true",
                "  reasoning_content: true",
                "  thinking: true",
                "  function_calling: false",
                "  streaming: false",
                "",
                "request_params:",
                "  max_tokens: 4096",
                "  response_format:",
                "    type: json_object",
                "  reasoning_effort: high",
                "  extra_body:",
                "    thinking:",
                "      type: enabled",
                "",
                "ignored_params_in_thinking_mode:",
                "  - temperature",
                "  - top_p",
                "  - presence_penalty",
                "  - frequency_penalty",
                "",
                "response_mapping:",
                "  final_content_path: choices.0.message.content",
                "  reasoning_content_path: choices.0.message.reasoning_content",
                "  usage_path: usage",
                "  finish_reason_path: choices.0.finish_reason",
                "  provider_request_id_path: id",
                "",
                "unsupported_params:",
                "  - tools",
                "  - function_call",
                "",
                "cost_policy:",
                "  track_token_usage: true",
                "  require_cost_confirmation: true",
                "  currency: USD",
                "  input_token_price: 2.0",
                "  output_token_price: 8.0",
            ]
        ),
        encoding="utf-8",
    )
