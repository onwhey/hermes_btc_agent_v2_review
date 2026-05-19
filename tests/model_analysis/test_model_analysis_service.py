from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from app.alerting.types import AlertSendResult, AlertSendStatus
from app.core.config import AppSettings
from app.model_analysis.hermes_formatter import build_model_analysis_visible_body
from app.model_analysis.prompt_builder import build_model_review_prompt
from app.model_analysis.providers.mock import MockModelReviewProvider, build_custom_mock_provider
from app.model_analysis.service import ModelAnalysisService
from app.model_analysis.types import (
    EXIT_PARAMETER_ERROR,
    ModelAnalysisRequest,
    ModelAnalysisServiceResult,
    ModelAnalysisStatus,
    ReviewDecision,
)
from scripts import run_model_analysis as model_analysis_cli


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeModelAnalysisRepository:
    def __init__(
        self,
        *,
        material_pack: Any | None = None,
        existing_result: Any | None = None,
        existing_after_unique_conflict: Any | None = None,
        create_result_error: Exception | None = None,
    ) -> None:
        self.material_pack = material_pack
        self.existing_result = existing_result
        self.existing_after_unique_conflict = existing_after_unique_conflict
        self.create_result_error = create_result_error
        self.unique_conflict_raised = False
        self.run_rows: list[Any] = []
        self.result_rows: list[Any] = []

    def get_material_pack_by_id(self, _db_session: Any, *, material_pack_id: str) -> Any | None:
        if self.material_pack and self.material_pack.material_pack_id == material_pack_id:
            return self.material_pack
        return None

    def get_existing_result_by_review_version_key(self, _db_session: Any, *, review_version_key: str) -> Any | None:
        candidate = self.existing_result
        if candidate is None and self.unique_conflict_raised:
            candidate = self.existing_after_unique_conflict
        candidate_key = getattr(candidate, "review_version_key", review_version_key) if candidate is not None else ""
        if candidate is not None and (not candidate_key or candidate_key == review_version_key):
            return candidate
        return None

    def create_model_analysis_run(self, _db_session: Any, *, payload: Any) -> Any:
        row_data = dict(payload.__dict__)
        row_data["status"] = payload.status.value
        row = SimpleNamespace(**row_data)
        row.id = len(self.run_rows) + 1
        self.run_rows.append(row)
        return row

    def create_model_analysis_result(self, _db_session: Any, *, payload: Any) -> Any:
        if self.create_result_error is not None:
            self.unique_conflict_raised = True
            raise self.create_result_error
        row = SimpleNamespace(**payload.__dict__)
        row.id = len(self.result_rows) + 1
        self.result_rows.append(row)
        return row

    def record_hermes_result(self, _db_session: Any, run_row: Any, **kwargs: Any) -> Any:
        for key, value in kwargs.items():
            setattr(run_row, key, value)
        return run_row


class FakeAlertSender:
    def __init__(self, *, raise_error: bool = False) -> None:
        self.raise_error = raise_error
        self.calls: list[Any] = []

    def __call__(self, event: Any, **_kwargs: Any) -> AlertSendResult:
        if self.raise_error:
            raise RuntimeError("Hermes unavailable")
        self.calls.append(event)
        return AlertSendResult(
            status=AlertSendStatus.SUBMITTED_TO_HERMES,
            message="submitted",
            submitted_at_utc=datetime(2026, 5, 19, tzinfo=timezone.utc),
            attempted_real_send=True,
        )


def material_pack(
    *,
    status: str = "success",
    strategies: list[Mapping[str, Any]] | None = None,
    material_pack_id: str = "AMP-stage19",
) -> Any:
    strategy_items = strategies if strategies is not None else [
        {
            "strategy_name": "fixture_alpha_review_source",
            "strategy_version": "v1",
            "strategy_role": "analysis_input",
            "enabled": True,
            "status": "success",
            "analysis_hypothesis_direction": "wait",
            "evidence_quality": "moderate",
            "risk_level": "medium",
            "summary": "compact strategy summary for review gate",
            "reason_codes": ["fixture_alpha_reason"],
            "missing_evidence": [],
        }
    ]
    return SimpleNamespace(
        material_pack_id=material_pack_id,
        aggregation_run_id="SAR-stage19",
        strategy_signal_run_id="SSR-stage19",
        snapshot_id="MCS-stage19",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        aggregation_version="aggregation_v1",
        material_schema_version="material_schema_v1",
        status=status,
        material_json=json.dumps({"strategy_summaries": strategy_items}, ensure_ascii=False),
        summary_json=json.dumps({"summary": "stage 18 compact material", "strategy_summaries": strategy_items}, ensure_ascii=False),
        question_json=json.dumps({"questions": ["材料证据是否足够？"]}, ensure_ascii=False),
        validation_plan_json=json.dumps({"focus": ["证据完整性", "冲突解释"]}, ensure_ascii=False),
    )


