from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_review_chain.schema import (
    DEFAULT_CHAIN_KEY,
    ModelReviewChainRequest,
    ModelReviewChainStatus,
    ModelReviewChainStepStatus,
)
from app.model_review_chain.service import ModelReviewChainService


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeModelReviewChainRepository:
    def __init__(self) -> None:
        self.material_packs: dict[str, Any] = {}
        self.chain_runs: dict[str, Any] = {}
        self.chain_steps: dict[str, list[Any]] = {}
        self.model_analysis_runs: dict[str, Any] = {}

    def get_material_pack_by_id(self, db_session: Any, *, material_pack_id: str) -> Any | None:
        return self.material_packs.get(material_pack_id)

    def get_chain_run_by_chain_id(self, db_session: Any, *, chain_id: str) -> Any | None:
        return self.chain_runs.get(chain_id)

    def list_chain_steps(self, db_session: Any, *, chain_id: str) -> tuple[Any, ...]:
        return tuple(sorted(self.chain_steps.get(chain_id, ()), key=lambda row: row.step_no))

    def create_model_review_chain_run(self, db_session: Any, *, payload: Any) -> Any:
        row = _row_from_payload(payload)
        self.chain_runs[row.chain_id] = row
        return row

    def update_model_review_chain_run(self, db_session: Any, chain_row: Any, *, payload: Any) -> Any:
        _apply_payload(chain_row, payload)
        return chain_row

    def create_model_review_chain_step(self, db_session: Any, *, payload: Any) -> Any:
        row = _row_from_payload(payload)
        self.chain_steps.setdefault(row.chain_id, []).append(row)
        return row

    def update_model_review_chain_step(self, db_session: Any, step_row: Any, *, payload: Any) -> Any:
        _apply_payload(step_row, payload)
        return step_row

    def create_mock_model_analysis_run(self, db_session: Any, *, payload: Any) -> Any:
        row = _row_from_payload(payload)
        self.model_analysis_runs[row.model_analysis_run_id] = row
        return row


def test_material_pack_missing_is_blocked_and_writes_nothing() -> None:
    repo = FakeModelReviewChainRepository()
    session = FakeSession()
    result = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(
            material_pack_id="AMP-MISSING",
            dry_run=False,
            confirm_write=True,
        ),
    )

    assert result.status == ModelReviewChainStatus.BLOCKED
    assert result.error_code == "material_pack_not_found"
    assert repo.chain_runs == {}
    assert repo.chain_steps == {}
    assert repo.model_analysis_runs == {}
    assert session.commit_count == 0


def test_dry_run_creates_two_step_mock_chain_without_writes() -> None:
    repo = _repo_with_material()
    session = FakeSession()
    result = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(material_pack_id="AMP-1", dry_run=True, confirm_write=False),
    )

    assert result.status == ModelReviewChainStatus.SUCCESS
    assert result.total_steps == 2
    assert result.mock_step_execution_count == 2
    assert result.real_model_invoked is False
    assert result.is_final_trading_advice is False
    assert result.is_trading_signal is False
    assert result.is_executable is False
    assert result.auto_trading_allowed is False
    assert repo.chain_runs == {}
    assert repo.chain_steps == {}
    assert repo.model_analysis_runs == {}
    assert session.commit_count == 0


def test_confirm_write_success_persists_chain_steps_and_mock_model_runs() -> None:
    repo = _repo_with_material()
    session = FakeSession()
    result = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(material_pack_id="AMP-1", dry_run=False, confirm_write=True),
    )

    assert result.status == ModelReviewChainStatus.SUCCESS
    assert result.material_pack_id == "AMP-1"
    assert result.aggregation_run_id == "AGR-1"
    assert result.strategy_signal_run_id == "SIG-1"
    assert result.snapshot_id == "SNAP-1"
    assert result.success_step_count == 2
    assert len(repo.chain_runs) == 1
    assert len(repo.chain_steps[result.chain_id]) == 2
    assert len(repo.model_analysis_runs) == 2
    assert session.commit_count == 1
    for step in result.steps:
        assert step.model_analysis_run_id
        stored_run = repo.model_analysis_runs[step.model_analysis_run_id]
        assert stored_run.model_provider == "mock"
        assert stored_run.is_final_trading_advice is False
        assert stored_run.is_trading_signal is False
        assert stored_run.is_executable is False
        assert stored_run.auto_trading_allowed is False


def test_step_2_failure_marks_chain_partial_success() -> None:
    repo = _repo_with_material()
    session = FakeSession()
    result = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(
            material_pack_id="AMP-1",
            dry_run=False,
            confirm_write=True,
            simulate_step_failure=2,
        ),
    )

    assert result.status == ModelReviewChainStatus.PARTIAL_SUCCESS
    assert result.success_step_count == 1
    assert result.failed_step_count == 1
    assert result.steps[0].status == ModelReviewChainStepStatus.SUCCESS
    assert result.steps[1].status == ModelReviewChainStepStatus.FAILED
    assert "full review" in result.summary_text


