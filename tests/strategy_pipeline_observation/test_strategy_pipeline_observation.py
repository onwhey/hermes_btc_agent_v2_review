from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterator

from app.core.config import AppSettings
from app.strategy_pipeline.types import PIPELINE_STEP_STAGE20, PIPELINE_STEP_STAGE26B
from app.strategy_pipeline_observation.service import StrategyPipelineObservationService
from app.strategy_pipeline_observation.types import (
    AdviceLinkSummary,
    EvidenceQualitySummary,
    KlineSlotObservationSource,
    OBSERVATION_STATUS_ADVICE_GENERATED,
    OBSERVATION_STATUS_EXPECTED_BLOCKED_BY_MODEL_CONFIG,
    OBSERVATION_STATUS_MISSING_PIPELINE,
    OBSERVATION_STATUS_MODEL_REVIEW_COMPLETED,
    OBSERVATION_STATUS_ONLY_CLI_RUNS,
    OBSERVATION_STATUS_PIPELINE_FAILED,
    OBSERVATION_STATUS_QUALITY_BLOCKED,
    PipelineRunCandidate,
    StrategyPipelineObservationBuildRequest,
    format_strategy_pipeline_observation_report_lines,
)
from scripts.build_strategy_pipeline_observations import main as cli_main

SLOT = datetime(2026, 5, 31, 4, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class FakeObservationRepository:
    """In-memory 26C repository that never calls model, Hermes, Binance, or stages."""

    def __init__(
        self,
        *,
        slots: tuple[KlineSlotObservationSource, ...] | None = None,
        pipelines_by_slot: dict[datetime, tuple[PipelineRunCandidate, ...]] | None = None,
        quality_by_pipeline: dict[str, EvidenceQualitySummary] | None = None,
        advice_by_pipeline: dict[str, AdviceLinkSummary] | None = None,
    ) -> None:
        self.slots = slots or (_slot(SLOT),)
        self.pipelines_by_slot = pipelines_by_slot or {}
        self.quality_by_pipeline = quality_by_pipeline or {}
        self.advice_by_pipeline = advice_by_pipeline or {}
        self.persisted_by_scope: dict[tuple[str, str, str, datetime], Any] = {}
        self.upsert_calls = 0
        self.calls: list[str] = []

    def list_kline_slots(self, db_session: Any, *, request: StrategyPipelineObservationBuildRequest) -> tuple[Any, ...]:
        del db_session
        self.calls.append("list_slots")
        if request.kline_slot_utc is not None:
            return tuple(slot for slot in self.slots if slot.open_time_utc == request.kline_slot_utc)
        return self.slots[: request.limit]

    def list_pipeline_runs_for_slots(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineObservationBuildRequest,
        slots: tuple[KlineSlotObservationSource, ...],
    ) -> dict[datetime, tuple[PipelineRunCandidate, ...]]:
        del db_session, request, slots
        self.calls.append("list_pipelines")
        return self.pipelines_by_slot

    def load_evidence_quality_by_pipeline_run(
        self,
        db_session: Any,
        *,
        pipeline_runs: tuple[PipelineRunCandidate, ...],
    ) -> dict[str, EvidenceQualitySummary]:
        del db_session, pipeline_runs
        self.calls.append("load_quality")
        return self.quality_by_pipeline

    def load_advice_links_by_pipeline_run(
        self,
        db_session: Any,
        *,
        pipeline_runs: tuple[PipelineRunCandidate, ...],
    ) -> dict[str, AdviceLinkSummary]:
        del db_session, pipeline_runs
        self.calls.append("load_advice")
        return self.advice_by_pipeline

    def upsert_observation(self, db_session: Any, *, payload: Any) -> tuple[Any, str]:
        del db_session
        self.upsert_calls += 1
        key = (payload.symbol, payload.base_interval, payload.higher_interval, payload.kline_slot_utc)
        action = "updated" if key in self.persisted_by_scope else "created"
        self.persisted_by_scope[key] = payload
        return SimpleNamespace(observation_id=payload.observation_id), action


def test_scheduler_pipeline_generates_canonical_observation() -> None:
    pipeline = _pipeline("SP-scheduler", trigger_source="scheduler", review_aggregation_run_id="MRAG-1")
    service = _service(
        FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)}),
        settings=_safe_settings(),
    )

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    payload = report.results[0].payload

    assert payload.canonical_pipeline_run_id == "SP-scheduler"
    assert payload.canonical_trigger_source == "scheduler"
    assert payload.observation_status == OBSERVATION_STATUS_MODEL_REVIEW_COMPLETED
    assert payload.eligible_for_review is True