def run_request(*, confirm: bool = False) -> ModelAnalysisRequest:
    return ModelAnalysisRequest(
        material_pack_id="AMP-stage19",
        trigger_source="cli",
        dry_run=not confirm,
        confirm_write=confirm,
        trace_id="trace-stage19",
    )


def service_with_repo(
    repo: FakeModelAnalysisRepository,
    *,
    settings: AppSettings | None = None,
    provider: Any | None = None,
    alert: FakeAlertSender | None = None,
) -> ModelAnalysisService:
    return ModelAnalysisService(
        settings=settings or AppSettings(),
        repository=repo,
        provider=provider,
        alert_sender=alert or FakeAlertSender(),
    )


def test_dry_run_does_not_write_and_is_allowed_when_disabled() -> None:
    repo = FakeModelAnalysisRepository(material_pack=material_pack())

    result = service_with_repo(repo, settings=AppSettings(model_review_enabled=False)).run_model_analysis(
        FakeSession(),
        request=run_request(),
    )

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert result.details["mock_provider_only"] is True
    assert result.details["no_real_model_call"] is True
    assert repo.run_rows == []
    assert repo.result_rows == []


def test_confirm_write_is_blocked_when_model_review_disabled() -> None:
    repo = FakeModelAnalysisRepository(material_pack=material_pack())

    result = service_with_repo(repo, settings=AppSettings(model_review_enabled=False)).run_model_analysis(
        FakeSession(),
        request=run_request(confirm=True),
    )

    assert result.status == ModelAnalysisStatus.BLOCKED
    assert result.error_code == "model_review_disabled"
    assert repo.run_rows == []
    assert repo.result_rows == []


def test_confirm_write_persists_run_and_result_only_with_enabled_config() -> None:
    session = FakeSession()
    repo = FakeModelAnalysisRepository(material_pack=material_pack())

    result = service_with_repo(repo, settings=AppSettings(model_review_enabled=True)).run_model_analysis(
        session,
        request=run_request(confirm=True),
    )

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert session.commits >= 1
    assert len(repo.run_rows) == 1
    assert len(repo.result_rows) == 1
    assert repo.run_rows[0].status == "success"
    assert repo.run_rows[0].is_final_trading_advice is False
    assert repo.run_rows[0].is_trading_signal is False
    assert repo.run_rows[0].is_executable is False
    assert repo.run_rows[0].auto_trading_allowed is False
    assert repo.result_rows[0].review_version_key == result.review_version_key


def test_missing_or_non_success_material_pack_is_blocked() -> None:
    missing = service_with_repo(FakeModelAnalysisRepository()).run_model_analysis(FakeSession(), request=run_request())
    blocked_pack = service_with_repo(
        FakeModelAnalysisRepository(material_pack=material_pack(status="partial_success"))
    ).run_model_analysis(FakeSession(), request=run_request())

    assert missing.status == ModelAnalysisStatus.BLOCKED
    assert missing.error_code == "material_pack_not_found"
    assert blocked_pack.status == ModelAnalysisStatus.BLOCKED
    assert blocked_pack.error_code == "material_pack_status_not_success"


def test_input_char_and_byte_limits_block_review() -> None:
    repo = FakeModelAnalysisRepository(material_pack=material_pack())

    char_result = service_with_repo(
        repo,
        settings=AppSettings(model_review_max_input_chars=50, model_review_max_input_bytes=32768),
    ).run_model_analysis(FakeSession(), request=run_request())
    byte_result = service_with_repo(
        repo,
        settings=AppSettings(model_review_max_input_chars=10000, model_review_max_input_bytes=50),
    ).run_model_analysis(FakeSession(), request=run_request())

    assert char_result.status == ModelAnalysisStatus.BLOCKED
    assert char_result.error_code == "input_char_limit_exceeded"
    assert byte_result.status == ModelAnalysisStatus.BLOCKED
    assert byte_result.error_code == "input_byte_limit_exceeded"