def test_resume_does_not_rerun_success_step_and_only_reruns_failed_step() -> None:
    repo = _repo_with_material()
    session = FakeSession()
    failed_result = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(
            material_pack_id="AMP-1",
            dry_run=False,
            confirm_write=True,
            simulate_step_failure=2,
        ),
    )
    step_1_before = repo.chain_steps[failed_result.chain_id][0]
    step_1_attempt_before = step_1_before.attempt_no
    step_1_run_before = step_1_before.model_analysis_run_id
    model_run_count_before = len(repo.model_analysis_runs)

    resumed = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(
            chain_id=failed_result.chain_id,
            resume=True,
            dry_run=False,
            confirm_write=True,
        ),
    )

    step_1_after = repo.chain_steps[failed_result.chain_id][0]
    step_2_after = repo.chain_steps[failed_result.chain_id][1]
    assert resumed.status == ModelReviewChainStatus.SUCCESS
    assert step_1_after.attempt_no == step_1_attempt_before
    assert step_1_after.model_analysis_run_id == step_1_run_before
    assert resumed.steps[0].skipped_due_to_success_resume is True
    assert step_2_after.attempt_no == 2
    assert step_2_after.status == "success"
    assert len(repo.model_analysis_runs) == model_run_count_before + 1


@pytest.mark.parametrize(
    "resumable_status",
    [
        ModelReviewChainStepStatus.FAILED.value,
        ModelReviewChainStepStatus.RETRY_WAITING.value,
        ModelReviewChainStepStatus.TIMEOUT.value,
    ],
)
def test_resume_reruns_failed_retry_waiting_and_timeout_steps(resumable_status: str) -> None:
    repo = _repo_with_material()
    session = FakeSession()
    result = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(material_pack_id="AMP-1", dry_run=False, confirm_write=True),
    )
    step_1, step_2 = repo.chain_steps[result.chain_id]
    step_2.status = resumable_status
    step_2.attempt_no = 1
    step_2.model_analysis_run_id = "OLD-RUN"
    repo.chain_runs[result.chain_id].status = "partial_success"
    model_run_count_before = len(repo.model_analysis_runs)

    resumed = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(
            chain_id=result.chain_id,
            resume=True,
            dry_run=False,
            confirm_write=True,
        ),
    )

    assert step_1.attempt_no == 1
    assert step_2.status == "success"
    assert step_2.attempt_no == 2
    assert step_2.model_analysis_run_id != "OLD-RUN"
    assert resumed.status == ModelReviewChainStatus.SUCCESS
    assert len(repo.model_analysis_runs) == model_run_count_before + 1


def test_resume_stops_when_max_retry_count_is_exhausted() -> None:
    repo = _repo_with_material()
    session = FakeSession()
    failed_result = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(
            material_pack_id="AMP-1",
            dry_run=False,
            confirm_write=True,
            simulate_step_failure=2,
            max_retry_count=0,
        ),
    )
    model_run_count_before = len(repo.model_analysis_runs)

    resumed = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(
            chain_id=failed_result.chain_id,
            resume=True,
            dry_run=False,
            confirm_write=True,
        ),
    )

    assert resumed.status == ModelReviewChainStatus.PARTIAL_SUCCESS
    assert resumed.steps[1].retry_blocked is True
    assert repo.chain_steps[failed_result.chain_id][1].attempt_no == 1
    assert len(repo.model_analysis_runs) == model_run_count_before


def test_scheduler_trigger_source_is_rejected_without_writes() -> None:
    repo = _repo_with_material()
    session = FakeSession()
    result = _service(repo).run_model_review_chain(
        session,
        request=ModelReviewChainRequest(
            material_pack_id="AMP-1",
            trigger_source="scheduler",
            dry_run=True,
            confirm_write=False,
        ),
    )

    assert result.status == ModelReviewChainStatus.FAILED
    assert result.error_code == "trigger_source_not_allowed"
    assert result.real_model_invoked is False
    assert repo.chain_runs == {}
    assert repo.model_analysis_runs == {}


def _repo_with_material() -> FakeModelReviewChainRepository:
    repo = FakeModelReviewChainRepository()
    repo.material_packs["AMP-1"] = SimpleNamespace(
        material_pack_id="AMP-1",
        aggregation_run_id="AGR-1",
        strategy_signal_run_id="SIG-1",
        snapshot_id="SNAP-1",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
    )
    return repo


def _service(repo: FakeModelReviewChainRepository) -> ModelReviewChainService:
    return ModelReviewChainService(repository=repo)


def _row_from_payload(payload: Any) -> Any:
    values = {}
    for field_name, value in payload.__dict__.items():
        values[field_name] = value.value if hasattr(value, "value") else value
    values.setdefault("chain_key", DEFAULT_CHAIN_KEY)
    return SimpleNamespace(**values)


def _apply_payload(row: Any, payload: Any) -> None:
    for field_name, value in payload.__dict__.items():
        setattr(row, field_name, value.value if hasattr(value, "value") else value)
