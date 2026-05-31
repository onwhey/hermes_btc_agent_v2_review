from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from app.core.config import AppSettings
from app.strategy_observability.service import StrategyPipelineObservabilityService
from app.strategy_observability.types import (
    EXIT_PARAMETER_OR_DATABASE_ERROR,
    EXIT_SUCCESS,
    EXIT_UNHEALTHY,
    KlineSlotRecord,
    SlotObservationStatus,
    StrategyPipelineLinkRecord,
    StrategyPipelineRunRecord,
    StrategyPipelineStatusRequest,
    format_strategy_pipeline_status_report_lines,
)
from app.strategy_pipeline.types import PIPELINE_STEP_STAGE20
from scripts.check_strategy_pipeline_status import main as cli_main

SLOT = datetime(2026, 5, 31, 4, 0, tzinfo=timezone.utc)


class FakeRepository:
    """In-memory read-only repository for 26A tests.

    It never calls model providers, Hermes, Redis, Binance, or trading APIs.
    """

    def __init__(
        self,
        *,
        slots: tuple[KlineSlotRecord, ...] | None = None,
        pipelines_by_slot: dict[datetime, tuple[StrategyPipelineRunRecord, ...]] | None = None,
        links_by_pipeline_id: dict[str, StrategyPipelineLinkRecord] | None = None,
    ) -> None:
        self.slots = slots or (_slot(SLOT),)
        self.pipelines_by_slot = pipelines_by_slot or {}
        self.links_by_pipeline_id = links_by_pipeline_id or {}
        self.calls: list[str] = []

    def list_recent_closed_kline_slots(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        limit: int,
    ) -> tuple[KlineSlotRecord, ...]:
        del db_session, symbol, base_interval
        self.calls.append("list_slots")
        return self.slots[:limit]

    def list_pipeline_runs_for_slots(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        slots: tuple[KlineSlotRecord, ...],
    ) -> dict[datetime, tuple[StrategyPipelineRunRecord, ...]]:
        del db_session, symbol, base_interval, higher_interval, slots
        self.calls.append("list_pipelines")
        return self.pipelines_by_slot

    def load_link_records_for_pipeline_runs(
        self,
        db_session: Any,
        *,
        pipeline_runs: tuple[StrategyPipelineRunRecord, ...],
    ) -> dict[str, StrategyPipelineLinkRecord]:
        del db_session, pipeline_runs
        self.calls.append("load_links")
        return self.links_by_pipeline_id


class FakeSession:
    pass


def test_safe_mode_no_model_review_result_is_expected_blocked() -> None:
    pipeline = _pipeline(status="blocked", current_step=PIPELINE_STEP_STAGE20, error_code="no_model_review_result")
    service = _service(
        repository=FakeRepository(
            pipelines_by_slot={SLOT: (pipeline,)},
            links_by_pipeline_id={pipeline.pipeline_run_id: _links(advr_id=None, advr_exists=False)},
        ),
        settings=_settings_safe_mode(),
    )

    report = service.check_strategy_pipeline_status(FakeSession(), request=StrategyPipelineStatusRequest())

    assert report.exit_code == EXIT_SUCCESS
    assert report.observations[0].status == SlotObservationStatus.EXPECTED_BLOCKED
    assert report.observations[0].blocked_reasonable is True


def test_real_model_enabled_no_model_review_result_is_failed() -> None:
    pipeline = _pipeline(status="blocked", current_step=PIPELINE_STEP_STAGE20, error_code="no_model_review_result")
    service = _service(
        repository=FakeRepository(
            pipelines_by_slot={SLOT: (pipeline,)},
            links_by_pipeline_id={pipeline.pipeline_run_id: _links(advr_id=None, advr_exists=False)},
        ),
        settings=_settings_real_model_enabled(),
    )

    report = service.check_strategy_pipeline_status(FakeSession(), request=StrategyPipelineStatusRequest())

    assert report.exit_code == EXIT_UNHEALTHY
    assert report.observations[0].status == SlotObservationStatus.FAILED
    assert report.observations[0].blocked_reasonable is False