def test_cli_pipeline_is_excluded_from_canonical_by_default() -> None:
    pipeline = _pipeline("SP-cli", trigger_source="cli")
    service = _service(FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)}))

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    payload = report.results[0].payload

    assert payload.canonical_pipeline_run_id is None
    assert payload.observation_status == OBSERVATION_STATUS_ONLY_CLI_RUNS
    assert payload.eligible_for_review is False
    assert payload.excluded_pipeline_run_ids[0]["reason"] == "cli_excluded_from_formal_sample"


def test_same_slot_multiple_cli_runs_is_only_cli_runs() -> None:
    pipelines = (_pipeline("SP-cli-1", trigger_source="cli"), _pipeline("SP-cli-2", trigger_source="cli"))
    service = _service(FakeObservationRepository(pipelines_by_slot={SLOT: pipelines}))

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    payload = report.results[0].payload

    assert payload.observation_status == OBSERVATION_STATUS_ONLY_CLI_RUNS
    assert payload.duplicate_pipeline_count == 2
    assert len(payload.excluded_pipeline_run_ids) == 2


def test_scheduler_plus_cli_selects_scheduler() -> None:
    scheduler = _pipeline("SP-scheduler", trigger_source="scheduler")
    cli = _pipeline("SP-cli", trigger_source="cli")
    service = _service(FakeObservationRepository(pipelines_by_slot={SLOT: (cli, scheduler)}))

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    payload = report.results[0].payload

    assert payload.canonical_pipeline_run_id == "SP-scheduler"
    assert payload.duplicate_pipeline_count == 2
    assert payload.excluded_pipeline_run_ids[0]["pipeline_run_id"] == "SP-cli"


def test_multiple_scheduler_uses_status_priority_then_created_time() -> None:
    failed_newer = _pipeline(
        "SP-failed-newer",
        trigger_source="scheduler",
        status="failed",
        created_at_utc=SLOT + timedelta(minutes=5),
    )
    advice_older = _pipeline(
        "SP-advice-older",
        trigger_source="scheduler",
        advice_id="ADV-old",
        review_id="ADVR-old",
        created_at_utc=SLOT + timedelta(minutes=1),
    )
    advice_newer = _pipeline(
        "SP-advice-newer",
        trigger_source="scheduler",
        advice_id="ADV-new",
        review_id="ADVR-new",
        created_at_utc=SLOT + timedelta(minutes=3),
    )
    service = _service(FakeObservationRepository(pipelines_by_slot={SLOT: (failed_newer, advice_older, advice_newer)}))

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    payload = report.results[0].payload

    assert payload.canonical_pipeline_run_id == "SP-advice-newer"
    assert payload.observation_status == OBSERVATION_STATUS_ADVICE_GENERATED
    assert payload.duplicate_pipeline_count == 3


def test_no_pipeline_generates_missing_pipeline_observation() -> None:
    service = _service(FakeObservationRepository())

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    payload = report.results[0].payload

    assert payload.observation_status == OBSERVATION_STATUS_MISSING_PIPELINE
    assert payload.canonical_pipeline_run_id is None
    assert payload.eligible_for_review is False


def test_26b_passed_is_written_to_observation() -> None:
    pipeline = _pipeline("SP-pass", trigger_source="scheduler")
    repo = FakeObservationRepository(
        pipelines_by_slot={SLOT: (pipeline,)},
        quality_by_pipeline={"SP-pass": EvidenceQualitySummary(quality_check_id="EQC-pass", status="passed")},
    )
    service = _service(repo)

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    payload = report.results[0].payload

    assert payload.evidence_quality_check_id == "EQC-pass"
    assert payload.evidence_quality_status == "passed"
    assert payload.evidence_quality_should_block is False


def test_26b_failed_is_quality_blocked_and_not_advice_performance_eligible() -> None:
    pipeline = _pipeline(
        "SP-quality-failed",
        trigger_source="scheduler",
        status="blocked",
        current_step=PIPELINE_STEP_STAGE26B,
        error_code="strategy_evidence_quality_failed",
    )
    repo = FakeObservationRepository(
        pipelines_by_slot={SLOT: (pipeline,)},
        quality_by_pipeline={
            "SP-quality-failed": EvidenceQualitySummary(
                quality_check_id="EQC-failed",
                status="failed",
                should_block_pipeline=True,
                failed_roles=("risk_control",),
                failed_strategies=("volatility_risk_control_strategy",),
            )
        },
    )
    service = _service(repo)

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    payload = report.results[0].payload

    assert payload.observation_status == OBSERVATION_STATUS_QUALITY_BLOCKED
    assert payload.eligible_for_review is True
    assert payload.eligible_for_advice_performance_review is False
    assert payload.evidence_quality_failed_roles == ("risk_control",)


