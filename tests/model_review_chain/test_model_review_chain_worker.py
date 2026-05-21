from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.core.config import AppSettings
from app.core.time_utils import now_utc
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER
from app.model_analysis.types import ModelAnalysisServiceResult, ModelAnalysisStatus
from app.model_review_aggregation.schema import ModelReviewAggregationResult, ModelReviewAggregationStatus
from app.model_review_chain.schema import (
    DEFAULT_SCHEDULER_CHAIN_KEY,
    SCHEDULER_RELAY_CHAIN_KEY,
    ModelReviewChainStatus,
    ModelReviewChainStepStatus,
)
from app.model_review_chain.worker import ModelReviewChainWorker
from app.model_review_chain.worker_schema import ModelReviewChainWorkerRequest


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeLockManager:
    def __init__(self, *, acquired: bool = True) -> None:
        self.acquired = acquired
        self.acquire_count = 0
        self.release_count = 0

    def acquire_worker_lock(self, *, key: str, owner: str, ttl_seconds: int) -> Any:
        self.acquire_count += 1
        return SimpleNamespace(key=key, owner=owner, ttl_seconds=ttl_seconds, acquired=self.acquired)

    def release_worker_lock(self, lock: Any) -> bool:
        self.release_count += 1
        return bool(getattr(lock, "acquired", False))


class FakeAggregationService:
    def __init__(self, result: ModelReviewAggregationResult) -> None:
        self.result = result
        self.calls: list[Any] = []

    def run_model_review_aggregation(self, db_session: Any, *, request: Any) -> ModelReviewAggregationResult:
        del db_session
        self.calls.append(request)
        return self.result


class FakeModelAnalysisService:
    def __init__(self, statuses: list[ModelAnalysisStatus]) -> None:
        self.statuses = list(statuses)
        self.calls: list[Any] = []

    def run_model_analysis(self, db_session: Any, *, request: Any) -> ModelAnalysisServiceResult:
        del db_session
        self.calls.append(request)
        status = self.statuses.pop(0) if self.statuses else ModelAnalysisStatus.SUCCESS
        run_id = f"MAR-FAKE-{len(self.calls)}"
        return ModelAnalysisServiceResult(
            status=status,
            exit_code=0 if status in {ModelAnalysisStatus.SUCCESS, ModelAnalysisStatus.PARTIAL_SUCCESS} else 4,
            model_analysis_run_id=run_id,
            model_analysis_result_id=f"MARES-FAKE-{len(self.calls)}" if status == ModelAnalysisStatus.SUCCESS else None,
            review_version_key=f"rvk-{len(self.calls)}",
            material_pack_id=request.material_pack_id,
            aggregation_run_id="AGR-1",
            strategy_signal_run_id="SIG-1",
            trace_id=request.trace_id,
            model_key=request.model_key,
            model_role=request.model_role,
            analysis_mode=request.analysis_mode,
            error_code=None if status == ModelAnalysisStatus.SUCCESS else "fake_model_failure",
            error_message=None if status == ModelAnalysisStatus.SUCCESS else "fake model failure",
        )