def test_output_char_and_byte_limits_block_review() -> None:
    large_output = _valid_provider_output(summary_text="x" * 11_000)
    provider = MockModelReviewProvider(override_response=large_output)
    repo = FakeModelAnalysisRepository(material_pack=material_pack())

    char_result = service_with_repo(
        repo,
        provider=provider,
        settings=AppSettings(model_review_max_output_chars=1000, model_review_max_output_bytes=32768),
    ).run_model_analysis(FakeSession(), request=run_request())
    byte_result = service_with_repo(
        repo,
        provider=provider,
        settings=AppSettings(model_review_max_output_chars=20000, model_review_max_output_bytes=1000),
    ).run_model_analysis(FakeSession(), request=run_request())

    assert char_result.status == ModelAnalysisStatus.BLOCKED
    assert char_result.error_code == "output_char_limit_exceeded"
    assert byte_result.status == ModelAnalysisStatus.BLOCKED
    assert byte_result.error_code == "output_byte_limit_exceeded"


def test_invalid_schema_and_forbidden_trading_fields_are_blocked() -> None:
    invalid_schema_result = service_with_repo(
        FakeModelAnalysisRepository(material_pack=material_pack()),
        provider=MockModelReviewProvider(override_response={"review_decision": "wait"}),
    ).run_model_analysis(FakeSession(), request=run_request())

    assert invalid_schema_result.status == ModelAnalysisStatus.BLOCKED
    assert invalid_schema_result.error_code == "schema_missing_required_field"

    for field_name in ("entry_price", "stop_loss", "take_profit", "leverage", "position_size"):
        forbidden = _valid_provider_output()
        forbidden[field_name] = "forbidden"
        result = service_with_repo(
            FakeModelAnalysisRepository(material_pack=material_pack()),
            provider=MockModelReviewProvider(override_response=forbidden),
        ).run_model_analysis(FakeSession(), request=run_request())

        assert result.status == ModelAnalysisStatus.BLOCKED
        assert result.error_code == "schema_forbidden_trading_field"


def test_existing_success_result_skips_and_blocked_failed_attempts_do_not_lock_rerun() -> None:
    existing = SimpleNamespace(
        model_analysis_result_id="MARES-existing",
        review_version_key="",
        material_pack_id="AMP-stage19",
        aggregation_run_id="SAR-stage19",
        strategy_signal_run_id="SSR-stage19",
        review_decision="wait",
        evidence_quality="moderate",
        risk_acceptability="caution",
    )
    existing_repo = FakeModelAnalysisRepository(material_pack=material_pack(), existing_result=existing)
    skipped = service_with_repo(
        existing_repo,
        settings=AppSettings(model_review_enabled=True),
    ).run_model_analysis(FakeSession(), request=run_request(confirm=True))

    assert skipped.status == ModelAnalysisStatus.SKIPPED
    assert skipped.details["skip_reason"] == "already_exists"

    for previous_status in ("blocked", "failed"):
        repo = FakeModelAnalysisRepository(material_pack=material_pack())
        repo.run_rows.append(SimpleNamespace(status=previous_status, review_version_key="same-key"))
        rerun = service_with_repo(repo, settings=AppSettings(model_review_enabled=True)).run_model_analysis(
            FakeSession(),
            request=run_request(confirm=True),
        )

        assert rerun.status == ModelAnalysisStatus.SUCCESS
        assert len(repo.result_rows) == 1


def test_unique_conflict_is_recovered_as_skipped() -> None:
    existing_final = SimpleNamespace(
        model_analysis_result_id="MARES-final",
        review_version_key="",
        material_pack_id="AMP-stage19",
        aggregation_run_id="SAR-stage19",
        strategy_signal_run_id="SSR-stage19",
        review_decision="wait",
        evidence_quality="moderate",
        risk_acceptability="caution",
    )
    repo = FakeModelAnalysisRepository(
        material_pack=material_pack(),
        existing_after_unique_conflict=existing_final,
        create_result_error=RuntimeError("UNIQUE constraint failed: uk_model_analysis_result_review_version_key"),
    )
    session = FakeSession()

    result = service_with_repo(repo, settings=AppSettings(model_review_enabled=True)).run_model_analysis(
        session,
        request=run_request(confirm=True),
    )

    assert result.status == ModelAnalysisStatus.SKIPPED
    assert result.details["skip_reason"] == "already_exists"
    assert result.details["unique_conflict_recovered"] is True
    assert session.rollbacks == 1