def test_model_closed_20c_block_is_expected_model_config_block() -> None:
    pipeline = _pipeline(
        "SP-model-block",
        trigger_source="scheduler",
        status="blocked",
        current_step=PIPELINE_STEP_STAGE20,
        error_code="no_model_review_result",
    )
    service = _service(
        FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)}),
        settings=_safe_settings(),
    )

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    payload = report.results[0].payload

    assert payload.observation_status == OBSERVATION_STATUS_EXPECTED_BLOCKED_BY_MODEL_CONFIG
    assert payload.real_model_blocked_by_config is True
    assert payload.eligible_for_advice_performance_review is False


def test_failed_pipeline_is_pipeline_failed() -> None:
    pipeline = _pipeline("SP-failed", trigger_source="scheduler", status="failed", error_code="stage21_failed")
    service = _service(FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)}))

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())

    assert report.results[0].payload.observation_status == OBSERVATION_STATUS_PIPELINE_FAILED


def test_advice_existing_enables_advice_performance_review() -> None:
    pipeline = _pipeline("SP-advice", trigger_source="scheduler", advice_id="ADV-1", review_id="ADVR-1")
    service = _service(FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)}))

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    payload = report.results[0].payload

    assert payload.observation_status == OBSERVATION_STATUS_ADVICE_GENERATED
    assert payload.eligible_for_advice_performance_review is True


def test_repeated_confirm_write_updates_same_observation_without_duplicate() -> None:
    pipeline = _pipeline("SP-repeat", trigger_source="scheduler")
    repo = FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)})
    service = _service(repo)
    session = FakeSession()
    request = _request(confirm_write=True, dry_run=False)

    first = service.build_strategy_pipeline_observations(session, request=request)
    second = service.build_strategy_pipeline_observations(session, request=request)

    assert first.results[0].database_action == "created"
    assert second.results[0].database_action == "updated"
    assert len(repo.persisted_by_scope) == 1
    assert session.commits == 2


def test_26c_does_not_call_model_or_send_hermes(monkeypatch: Any) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("26C-A must not call model or Hermes")

    import app.alerting.service as alerting_service
    import app.model_analysis.service as model_analysis_service
    import app.model_review_aggregation.service as model_review_aggregation_service
    import app.model_review_chain.worker as model_review_worker

    monkeypatch.setattr(alerting_service, "send_alert", forbidden)
    monkeypatch.setattr(model_analysis_service, "run_model_analysis", forbidden)
    monkeypatch.setattr(model_review_aggregation_service, "run_model_review_aggregation", forbidden)
    monkeypatch.setattr(model_review_worker, "run_model_review_chain_worker", forbidden)
    pipeline = _pipeline("SP-safe", trigger_source="scheduler")
    service = _service(FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)}))

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())

    assert report.results[0].payload.real_model_called is False
    assert report.results[0].payload.hermes_real_sent is False


def test_26c_does_not_rerun_strategy_pipeline_stages(monkeypatch: Any) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("26C-A must not rerun 16/23F/26B/18/20/21")

    import app.strategy.aggregation.evidence_service as evidence_service
    import app.strategy.aggregation.service as aggregation_service
    import app.strategy.evidence_quality.service as quality_service
    import app.strategy.signal_service as signal_service
    import app.strategy_advice.service as advice_service
    import app.strategy_advice.scheduler_service as advice_scheduler_service

    monkeypatch.setattr(signal_service, "run_strategy_signals", forbidden)
    monkeypatch.setattr(evidence_service, "run_strategy_evidence_aggregation", forbidden)
    monkeypatch.setattr(quality_service, "run_strategy_evidence_quality_gate", forbidden)
    monkeypatch.setattr(aggregation_service, "run_strategy_aggregation", forbidden)
    monkeypatch.setattr(advice_service, "run_strategy_advice", forbidden)
    monkeypatch.setattr(advice_scheduler_service, "run_strategy_advice_scheduler", forbidden)
    pipeline = _pipeline("SP-stage-safe", trigger_source="scheduler")
    service = _service(FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)}))

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())

    assert report.results[0].payload.canonical_pipeline_run_id == "SP-stage-safe"