class FakeModelReviewChainRepository:
    def __init__(self) -> None:
        self.material_packs: dict[str, Any] = {"AMP-1": _material_pack("AMP-1")}
        self.chain_runs: dict[str, Any] = {}
        self.chain_steps: dict[str, list[Any]] = {}
        self.worker_runs: list[Any] = []
        self.created_chain_count = 0
        self.created_step_count = 0
        self.updated_step_count = 0
        self.updated_chain_count = 0

    def get_material_pack_by_id(self, db_session: Any, *, material_pack_id: str) -> Any | None:
        del db_session
        return self.material_packs.get(material_pack_id)

    def get_chain_run_by_chain_id(self, db_session: Any, *, chain_id: str) -> Any | None:
        del db_session
        return self.chain_runs.get(chain_id)

    def get_latest_chain_run_for_material_pack(self, db_session: Any, *, material_pack_id: str) -> Any | None:
        del db_session
        rows = [row for row in self.chain_runs.values() if row.material_pack_id == material_pack_id]
        return rows[-1] if rows else None

    def list_chain_steps(self, db_session: Any, *, chain_id: str) -> tuple[Any, ...]:
        del db_session
        return tuple(sorted(self.chain_steps.get(chain_id, ()), key=lambda row: row.step_no))

    def list_unfinished_chain_runs(self, db_session: Any, *, limit: int = 20) -> tuple[Any, ...]:
        del db_session
        rows = [row for row in self.chain_runs.values() if row.status != "success"]
        return tuple(rows[:limit])

    def list_worker_real_model_runs_between(self, db_session: Any, *, start_at_utc: Any, end_at_utc: Any) -> tuple[Any, ...]:
        del db_session
        return tuple(row for row in self.worker_runs if start_at_utc <= row.created_at_utc < end_at_utc)

    def create_model_review_chain_run(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        row = _row_from_payload(payload)
        self.chain_runs[row.chain_id] = row
        self.created_chain_count += 1
        return row

    def update_model_review_chain_run(self, db_session: Any, chain_row: Any, *, payload: Any) -> Any:
        del db_session
        _apply_payload(chain_row, payload)
        self.updated_chain_count += 1
        return chain_row

    def create_model_review_chain_step(self, db_session: Any, *, payload: Any) -> Any:
        del db_session
        row = _row_from_payload(payload)
        self.chain_steps.setdefault(row.chain_id, []).append(row)
        self.created_step_count += 1
        return row

    def update_model_review_chain_step(self, db_session: Any, step_row: Any, *, payload: Any) -> Any:
        del db_session
        _apply_payload(step_row, payload)
        self.updated_step_count += 1
        return step_row


def test_real_model_disabled_blocks_without_stage19_call() -> None:
    repo = FakeModelReviewChainRepository()
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])
    result = _worker(repo, _blocked_aggregation(expired=True), model_service, real_enabled=False).run_model_review_chain_worker(
        FakeSession(),
        request=_request(confirm=True),
    )

    assert result.status == "blocked"
    assert result.model_review_invoked is False
    assert "MODEL_REVIEW_REAL_MODEL_ENABLED=false" in (result.model_review_block_reason or "")
    assert model_service.calls == []


def test_auto_run_disabled_does_not_auto_advance() -> None:
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])
    settings = _settings(auto_enabled=False)
    result = _worker_with_settings(settings, FakeModelReviewChainRepository(), _blocked_aggregation(), model_service).run_model_review_chain_worker(
        FakeSession(),
        request=_request(confirm=True),
    )

    assert result.status == "skipped"
    assert result.error_code == "auto_run_disabled"
    assert model_service.calls == []


def test_scheduler_disabled_does_not_run_worker() -> None:
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])
    settings = _settings(scheduler_enabled=False)
    result = _worker_with_settings(settings, FakeModelReviewChainRepository(), _blocked_aggregation(), model_service).run_model_review_chain_worker(
        FakeSession(),
        request=_request(confirm=True),
    )

    assert result.status == "skipped"
    assert result.error_code == "scheduler_model_review_disabled"
    assert model_service.calls == []


def test_model_key_not_in_scheduler_whitelist_blocks_before_stage19() -> None:
    repo = FakeModelReviewChainRepository()
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])
    settings = _settings(allowed_keys="some_other_model", budget="100")

    result = _worker_with_settings(settings, repo, _blocked_aggregation(), model_service).run_model_review_chain_worker(
        FakeSession(),
        request=_request(confirm=True),
    )

    assert result.status == "blocked"
    assert result.error_code == "model_key_not_in_scheduler_whitelist"
    assert model_service.calls == []
    step = next(iter(repo.chain_steps.values()))[0]
    assert step.status == ModelReviewChainStepStatus.BLOCKED.value