def test_human_review_required_is_success_not_blocked() -> None:
    provider = MockModelReviewProvider(
        override_response=_valid_provider_output(review_decision=ReviewDecision.HUMAN_REVIEW_REQUIRED.value)
    )
    repo = FakeModelAnalysisRepository(material_pack=material_pack())

    result = service_with_repo(
        repo,
        provider=provider,
        settings=AppSettings(model_review_enabled=True),
    ).run_model_analysis(FakeSession(), request=run_request(confirm=True))

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert result.review_decision == "human_review_required"
    assert result.human_review_required is True
    assert len(repo.result_rows) == 1


def test_hermes_visible_body_is_chinese_and_hermes_failure_does_not_fail_review() -> None:
    base_result = ModelAnalysisServiceResult(
        status=ModelAnalysisStatus.SUCCESS,
        exit_code=0,
        model_analysis_run_id="MAR-test",
        model_analysis_result_id="MARES-test",
        review_version_key="rvk",
        material_pack_id="AMP-stage19",
        aggregation_run_id="SAR-stage19",
        strategy_signal_run_id="SSR-stage19",
        trace_id="trace-stage19",
        review_decision="wait",
        evidence_quality="moderate",
        risk_acceptability="caution",
        human_review_required=False,
    )
    body = build_model_analysis_visible_body(base_result)
    assert "不是最终交易建议" in body
    assert "本阶段未自动交易" in body
    assert "本阶段未生成订单" in body
    assert "本阶段未给出仓位或杠杆" in body

    repo = FakeModelAnalysisRepository(material_pack=material_pack())
    result = service_with_repo(
        repo,
        settings=AppSettings(model_review_enabled=True, model_review_hermes_enabled=True),
        alert=FakeAlertSender(raise_error=True),
    ).run_model_analysis(FakeSession(), request=run_request(confirm=True))

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert result.hermes_status.value == "failed"
    assert repo.run_rows[0].hermes_status == "failed"


def test_prompt_builder_compresses_dynamic_strategy_list_without_name_assumptions() -> None:
    strategies = [
        {
            "strategy_name": f"fixture_dynamic_{index}",
            "strategy_version": "v1",
            "strategy_role": "analysis_input",
            "enabled": True,
            "status": "success",
            "analysis_hypothesis_direction": "wait",
            "evidence_quality": "moderate",
            "risk_level": "medium",
            "summary": "summary",
            "reason_codes": [f"reason_{item}" for item in range(10)],
            "missing_evidence": [],
        }
        for index in range(50)
    ]
    prompt = build_model_review_prompt(
        material_pack(strategies=strategies),
        settings=AppSettings(model_review_max_strategy_items=30, model_review_max_reason_items_per_strategy=5),
    )

    assert prompt.strategy_item_count == 30
    assert prompt.truncated_strategy_count == 20
    assert len(prompt.input_summary["strategy_summaries"][0]["reason_codes"]) == 5
    assert prompt.input_char_count <= 10000
    assert prompt.input_byte_count <= 32768