def test_cli_dry_run_does_not_write(capsys: Any) -> None:
    pipeline = _pipeline("SP-dry-run", trigger_source="scheduler")
    repo = FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)})
    service = _service(repo)
    session = FakeSession()

    exit_code = cli_main(
        ["--symbol", "BTCUSDT", "--base-interval", "4h", "--higher-interval", "1d", "--limit", "1"],
        service=service,
        settings=_safe_settings(),
        session_scope_factory=_fake_session_scope(session),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert repo.upsert_calls == 0
    assert session.commits == 0
    assert "dry_run=true confirm_write=false" in output
    assert "database_written：false" in output


def test_cli_confirm_write_writes(capsys: Any) -> None:
    pipeline = _pipeline("SP-confirm", trigger_source="scheduler")
    repo = FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)})
    service = _service(repo)
    session = FakeSession()

    exit_code = cli_main(
        ["--symbol", "BTCUSDT", "--base-interval", "4h", "--higher-interval", "1d", "--limit", "1", "--confirm-write"],
        service=service,
        settings=_safe_settings(),
        session_scope_factory=_fake_session_scope(session),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert repo.upsert_calls == 1
    assert session.commits == 1
    assert "dry_run=false confirm_write=true" in output
    assert "database_written：true" in output


def test_cli_parameter_error_returns_exit_code_2() -> None:
    exit_code = cli_main(["--trigger-source", "scheduler"], settings=_safe_settings())

    assert exit_code == 2


def test_output_contains_required_observation_fields() -> None:
    pipeline = _pipeline("SP-output", trigger_source="scheduler", advice_id="ADV-output", review_id="ADVR-output")
    service = _service(FakeObservationRepository(pipelines_by_slot={SLOT: (pipeline,)}))

    report = service.build_strategy_pipeline_observations(FakeSession(), request=_request())
    output = "\n".join(format_strategy_pipeline_observation_report_lines(report))

    assert "canonical_pipeline_run_id：SP-output" in output
    assert "observation_status：advice_generated" in output
    assert "eligible_for_review：true" in output
    assert "eligible_for_advice_performance_review：true" in output
    assert "26B 状态" in output
    assert "模型状态" in output
    assert "advice 状态" in output
    assert "duplicate_pipeline_count" in output
    assert "excluded pipeline 数量" in output
    assert "不自动交易，不读取账户，不生成订单" in output


def _service(repository: FakeObservationRepository, *, settings: AppSettings | None = None) -> StrategyPipelineObservationService:
    return StrategyPipelineObservationService(settings=settings or _safe_settings(), repository=repository)


def _request(*, confirm_write: bool = False, dry_run: bool = True) -> StrategyPipelineObservationBuildRequest:
    return StrategyPipelineObservationBuildRequest(
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        limit=10,
        dry_run=dry_run,
        confirm_write=confirm_write,
        trigger_source="cli",
    )


def _slot(value: datetime) -> KlineSlotObservationSource:
    return KlineSlotObservationSource(open_time_utc=value)


def _pipeline(
    pipeline_run_id: str,
    *,
    trigger_source: str,
    status: str = "success",
    current_step: str | None = None,
    error_code: str | None = None,
    advice_id: str | None = None,
    review_id: str | None = None,
    review_aggregation_run_id: str | None = "MRAG-test",
    created_at_utc: datetime | None = None,
) -> PipelineRunCandidate:
    return PipelineRunCandidate(
        pipeline_run_id=pipeline_run_id,
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        kline_slot_utc=SLOT,
        trigger_source=trigger_source,
        status=status,
        current_step=current_step,
        strategy_signal_run_id=f"SSR-{pipeline_run_id}",
        strategy_evidence_aggregation_id=f"SEA-{pipeline_run_id}",
        material_pack_id=f"AMP-{pipeline_run_id}",
        model_analysis_run_id=f"MAR-{pipeline_run_id}" if review_aggregation_run_id else None,
        review_aggregation_run_id=review_aggregation_run_id,
        advice_id=advice_id,
        review_id=review_id,
        notification_status=None,
        model_review_invoked=bool(review_aggregation_run_id),
        model_review_reused=False,
        real_model_called=False,
        hermes_real_sent=False,
        error_code=error_code,
        error_message="test error" if error_code else None,
        created_at_utc=created_at_utc or SLOT,
        updated_at_utc=created_at_utc or SLOT,
        id=abs(hash(pipeline_run_id)) % 100000,
    )


def _safe_settings() -> AppSettings:
    return AppSettings(
        strategy_pipeline_real_model_enabled=False,
        strategy_pipeline_confirm_real_model_cost=False,
        model_review_real_model_enabled=False,
        strategy_pipeline_notification_send_enabled=False,
        strategy_advice_notification_send_enabled=False,
    )


def _fake_session_scope(session: FakeSession) -> Any:
    @contextmanager
    def _scope(*args: Any, **kwargs: Any) -> Iterator[FakeSession]:
        del args, kwargs
        yield session

    return _scope