def test_daily_budget_exceeded_blocks_before_stage19() -> None:
    repo = FakeModelReviewChainRepository()
    repo.worker_runs.append(SimpleNamespace(estimated_cost="1.00", created_at_utc=now_utc()))
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])
    settings = _settings(budget="1.00")

    result = _worker_with_settings(settings, repo, _blocked_aggregation(), model_service).run_model_review_chain_worker(
        FakeSession(),
        request=_request(confirm=True),
    )

    assert result.status == "blocked"
    assert result.error_code == "daily_budget_exceeded"
    assert model_service.calls == []


def test_max_runs_per_4h_blocks_before_stage19() -> None:
    repo = FakeModelReviewChainRepository()
    repo.worker_runs.append(SimpleNamespace(estimated_cost="0.01", created_at_utc=now_utc()))
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])
    settings = _settings(budget="100", max_runs=1)

    result = _worker_with_settings(settings, repo, _blocked_aggregation(), model_service).run_model_review_chain_worker(
        FakeSession(),
        request=_request(confirm=True),
    )

    assert result.status == "blocked"
    assert result.error_code == "max_runs_per_4h_exceeded"
    assert model_service.calls == []


def test_reuse_within_base_bars_skips_chain_and_stage19() -> None:
    repo = FakeModelReviewChainRepository()
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])
    result = _worker(repo, _reused_aggregation(), model_service).run_model_review_chain_worker(
        FakeSession(),
        request=_request(confirm=True),
    )

    assert result.status == "skipped"
    assert result.model_review_reused is True
    assert result.reused_model_analysis_run_id == "MAR-OLD"
    assert repo.created_chain_count == 0
    assert model_service.calls == []


def test_expired_old_result_is_not_treated_as_latest_review_when_real_disabled() -> None:
    repo = FakeModelReviewChainRepository()
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])
    result = _worker(repo, _blocked_aggregation(expired=True), model_service, real_enabled=False).run_model_review_chain_worker(
        FakeSession(),
        request=_request(confirm=True),
    )

    assert result.model_review_expired is True
    assert result.model_review_invoked is False
    assert result.status == "blocked"
    assert model_service.calls == []


def test_resume_reruns_only_failed_step_after_step1_success() -> None:
    repo = FakeModelReviewChainRepository()
    chain, step1, step2 = _seed_two_step_chain(repo, step2_status=ModelReviewChainStepStatus.FAILED.value)
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])

    result = _worker(repo, _blocked_aggregation(), model_service).run_model_review_chain_worker(
        FakeSession(),
        request=_request(chain_id=chain.chain_id, confirm=True),
    )

    assert result.model_review_chain_status == ModelReviewChainStatus.SUCCESS.value
    assert len(model_service.calls) == 1
    assert model_service.calls[0].chain_step == 2
    assert step1.attempt_no == 1
    assert step2.attempt_no == 2
    assert step2.status == ModelReviewChainStepStatus.SUCCESS.value


def test_success_step_is_not_repeated() -> None:
    repo = FakeModelReviewChainRepository()
    chain, step1, _step2 = _seed_two_step_chain(repo, step2_status=ModelReviewChainStepStatus.FAILED.value)
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])

    _worker(repo, _blocked_aggregation(), model_service).run_model_review_chain_worker(
        FakeSession(),
        request=_request(chain_id=chain.chain_id, confirm=True),
    )

    assert step1.model_analysis_run_id == "MAR-STEP-1"
    assert all(call.chain_step != 1 for call in model_service.calls)


def test_partial_success_is_not_reported_as_full_success() -> None:
    repo = FakeModelReviewChainRepository()
    chain, _step1, _step2 = _seed_two_step_chain(repo, step2_status=ModelReviewChainStepStatus.FAILED.value)
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.FAILED])

    result = _worker(repo, _blocked_aggregation(), model_service).run_model_review_chain_worker(
        FakeSession(),
        request=_request(chain_id=chain.chain_id, confirm=True),
    )

    assert result.status == "blocked"
    assert result.model_review_chain_status == ModelReviewChainStatus.PARTIAL_SUCCESS.value
    assert result.error_code == "chain_not_complete"