def test_model_analysis_schema_uses_attempt_key_and_single_result_unique_key() -> None:
    from sqlalchemy import UniqueConstraint

    from app.storage.mysql.models.model_analysis import ModelAnalysisResult, ModelAnalysisRun

    run_table = ModelAnalysisRun.__table__
    run_unique_names = {
        constraint.name
        for constraint in run_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_model_analysis_run_id" in run_unique_names
    assert "uk_model_analysis_run_review_version_key" not in run_unique_names

    result_table = ModelAnalysisResult.__table__
    result_unique_names = {
        constraint.name
        for constraint in result_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_model_analysis_result_id" in result_unique_names
    assert "uk_model_analysis_result_review_version_key" in result_unique_names

    run_index_names = {index.name for index in run_table.indexes}
    assert "idx_model_analysis_run_review_version_key" in run_index_names
    assert "idx_model_analysis_version_status" not in run_index_names
    for index in run_table.indexes:
        assert len(index.columns) <= 2
        assert index.unique is False


def test_stage19_migration_does_not_create_large_composite_varchar_indexes() -> None:
    migration = Path("migrations/versions/20260520_19_create_model_analysis_tables.py")
    source = migration.read_text(encoding="utf-8")

    assert "uk_model_analysis_run_review_version_key" not in source
    assert "uk_model_analysis_result_review_version_key" in source
    assert "idx_model_analysis_version_status" not in source
    assert "review_version_key\", \"material_pack_id" not in source
    assert "strategy_signal_run_id\", \"review_version_key" not in source


def test_model_analysis_source_does_not_call_real_model_strategy_or_trading_interfaces() -> None:
    import app.model_analysis.prompt_builder as prompt_module
    import app.model_analysis.providers.mock as mock_module
    import app.model_analysis.schema_validator as schema_module
    import app.model_analysis.service as service_module
    import scripts.run_model_analysis as script_module

    source = "\n".join(
        inspect.getsource(module)
        for module in (prompt_module, mock_module, schema_module, service_module, script_module)
    )

    assert "from app.exchange" not in source
    assert "BinanceRestClient" not in source
    assert "MarketKline4h" not in source
    assert "MarketKline1d" not in source
    assert "DeepSeekClient" not in source
    assert "GannStrategy" not in source
    assert "TrendStrategy" not in source
    assert "RiskControlStrategy" not in source
    for strategy_name in ("gann", "trend", "risk_control"):
        assert strategy_name not in source.lower()


def test_cli_dry_run_confirm_write_and_real_model_rejection(monkeypatch: Any, capsys: Any) -> None:
    fake_session = object()
    captured: list[ModelAnalysisRequest] = []

    @contextmanager
    def fake_session_scope(**_kwargs: Any) -> Any:
        yield fake_session

    def fake_run_model_analysis(*, db_session: Any, request: ModelAnalysisRequest) -> ModelAnalysisServiceResult:
        assert db_session is fake_session
        captured.append(request)
        return ModelAnalysisServiceResult(
            status=ModelAnalysisStatus.SUCCESS,
            exit_code=0,
            model_analysis_run_id="MAR-cli",
            model_analysis_result_id="MARES-cli" if request.confirm_write else None,
            review_version_key="rvk-cli",
            material_pack_id=request.material_pack_id,
            aggregation_run_id="SAR-cli",
            strategy_signal_run_id="SSR-cli",
            trace_id="trace-cli",
            review_decision="wait",
            evidence_quality="moderate",
            risk_acceptability="caution",
            human_review_required=False,
            message="ok",
        )

    monkeypatch.setattr(model_analysis_cli, "session_scope", fake_session_scope)
    monkeypatch.setattr(model_analysis_cli, "run_model_analysis", fake_run_model_analysis)

    dry_exit = model_analysis_cli.main(["--material-pack-id", "AMP-cli", "--trigger-source", "cli"])
    dry_output = _captured_key_values(capsys)
    confirm_exit = model_analysis_cli.main(
        ["--material-pack-id", "AMP-cli", "--trigger-source", "cli", "--confirm-write"]
    )
    confirm_output = _captured_key_values(capsys)
    real_exit = model_analysis_cli.main(
        ["--material-pack-id", "AMP-cli", "--trigger-source", "cli", "--use-real-model"]
    )
    real_output = _captured_key_values(capsys)

    assert dry_exit == 0
    assert dry_output["model_analysis_result_id"] == ""
    assert captured[0].dry_run is True
    assert captured[0].confirm_write is False
    assert confirm_exit == 0
    assert confirm_output["model_analysis_result_id"] == "MARES-cli"
    assert captured[1].dry_run is False
    assert captured[1].confirm_write is True
    assert real_exit == EXIT_PARAMETER_ERROR
    assert real_output["error_message"] == "real model provider is not implemented in stage 19A"


def _valid_provider_output(
    *,
    review_decision: str = "wait",
    summary_text: str = "这是 mock 审查结果，不是最终交易建议。",
) -> dict[str, Any]:
    return {
        "review_decision": review_decision,
        "evidence_quality": "moderate",
        "logic_consistency": "consistent",
        "risk_acceptability": "caution",
        "strategy_conflict_level": "low",
        "missing_evidence": [],
        "rejection_reasons": [],
        "risk_warnings": [],
        "conditions_to_reconsider": ["需要后续人工复核。"],
        "human_review_questions": [],
        "validation_focus": ["证据完整性"],
        "summary_text": summary_text,
        "not_trading_advice": True,
        "not_trading_advice_text": "这是大模型审查结果，不是最终交易建议。",
    }


def _captured_key_values(capsys: Any) -> dict[str, str]:
    captured = capsys.readouterr().out.strip().splitlines()
    return dict(line.split("=", 1) for line in captured if "=" in line)