def test_existing_kline_without_pipeline_is_missing() -> None:
    service = _service(repository=FakeRepository(), settings=_settings_safe_mode())

    report = service.check_strategy_pipeline_status(FakeSession(), request=StrategyPipelineStatusRequest())

    assert report.exit_code == EXIT_UNHEALTHY
    assert report.observations[0].status == SlotObservationStatus.MISSING


def test_same_slot_multiple_pipelines_is_duplicate() -> None:
    first = _pipeline(pipeline_run_id="SP-first")
    second = _pipeline(pipeline_run_id="SP-second")
    service = _service(
        repository=FakeRepository(
            pipelines_by_slot={SLOT: (first, second)},
            links_by_pipeline_id={"SP-first": _links(pipeline_run_id="SP-first")},
        ),
        settings=_settings_safe_mode(),
    )

    report = service.check_strategy_pipeline_status(FakeSession(), request=StrategyPipelineStatusRequest())

    assert report.exit_code == EXIT_UNHEALTHY
    assert report.observations[0].status == SlotObservationStatus.DUPLICATE
    assert report.observations[0].pipeline_run_ids == ("SP-first", "SP-second")


def test_pipeline_failed_is_failed() -> None:
    pipeline = _pipeline(status="failed", error_code="stage21_failed")
    service = _service(
        repository=FakeRepository(
            pipelines_by_slot={SLOT: (pipeline,)},
            links_by_pipeline_id={pipeline.pipeline_run_id: _links()},
        ),
        settings=_settings_safe_mode(),
    )

    report = service.check_strategy_pipeline_status(FakeSession(), request=StrategyPipelineStatusRequest())

    assert report.exit_code == EXIT_UNHEALTHY
    assert report.observations[0].status == SlotObservationStatus.FAILED


def test_output_contains_key_ids_and_model_hermes_flags() -> None:
    pipeline = _pipeline(status="success", real_model_called=False, hermes_real_sent=False)
    service = _service(
        repository=FakeRepository(
            pipelines_by_slot={SLOT: (pipeline,)},
            links_by_pipeline_id={pipeline.pipeline_run_id: _links()},
        ),
        settings=_settings_safe_mode(),
    )

    report = service.check_strategy_pipeline_status(FakeSession(), request=StrategyPipelineStatusRequest())
    output = "\n".join(format_strategy_pipeline_status_report_lines(report))

    assert "SP / pipeline_run_id：SP-test" in output
    assert "SSR：存在 SSR-test" in output
    assert "SEA：存在 SEA-test" in output
    assert "AMP：存在 AMP-test" in output
    assert "MRAG：存在 MRAG-test" in output
    assert "ADVR：存在 ADVR-test" in output
    assert "real_model_called：false" in output
    assert "hermes_real_sent：false" in output
    assert "本检查只用于策略链路运行观测，不是交易建议；不自动交易，不读取账户，不生成订单。" in output


def test_cli_parameter_error_returns_exit_code_2() -> None:
    exit_code = cli_main(["--limit", "0"], settings=_settings_safe_mode())

    assert exit_code == EXIT_PARAMETER_OR_DATABASE_ERROR


def test_observability_does_not_call_real_model_or_send_hermes(monkeypatch: Any) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("26A observability must not call model or Hermes paths")

    import app.alerting.service as alerting_service
    import app.model_analysis.service as model_analysis_service

    monkeypatch.setattr(alerting_service, "send_alert", forbidden)
    monkeypatch.setattr(model_analysis_service, "run_model_analysis", forbidden)
    pipeline = _pipeline(status="success", real_model_called=False, hermes_real_sent=False)
    service = _service(
        repository=FakeRepository(
            pipelines_by_slot={SLOT: (pipeline,)},
            links_by_pipeline_id={pipeline.pipeline_run_id: _links()},
        ),
        settings=_settings_safe_mode(),
    )

    report = service.check_strategy_pipeline_status(FakeSession(), request=StrategyPipelineStatusRequest())

    assert report.observations[0].real_model_called is False
    assert report.observations[0].hermes_real_sent is False