def test_dry_run_does_not_write_or_call_stage19() -> None:
    repo = FakeModelReviewChainRepository()
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])
    session = FakeSession()

    result = _worker(repo, _blocked_aggregation(), model_service).run_model_review_chain_worker(
        session,
        request=_request(confirm=False),
    )

    assert result.status == "skipped"
    assert repo.created_chain_count == 0
    assert repo.updated_step_count == 0
    assert session.commit_count == 0
    assert model_service.calls == []


def test_confirm_write_creates_chain_and_writes_success_step() -> None:
    repo = FakeModelReviewChainRepository()
    model_service = FakeModelAnalysisService([ModelAnalysisStatus.SUCCESS])
    session = FakeSession()

    result = _worker(repo, _blocked_aggregation(), model_service).run_model_review_chain_worker(
        session,
        request=_request(confirm=True),
    )

    assert result.status == "success"
    assert result.model_review_invoked is True
    assert repo.created_chain_count == 1
    assert repo.created_step_count == 1
    assert repo.updated_step_count >= 2
    assert session.commit_count >= 1
    assert result.is_final_trading_advice is False
    assert result.is_trading_signal is False
    assert result.is_executable is False
    assert result.auto_trading_allowed is False
    assert model_service.calls[0].trigger_source == "worker"


def _worker(
    repo: FakeModelReviewChainRepository,
    aggregation_result: ModelReviewAggregationResult,
    model_service: FakeModelAnalysisService,
    *,
    real_enabled: bool = True,
) -> ModelReviewChainWorker:
    return _worker_with_settings(
        _settings(real_enabled=real_enabled),
        repo,
        aggregation_result,
        model_service,
    )


def _worker_with_settings(
    settings: AppSettings,
    repo: FakeModelReviewChainRepository,
    aggregation_result: ModelReviewAggregationResult,
    model_service: FakeModelAnalysisService,
) -> ModelReviewChainWorker:
    return ModelReviewChainWorker(
        settings=settings,
        repository=repo,
        aggregation_service=FakeAggregationService(aggregation_result),
        model_analysis_service=model_service,
        lock_manager=FakeLockManager(),
    )


def _settings(
    *,
    real_enabled: bool = True,
    auto_enabled: bool = True,
    scheduler_enabled: bool = True,
    allowed_keys: str = "deepseek_v4_pro_review,deepseek_v4_flash_review",
    budget: str = "100",
    max_runs: int = 10,
) -> AppSettings:
    return AppSettings(
        model_review_enabled=True,
        model_review_real_model_enabled=real_enabled,
        model_review_auto_run_enabled=auto_enabled,
        model_review_scheduler_enabled=scheduler_enabled,
        model_review_scheduler_allowed_model_keys=allowed_keys,
        model_review_daily_budget_usd=budget,
        model_review_max_runs_per_4h=max_runs,
    )


def _request(
    *,
    confirm: bool,
    chain_id: str | None = None,
    chain_key: str = DEFAULT_SCHEDULER_CHAIN_KEY,
) -> ModelReviewChainWorkerRequest:
    return ModelReviewChainWorkerRequest(
        material_pack_id="" if chain_id else "AMP-1",
        chain_id=chain_id,
        chain_key=chain_key,
        trigger_source=TRIGGER_SOURCE_SCHEDULER,
        dry_run=not confirm,
        confirm_write=confirm,
        trace_id="trace-20c",
    )


def _material_pack(material_pack_id: str) -> Any:
    return SimpleNamespace(
        material_pack_id=material_pack_id,
        aggregation_run_id="AGR-1",
        strategy_signal_run_id="SIG-1",
        snapshot_id="SNAP-1",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
    )


def _blocked_aggregation(*, expired: bool = False) -> ModelReviewAggregationResult:
    return ModelReviewAggregationResult(
        status=ModelReviewAggregationStatus.BLOCKED,
        exit_code=2,
        review_aggregation_run_id="MRA-1",
        material_pack_id="AMP-1",
        aggregation_run_id="AGR-1",
        strategy_signal_run_id="SIG-1",
        snapshot_id="SNAP-1",
        trace_id="trace-20c",
        model_review_skip_reason="本轮未调用大模型；没有可用模型审查结果。",
        model_review_block_reason="MODEL_REVIEW_REAL_MODEL_ENABLED=false" if expired else "no_model_review_result",
        model_review_basis="expired_model_review_not_used" if expired else "material_only_without_model_review",
        model_review_reuse_status="model_review_expired_but_real_model_disabled" if expired else "no_model_review_result",
        model_review_expired=expired,
        error_code="model_review_expired_but_real_model_disabled" if expired else "no_model_review_result",
        error_message="expired" if expired else "no result",
    )


