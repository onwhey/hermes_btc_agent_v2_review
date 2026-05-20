from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from app.core.config import AppSettings
from app.model_analysis.hermes_formatter import build_model_analysis_oversized_response_visible_body
from app.model_analysis.model_registry import resolve_model_review_profile
from app.model_analysis.providers.base import ProviderRequest, ProviderResponse
from app.model_analysis.service import ModelAnalysisService
from app.model_analysis.types import ModelAnalysisRequest, ModelAnalysisStatus
from tests.model_analysis.test_model_analysis_service import (
    FakeAlertSender,
    FakeModelAnalysisRepository,
    FakeSession,
    _valid_provider_output,
    material_pack,
)


class ArtifactRepository(FakeModelAnalysisRepository):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.artifact_rows: list[Any] = []

    def create_model_provider_call_artifact(self, _db_session: Any, *, payload: Any) -> Any:
        row = SimpleNamespace(**payload.__dict__)
        row.id = len(self.artifact_rows) + 1
        self.artifact_rows.append(row)
        return row


class FakeDeepSeekProvider:
    provider_name = "deepseek"

    def __init__(self, *, output: Mapping[str, Any] | None = None, raw_padding: int = 0) -> None:
        self.output = dict(output or _valid_provider_output())
        self.raw_padding = raw_padding
        self.calls = 0
        self.last_request: ProviderRequest | None = None

    def call_review_model(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        self.last_request = request
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
    assert pro.profile.profile_hash
    assert flash.profile.enabled is False
    assert flash.profile.model_name == "deepseek-v4-flash"


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


def test_oversized_raw_response_dry_run_keeps_artifact_ref_without_database_write(tmp_path: Path) -> None:
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
    assert result.details["raw_response_storage_ref"]
    assert not repo.run_rows
    assert not repo.result_rows
    assert not repo.artifact_rows
    assert len(alert.calls) == 1


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


def _real_request(*, confirm: bool = False) -> ModelAnalysisRequest:
    return ModelAnalysisRequest(
        material_pack_id="AMP-stage19",
        trigger_source="cli",
        dry_run=not confirm,
        confirm_write=confirm,
        trace_id="trace-stage19b",
        use_real_model=True,
        model_key="deepseek_v4_pro_review",
        confirm_real_model_cost=True,
    )


def _real_settings(
    config_dir: Path,
    *,
    enabled: bool = False,
    artifact_dir: Path | None = None,
) -> AppSettings:
    return AppSettings(
        model_review_config_dir=str(config_dir),
        model_review_real_model_enabled=True,
        model_review_enabled=enabled,
        deepseek_api_key="test-deepseek-key",
        model_review_artifact_dir=str(artifact_dir or (config_dir / "artifacts")),
    )


def _write_deepseek_config(
    base_dir: Path,
    *,
    provider_enabled: bool = True,
    profile_enabled: bool = True,
    registry_models: list[str] | None = None,
    include_flash: bool = False,
    extra_model_key: str | None = None,
    extra_model_version: str = "v4_other",
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
    (base_dir / "providers" / "deepseek.yaml").write_text(
        "\n".join(
            [
                "provider: deepseek",
                f"enabled: {str(provider_enabled).lower()}",
                "api_base_url: https://api.deepseek.com",
                "api_key_env: DEEPSEEK_API_KEY",
                "timeout_seconds: 60",
                "max_retries: 0",
                "retry_backoff_seconds: 0",
            ]
        ),
        encoding="utf-8",
    )
    _write_profile(base_dir, "deepseek_v4_pro_review", enabled=profile_enabled, model_version="v4_pro")
    if include_flash:
        _write_profile(base_dir, "deepseek_v4_flash_review", enabled=False, model_version="v4_flash")
    if extra_model_key:
        _write_profile(base_dir, extra_model_key, enabled=True, model_version=extra_model_version)
    return base_dir


def _write_profile(base_dir: Path, model_key: str, *, enabled: bool, model_version: str) -> None:
    model_name = "deepseek-v4-flash" if "flash" in model_key else "deepseek-v4-pro"
    if model_key == "deepseek_other_review":
        model_name = "deepseek-v4-other"
    (base_dir / "profiles" / "deepseek" / f"{model_key}.yaml").write_text(
        "\n".join(
            [
                f"model_key: {model_key}",
                "provider: deepseek",
                f"enabled: {str(enabled).lower()}",
                "api_style: openai_chat_completion",
                f"model_name: {model_name}",
                f"model_version: {model_version}",
                "profile_version: profile_v1",
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
                "  temperature: 0.2",
                "  top_p: 1",
                "  max_tokens: 4096",
                "  response_format:",
                "    type: json_object",
                "  reasoning_effort: high",
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