def test_cli_uses_read_only_service_and_returns_report_exit_code() -> None:
    pipeline = _pipeline(status="success")
    fake_service = _service(
        repository=FakeRepository(
            pipelines_by_slot={SLOT: (pipeline,)},
            links_by_pipeline_id={pipeline.pipeline_run_id: _links()},
        ),
        settings=_settings_safe_mode(),
    )

    exit_code = cli_main(
        ["--symbol", "BTCUSDT", "--base-interval", "4h", "--higher-interval", "1d", "--limit", "1"],
        service=fake_service,
        settings=_settings_safe_mode(),
        session_scope_factory=_fake_session_scope,
    )

    assert exit_code == EXIT_SUCCESS


def _service(*, repository: FakeRepository, settings: AppSettings) -> StrategyPipelineObservabilityService:
    return StrategyPipelineObservabilityService(settings=settings, repository=repository)


def _slot(value: datetime) -> KlineSlotRecord:
    return KlineSlotRecord(open_time_utc=value, open_time_ms=1_779_940_800_000)


def _pipeline(
    *,
    pipeline_run_id: str = "SP-test",
    status: str = "success",
    current_step: str | None = "21a_21b_advice_notification",
    error_code: str | None = None,
    real_model_called: bool = False,
    hermes_real_sent: bool = False,
) -> StrategyPipelineRunRecord:
    return StrategyPipelineRunRecord(
        pipeline_run_id=pipeline_run_id,
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        kline_slot_utc=SLOT,
        status=status,
        current_step=current_step,
        strategy_signal_run_id="SSR-test",
        strategy_evidence_aggregation_id="SEA-test",
        material_pack_id="AMP-test",
        review_aggregation_run_id="MRAG-test",
        advice_id="ADV-test",
        review_id="ADVR-test",
        notification_status="skipped",
        real_model_called=real_model_called,
        hermes_real_sent=hermes_real_sent,
        error_code=error_code,
        error_message="MODEL_REVIEW_REAL_MODEL_ENABLED=false" if error_code else None,
    )


def _links(
    *,
    pipeline_run_id: str = "SP-test",
    advr_id: str | None = "ADVR-test",
    advr_exists: bool = True,
) -> StrategyPipelineLinkRecord:
    return StrategyPipelineLinkRecord(
        pipeline_run_id=pipeline_run_id,
        strategy_signal_run_id="SSR-test",
        strategy_signal_run_exists=True,
        strategy_evidence_aggregation_id="SEA-test",
        strategy_evidence_aggregation_exists=True,
        material_pack_id="AMP-test",
        material_pack_exists=True,
        review_aggregation_run_id="MRAG-test",
        review_aggregation_run_exists=True,
        advice_lifecycle_review_id=advr_id,
        advice_lifecycle_review_exists=advr_exists,
    )


def _settings_safe_mode() -> AppSettings:
    return AppSettings(
        strategy_pipeline_enabled=True,
        strategy_pipeline_real_model_enabled=False,
        strategy_pipeline_confirm_real_model_cost=False,
        model_review_real_model_enabled=False,
        strategy_pipeline_notification_send_enabled=False,
        strategy_advice_notification_send_enabled=False,
    )


def _settings_real_model_enabled() -> AppSettings:
    return AppSettings(
        strategy_pipeline_enabled=True,
        strategy_pipeline_real_model_enabled=True,
        strategy_pipeline_confirm_real_model_cost=True,
        model_review_real_model_enabled=True,
        strategy_pipeline_notification_send_enabled=False,
        strategy_advice_notification_send_enabled=False,
    )


@contextmanager
def _fake_session_scope(*args: Any, **kwargs: Any) -> Iterator[FakeSession]:
    del args, kwargs
    yield FakeSession()