def _reused_aggregation() -> ModelReviewAggregationResult:
    return ModelReviewAggregationResult(
        status=ModelReviewAggregationStatus.SUCCESS,
        exit_code=0,
        review_aggregation_run_id="MRA-1",
        material_pack_id="AMP-1",
        aggregation_run_id="AGR-1",
        strategy_signal_run_id="SIG-1",
        snapshot_id="SNAP-1",
        trace_id="trace-20c",
        accepted_model_result_count=1,
        model_review_invocation_mode="reused",
        model_review_reused=True,
        reused_model_analysis_run_id="MAR-OLD",
        model_review_skip_reason="本轮未调用大模型；复用旧模型结果。",
        model_review_basis="reused_model_review",
        model_review_reuse_status="reused_within_base_bar_ttl",
        model_review_reuse_base_bars=2,
        summary_text="本轮未调用大模型；复用旧模型结果。",
    )


def _seed_two_step_chain(
    repo: FakeModelReviewChainRepository,
    *,
    step2_status: str,
) -> tuple[Any, Any, Any]:
    chain = SimpleNamespace(
        chain_id="CHAIN-1",
        material_pack_id="AMP-1",
        aggregation_run_id="AGR-1",
        strategy_signal_run_id="SIG-1",
        snapshot_id="SNAP-1",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        chain_key=SCHEDULER_RELAY_CHAIN_KEY,
        chain_profile_version="scheduler_chain_profile_v1",
        status="partial_success",
        trigger_source=TRIGGER_SOURCE_CLI,
        trace_id="trace-20c",
        current_step=2,
        total_steps=2,
        success_step_count=1,
        failed_step_count=1,
        timeout_step_count=0,
        skipped_step_count=0,
        blocked_step_count=0,
        max_retry_count=1,
        summary_text="partial",
        error_code="partial_success",
        error_message="partial",
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
    )
    step1 = _step("CHAIN-1", 1, "deepseek_v4_pro_review", "mathematical_structure_review", "success", 1, "MAR-STEP-1")
    step2 = _step("CHAIN-1", 2, "deepseek_v4_pro_review", "mathematical_structure_review", step2_status, 1, "MAR-OLD-2")
    step2.parent_model_analysis_run_id = "MAR-STEP-1"
    repo.chain_runs[chain.chain_id] = chain
    repo.chain_steps[chain.chain_id] = [step1, step2]
    return chain, step1, step2


def _step(
    chain_id: str,
    step_no: int,
    model_key: str,
    model_role: str,
    status: str,
    attempt_no: int,
    model_analysis_run_id: str | None,
) -> Any:
    return SimpleNamespace(
        chain_step_id=f"CHSTEP-{step_no}",
        chain_id=chain_id,
        step_no=step_no,
        model_key=model_key,
        model_role=model_role,
        parent_step_id=None,
        parent_model_analysis_run_id=None,
        model_analysis_run_id=model_analysis_run_id,
        status=status,
        attempt_no=attempt_no,
        max_retry_count=1,
        started_at_utc=None,
        finished_at_utc=None,
        error_code=None,
        error_message=None,
        retry_after_utc=None,
        step_input_hash=None,
        step_output_hash=None,
    )


def _row_from_payload(payload: Any) -> Any:
    values = {}
    for field_name, value in payload.__dict__.items():
        values[field_name] = value.value if hasattr(value, "value") else value
    return SimpleNamespace(**values)


def _apply_payload(row: Any, payload: Any) -> None:
    for field_name, value in payload.__dict__.items():
        setattr(row, field_name, value.value if hasattr(value, "value") else value)
