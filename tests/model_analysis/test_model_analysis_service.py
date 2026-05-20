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
from app.model_analysis.schema_validator import validate_model_review_output
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

    def update_model_analysis_run(self, _db_session: Any, run_row: Any, *, payload: Any) -> Any:
        row_data = dict(payload.__dict__)
        row_data["status"] = payload.status.value
        for key, value in row_data.items():
            setattr(run_row, key, value)
        return run_row

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
    snapshot_id: str = "MCS-stage19",
    strategy_signal_run_id: str = "SSR-stage19",
    material_payload: Mapping[str, Any] | str | None = None,
    summary_payload: Mapping[str, Any] | str | None = None,
    question_payload: Mapping[str, Any] | str | None = None,
    validation_plan_payload: Mapping[str, Any] | str | None = None,
    data_window_payload: Mapping[str, Any] | str | None = None,
    future_leakage_guard_payload: Mapping[str, Any] | str | None = None,
    failed_strategy_count: int = 0,
    invalid_strategy_count: int = 0,
    not_implemented_strategy_count: int = 0,
    effective_strategy_count: int = 1,
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
    default_question_payload = {"questions": ["材料证据是否足够？"]}
    default_material_payload = {
        "strategy_summaries": strategy_items,
        "strategy_conflict_points": {
            "failed_strategy_count": failed_strategy_count,
            "invalid_strategy_count": invalid_strategy_count,
            "not_implemented_strategy_count": not_implemented_strategy_count,
        },
        "question_list_for_stage19": default_question_payload,
    }
    default_summary_payload = {
        "summary": "stage 18 compact material",
        "strategy_summaries": strategy_items,
        "effective_strategy_count": effective_strategy_count,
    }
    return SimpleNamespace(
        material_pack_id=material_pack_id,
        aggregation_run_id="SAR-stage19",
        strategy_signal_run_id=strategy_signal_run_id,
        snapshot_id=snapshot_id,
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        aggregation_version="aggregation_v1",
        material_schema_version="material_schema_v1",
        status=status,
        material_json=_json_payload(default_material_payload if material_payload is None else material_payload),
        summary_json=_json_payload(default_summary_payload if summary_payload is None else summary_payload),
        question_json=_json_payload(default_question_payload if question_payload is None else question_payload),
        validation_plan_json=_json_payload(
            {"focus": ["证据完整性", "冲突解释"]}
            if validation_plan_payload is None
            else validation_plan_payload
        ),
        data_window_json=_json_payload(
            {"base_interval": "4h", "higher_interval": "1d", "source_snapshot_id": snapshot_id}
            if data_window_payload is None
            else data_window_payload
        ),
        future_leakage_guard_json=_json_payload(
            {"future_leakage_guard": True, "uses_closed_material_only": True}
            if future_leakage_guard_payload is None
            else future_leakage_guard_payload
        ),
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


def test_model_registry_missing_empty_disabled_and_non_mock_are_blocked(tmp_path: Path) -> None:
    missing_dir = tmp_path / "missing"
    missing_dir.mkdir()
    missing = service_with_repo(
        FakeModelAnalysisRepository(material_pack=material_pack()),
        settings=AppSettings(model_review_config_dir=str(missing_dir)),
    ).run_model_analysis(FakeSession(), request=run_request())

    empty_dir = tmp_path / "empty"
    _write_model_registry(empty_dir, enabled_models=[])
    empty = service_with_repo(
        FakeModelAnalysisRepository(material_pack=material_pack()),
        settings=AppSettings(model_review_config_dir=str(empty_dir)),
    ).run_model_analysis(FakeSession(), request=run_request())

    disabled_dir = tmp_path / "disabled"
    _write_model_registry(disabled_dir, model_enabled=False)
    disabled = service_with_repo(
        FakeModelAnalysisRepository(material_pack=material_pack()),
        settings=AppSettings(model_review_config_dir=str(disabled_dir)),
    ).run_model_analysis(FakeSession(), request=run_request())

    future_dir = tmp_path / "future"
    _write_model_registry(future_dir, model_key="future_review", provider="future_provider")
    future = service_with_repo(
        FakeModelAnalysisRepository(material_pack=material_pack()),
        settings=AppSettings(model_review_config_dir=str(future_dir)),
    ).run_model_analysis(FakeSession(), request=run_request())

    assert missing.status == ModelAnalysisStatus.BLOCKED
    assert missing.error_code == "model_registry_not_found"
    assert empty.status == ModelAnalysisStatus.BLOCKED
    assert empty.error_code == "model_registry_empty"
    assert disabled.status == ModelAnalysisStatus.BLOCKED
    assert disabled.error_code == "no_enabled_model_config"
    assert future.status == ModelAnalysisStatus.BLOCKED
    assert future.error_code == "no_enabled_mock_model_config"


def test_enabled_mock_registry_allows_dry_run_and_supplies_metadata(tmp_path: Path) -> None:
    config_dir = tmp_path / "enabled"
    _write_model_registry(config_dir)
    repo = FakeModelAnalysisRepository(material_pack=material_pack())

    result = service_with_repo(
        repo,
        settings=AppSettings(model_review_config_dir=str(config_dir), model_review_enabled=False),
    ).run_model_analysis(FakeSession(), request=run_request())

    assert result.status == ModelAnalysisStatus.SUCCESS
    assert result.model_key == "mock_review"
    assert result.model_role == "review_gate"
    assert result.analysis_mode == "single"
    assert result.details["mock_provider_only"] is True
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
    assert repo.run_rows[0].model_key == "mock_review"
    assert repo.run_rows[0].model_role == "review_gate"
    assert repo.run_rows[0].analysis_mode == "single"
    assert repo.run_rows[0].chain_id is None
    assert repo.run_rows[0].chain_step is None
    assert repo.run_rows[0].parent_model_analysis_run_id is None
    assert repo.run_rows[0].comparison_group_id is None
    assert repo.result_rows[0].review_version_key == result.review_version_key


def test_missing_or_non_reviewable_material_pack_is_blocked() -> None:
    missing = service_with_repo(FakeModelAnalysisRepository()).run_model_analysis(FakeSession(), request=run_request())
    blocked_pack = service_with_repo(
        FakeModelAnalysisRepository(material_pack=material_pack(status="failed"))
    ).run_model_analysis(FakeSession(), request=run_request())

    assert missing.status == ModelAnalysisStatus.BLOCKED
    assert missing.error_code == "material_pack_not_found"
    assert blocked_pack.status == ModelAnalysisStatus.BLOCKED
    assert blocked_pack.error_code == "material_pack_status_not_reviewable"
    assert blocked_pack.message == "analysis_material_pack status is not reviewable."
    assert "status is not success" not in blocked_pack.message


def test_success_and_complete_partial_success_material_pack_can_enter_review() -> None:
    success_result = service_with_repo(
        FakeModelAnalysisRepository(material_pack=material_pack(status="success"))
    ).run_model_analysis(FakeSession(), request=run_request())
    partial_result = service_with_repo(
        FakeModelAnalysisRepository(material_pack=material_pack(status="partial_success"))
    ).run_model_analysis(FakeSession(), request=run_request())

    assert success_result.status == ModelAnalysisStatus.SUCCESS
    assert partial_result.status == ModelAnalysisStatus.SUCCESS


def test_partial_success_core_material_missing_is_blocked() -> None:
    cases = (
        ("material_json", {"material_payload": {}}),
        ("summary_json", {"summary_payload": {}}),
        ("validation_plan_json", {"validation_plan_payload": {}}),
        ("data_window_json", {"data_window_payload": {}}),
        ("future_leakage_guard_json", {"future_leakage_guard_payload": {}}),
        (
            "question_json",
            {
                "question_payload": {},
                "material_payload": {
                    "strategy_summaries": [{"strategy_name": "fixture_without_question"}],
                    "strategy_conflict_points": {
                        "failed_strategy_count": 0,
                        "invalid_strategy_count": 0,
                        "not_implemented_strategy_count": 0,
                    },
                },
            },
        ),
    )

    for expected_field, kwargs in cases:
        result = service_with_repo(
            FakeModelAnalysisRepository(
                material_pack=material_pack(status="partial_success", **kwargs)
            )
        ).run_model_analysis(FakeSession(), request=run_request())

        assert result.status == ModelAnalysisStatus.BLOCKED
        assert result.error_code == "material_pack_partial_core_incomplete"
        assert result.message == (
            "analysis_material_pack partial_success is not reviewable because core material is incomplete."
        )
        assert expected_field in (result.error_message or "")


def test_partial_success_missing_ids_or_effective_strategy_count_is_blocked() -> None:
    cases = (
        ("snapshot_id", {"snapshot_id": ""}),
        ("strategy_signal_run_id", {"strategy_signal_run_id": ""}),
        ("effective_strategy_count", {"effective_strategy_count": 0}),
    )

    for expected_field, kwargs in cases:
        result = service_with_repo(
            FakeModelAnalysisRepository(
                material_pack=material_pack(status="partial_success", **kwargs)
            )
        ).run_model_analysis(FakeSession(), request=run_request())

        assert result.status == ModelAnalysisStatus.BLOCKED
        assert result.error_code == "material_pack_partial_core_incomplete"
        assert expected_field in (result.error_message or "")


def test_partial_success_failed_or_invalid_strategy_counts_are_blocked() -> None:
    failed_result = service_with_repo(
        FakeModelAnalysisRepository(
            material_pack=material_pack(status="partial_success", failed_strategy_count=1)
        )
    ).run_model_analysis(FakeSession(), request=run_request())
    invalid_result = service_with_repo(
        FakeModelAnalysisRepository(
            material_pack=material_pack(status="partial_success", invalid_strategy_count=1)
        )
    ).run_model_analysis(FakeSession(), request=run_request())

    for result in (failed_result, invalid_result):
        assert result.status == ModelAnalysisStatus.BLOCKED
        assert result.error_code == "material_pack_partial_failed_or_invalid_strategy"
        assert result.message == (
            "analysis_material_pack partial_success is not reviewable because strategy material "
            "contains failed or invalid results."
        )


def test_partial_success_not_implemented_only_can_enter_review() -> None:
    result = service_with_repo(
        FakeModelAnalysisRepository(
            material_pack=material_pack(
                status="partial_success",
                not_implemented_strategy_count=2,
                failed_strategy_count=0,
                invalid_strategy_count=0,
                effective_strategy_count=1,
            )
        )
    ).run_model_analysis(FakeSession(), request=run_request())

    assert result.status == ModelAnalysisStatus.SUCCESS


def test_non_reviewable_statuses_are_blocked() -> None:
    for status in ("failed", "blocked", "skipped", "running", "pending", "unknown"):
        result = service_with_repo(
            FakeModelAnalysisRepository(material_pack=material_pack(status=status))
        ).run_model_analysis(FakeSession(), request=run_request())

        assert result.status == ModelAnalysisStatus.BLOCKED
        assert result.error_code == "material_pack_status_not_reviewable"
        assert result.message == "analysis_material_pack status is not reviewable."
        assert "status is not success" not in result.message


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


def test_schema_requires_human_review_required_boolean_and_not_trading_advice_true() -> None:
    not_boolean = _valid_provider_output(human_review_required=False)
    not_boolean["human_review_required"] = "false"
    not_boolean_result = validate_model_review_output(not_boolean)

    not_advice = _valid_provider_output()
    not_advice["not_trading_advice"] = False
    not_advice_result = validate_model_review_output(not_advice)

    assert not_boolean_result.is_valid is False
    assert not_boolean_result.error_code == "schema_human_review_required_not_boolean"
    assert not_advice_result.is_valid is False
    assert not_advice_result.error_code == "schema_not_trading_advice_false"


def test_existing_success_result_skips_and_blocked_failed_attempts_do_not_lock_rerun() -> None:
    existing = SimpleNamespace(
        model_analysis_result_id="MARES-existing",
        review_version_key="",
        material_pack_id="AMP-stage19",
        aggregation_run_id="SAR-stage19",
        strategy_signal_run_id="SSR-stage19",
        review_decision="wait",
        human_review_required=False,
        evidence_quality="moderate",
        risk_acceptability="caution",
        strategy_conflict_level="low",
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
        human_review_required=False,
        evidence_quality="moderate",
        risk_acceptability="caution",
        strategy_conflict_level="low",
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
        override_response=_valid_provider_output(
            review_decision=ReviewDecision.HUMAN_REVIEW_REQUIRED.value,
            human_review_required=True,
        )
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
    assert repo.result_rows[0].human_review_required is True


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
        strategy_conflict_level="low",
        human_review_required=False,
    )
    body = build_model_analysis_visible_body(base_result)
    assert "不是最终交易建议" in body
    assert "本阶段未自动交易" in body
    assert "本阶段未生成订单" in body
    assert "本阶段未给出仓位或杠杆" in body
    assert "是否需要人工判断" in body

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

    from app.storage.mysql.models.model_analysis import (
        ModelAnalysisResult,
        ModelAnalysisRun,
        ModelProviderCallArtifact,
    )

    run_table = ModelAnalysisRun.__table__
    run_unique_names = {
        constraint.name
        for constraint in run_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_model_analysis_run_id" in run_unique_names
    assert "uk_model_analysis_run_review_version_key" not in run_unique_names
    assert run_table.c.human_review_required.default is not None
    assert run_table.c.human_review_required.default.arg is False

    result_table = ModelAnalysisResult.__table__
    result_columns = {column.name for column in result_table.columns}
    result_unique_names = {
        constraint.name
        for constraint in result_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_model_analysis_result_id" in result_unique_names
    assert "uk_model_analysis_result_review_version_key" in result_unique_names
    assert "human_review_required" in result_columns
    assert result_table.c.human_review_required.default is not None
    assert result_table.c.human_review_required.default.arg is False

    run_columns = {column.name for column in run_table.columns}
    assert "model_key" in run_columns
    assert "model_role" in run_columns
    assert "analysis_mode" in run_columns
    assert "chain_id" in run_columns
    assert "chain_step" in run_columns
    assert "parent_model_analysis_run_id" in run_columns
    assert "comparison_group_id" in run_columns
    assert "profile_version" in run_columns
    assert "profile_hash" in run_columns
    assert "api_style" in run_columns
    assert "raw_response_hash" in run_columns
    assert "raw_response_storage_ref" in run_columns
    assert "input_token_count" in run_columns
    assert "output_token_count" in run_columns
    assert "total_token_count" in run_columns
    assert "estimated_cost" in run_columns
    run_index_names = {index.name for index in run_table.indexes}
    assert "idx_model_analysis_run_review_version_key" in run_index_names
    assert "idx_model_analysis_run_model_key" in run_index_names
    assert "idx_model_analysis_run_analysis_mode" in run_index_names
    assert "idx_model_analysis_version_status" not in run_index_names
    for index in run_table.indexes:
        assert len(index.columns) <= 2
        assert index.unique is False

    artifact_table = ModelProviderCallArtifact.__table__
    artifact_columns = {column.name for column in artifact_table.columns}
    artifact_unique_names = {
        constraint.name
        for constraint in artifact_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "artifact_id" in artifact_columns
    assert "storage_ref" in artifact_columns
    assert "sha256_hash" in artifact_columns
    assert "uq_model_provider_call_artifact_id" in artifact_unique_names


def test_stage19_migration_does_not_create_large_composite_varchar_indexes() -> None:
    source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "migrations/versions/20260520_19_create_model_analysis_tables.py",
            "migrations/versions/20260521_19a_model_review_registry_fields.py",
            "migrations/versions/20260522_19a_model_analysis_run_human_review_default.py",
            "migrations/versions/20260523_19b_deepseek_provider_fields.py",
        )
    )

    assert "uk_model_analysis_run_review_version_key" not in source
    assert "uk_model_analysis_result_review_version_key" in source
    assert "idx_model_analysis_version_status" not in source
    assert "review_version_key\", \"material_pack_id" not in source
    assert "strategy_signal_run_id\", \"review_version_key" not in source
    assert "human_review_required" in source
    assert 'sa.Column("human_review_required", sa.Boolean(), nullable=False, server_default=sa.false())' in source


def test_model_analysis_source_does_not_call_real_model_strategy_or_trading_interfaces() -> None:
    import app.model_analysis.model_registry as registry_module
    import app.model_analysis.prompt_builder as prompt_module
    import app.model_analysis.providers.mock as mock_module
    import app.model_analysis.schema_validator as schema_module
    import app.model_analysis.service as service_module
    import scripts.run_model_analysis as script_module

    source = "\n".join(
        inspect.getsource(module)
        for module in (prompt_module, registry_module, mock_module, schema_module, service_module, script_module)
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
            strategy_conflict_level="low",
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
        [
            "--material-pack-id",
            "AMP-cli",
            "--trigger-source",
            "cli",
            "--use-real-model",
            "--model-key",
            "deepseek_v4_pro_review",
            "--confirm-real-model-cost",
        ]
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
    assert real_exit == 0
    assert real_output["model_analysis_run_id"] == "MAR-cli"
    assert captured[2].use_real_model is True
    assert captured[2].model_key == "deepseek_v4_pro_review"
    assert captured[2].confirm_real_model_cost is True


def _valid_provider_output(
    *,
    review_decision: str = "wait",
    human_review_required: bool | None = None,
    summary_text: str = "这是 mock 审查结果，不是最终交易建议。",
) -> dict[str, Any]:
    return {
        "review_decision": review_decision,
        "human_review_required": (
            review_decision == ReviewDecision.HUMAN_REVIEW_REQUIRED.value
            if human_review_required is None
            else human_review_required
        ),
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


def _json_payload(value: Mapping[str, Any] | list[Any] | str) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _write_model_registry(
    base_dir: Path,
    *,
    enabled_models: list[str] | None = None,
    model_key: str = "mock_review",
    provider: str = "mock",
    model_enabled: bool = True,
) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    active_models = [model_key] if enabled_models is None else enabled_models
    registry_lines = ["enabled_models:"]
    registry_lines.extend(f"  - {item}" for item in active_models)
    registry_lines.append("")
    registry_lines.append("default_mode: single")
    (base_dir / "model_registry.yaml").write_text("\n".join(registry_lines), encoding="utf-8")
    if model_key in active_models:
        (base_dir / f"{model_key}.yaml").write_text(
            "\n".join(
                [
                    f"model_key: {model_key}",
                    f"provider: {provider}",
                    f"enabled: {str(model_enabled).lower()}",
                    "model_name: mock-reviewer",
                    "model_version: mock_v1",
                    "model_role: review_gate",
                    "analysis_mode: single",
                    "prompt_template_version: review_gate_v1",
                    "review_schema_version: review_schema_v1",
                ]
            ),
            encoding="utf-8",
        )


def _captured_key_values(capsys: Any) -> dict[str, str]:
    captured = capsys.readouterr().out.strip().splitlines()
    return dict(line.split("=", 1) for line in captured if "=" in line)
