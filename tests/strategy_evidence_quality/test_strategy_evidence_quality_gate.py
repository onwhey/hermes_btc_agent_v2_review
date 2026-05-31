from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.core.config import AppSettings
from app.strategy.evidence_quality.service import (
    StrategyEvidenceQualityConfigProvider,
    StrategyEvidenceQualityGateService,
)
from app.strategy.evidence_quality.types import (
    EXIT_PARAMETER_OR_DATABASE_ERROR,
    EXIT_SUCCESS,
    STRATEGY_EVIDENCE_QUALITY_ERROR_CODE,
    NormalOperatingStrategyDefinition,
    StrategyEvidenceQualityGateRequest,
    StrategyEvidenceQualityQueryReport,
    StrategyEvidenceQualityQueryRequest,
    StrategyEvidenceQualityRowSummary,
    StrategyEvidenceQualityStatus,
)
from scripts import check_strategy_evidence_quality as quality_cli

SLOT = datetime(2026, 5, 30, 4, 0, tzinfo=timezone.utc)


@dataclass
class FakeSignalRun:
    run_id: str = "SSR-test"


@dataclass
class FakeAggregation:
    aggregation_id: str = "SEA-test"
    strategy_signal_run_id: str = "SSR-test"
    symbol: str = "BTCUSDT"
    base_interval: str = "4h"
    higher_interval: str = "1d"
    status: str = "success"
    role_coverage_matrix_json: str = "{}"


@dataclass
class FakeStrategyResultRow:
    strategy_name: str
    strategy_role: str
    common_payload_json: str
    strategy_status: str = "success"
    validation_status: str = "passed"
    run_id: str = "SSR-test"


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class FakeQualityRepository:
    def __init__(
        self,
        *,
        aggregation: FakeAggregation | None = None,
        rows: tuple[FakeStrategyResultRow, ...] = (),
        signal_run: FakeSignalRun | None = None,
        existing_quality: Any | None = None,
        query_rows: tuple[StrategyEvidenceQualityRowSummary, ...] = (),
    ) -> None:
        self.aggregation = aggregation or FakeAggregation(role_coverage_matrix_json=_role_matrix())
        self.rows = rows
        self.signal_run = signal_run or FakeSignalRun()
        self.existing_quality = existing_quality
        self.query_rows = query_rows
        self.persisted_payloads: list[Any] = []
        self.persisted_rows: dict[tuple[str | None, str], Any] = {}
        self.alert_updates: list[tuple[str, int | None]] = []

    def get_strategy_signal_run(self, db_session: Any, *, run_id: str) -> Any | None:
        return self.signal_run

    def get_strategy_evidence_aggregation(self, db_session: Any, *, aggregation_id: str) -> Any | None:
        return self.aggregation

    def list_strategy_signal_results(self, db_session: Any, *, run_id: str) -> tuple[Any, ...]:
        return self.rows

    def get_existing_quality_check(
        self,
        db_session: Any,
        *,
        pipeline_run_id: str | None,
        evidence_aggregation_id: str | None = None,
        trigger_source: str,
    ) -> Any:
        if self.existing_quality is not None:
            return self.existing_quality
        return self.persisted_rows.get((pipeline_run_id, trigger_source))

    def upsert_quality_check_result(self, db_session: Any, *, payload: Any) -> tuple[Any, str]:
        self.persisted_payloads.append(payload)
        key = (payload.pipeline_run_id, payload.trigger_source)
        action = "updated" if key in self.persisted_rows else "created"
        row = self.persisted_rows.get(key) or SimpleNamespace()
        for name, value in payload.__dict__.items():
            setattr(row, name, value)
        row.quality_check_id = payload.quality_check_id
        row.alert_status = payload.alert_status
        row.alert_message_id = payload.alert_message_id
        self.persisted_rows[key] = row
        return (
            row,
            action,
        )

    def update_quality_alert_status(
        self,
        db_session: Any,
        *,
        quality_check_id: str,
        alert_status: str,
        alert_message_id: int | None = None,
    ) -> Any:
        self.alert_updates.append((alert_status, alert_message_id))
        return SimpleNamespace(quality_check_id=quality_check_id, alert_status=alert_status)

    def list_quality_check_results(self, db_session: Any, *, request: StrategyEvidenceQualityQueryRequest) -> tuple[Any, ...]:
        return self.query_rows


class FakeConfigProvider:
    def __init__(
        self,
        *,
        active: tuple[NormalOperatingStrategyDefinition, ...],
        required_roles: tuple[str, ...] = ("context", "support_resistance", "filter", "risk_control"),
        required_role_provides: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._active = active
        self._required_roles = required_roles
        self._required_role_provides = required_role_provides or {
            "context": ("primary_regime", "market_environment_context"),
            "support_resistance": ("key_levels",),
            "filter": ("trigger_state",),
            "risk_control": ("risk_gate_decision",),
        }

    def list_normal_operating_strategies(self) -> tuple[NormalOperatingStrategyDefinition, ...]:
        return self._active

    def required_roles(self) -> tuple[str, ...]:
        return self._required_roles

    def required_role_provides(self) -> dict[str, tuple[str, ...]]:
        return self._required_role_provides


class FakeAlertDispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[Any] = []

    def __call__(self, db_session: Any, *, quality_result: Any, settings: Any, send_real_alert: bool) -> Any:
        self.calls.append(quality_result)
        if self.fail:
            raise RuntimeError("Hermes failed")
        return SimpleNamespace(alert_status="submitted_to_hermes", alert_message_id=7, error_message=None)


def test_active_decision_participant_strategy_missing_blocks() -> None:
    result, repo, alert = _run_gate(active=(_strategy("market_direction_regime", "context"),), rows=())

    assert result.status == StrategyEvidenceQualityStatus.FAILED
    assert result.should_block_pipeline is True
    assert result.error_code == STRATEGY_EVIDENCE_QUALITY_ERROR_CODE
    assert "market_direction_regime" in result.failed_strategies
    assert repo.persisted_payloads[-1].should_block_pipeline is True
    assert len(alert.calls) == 1


def test_active_can_veto_risk_strategy_missing_blocks() -> None:
    active = (_strategy("volatility_risk_control_strategy", "risk_control", mode="observe_only", can_veto=True),)
    result, _, _ = _run_gate(active=active, rows=())

    assert result.should_block_pipeline is True
    assert "volatility_risk_control_strategy" in result.failed_strategies


def test_active_strategy_failed_or_invalid_status_blocks() -> None:
    failed_row = _row("market_direction_regime", "context", _context_payload(), strategy_status="failed")
    invalid_row = _row("market_direction_regime", "context", _context_payload(), strategy_status="invalid")

    failed_result, _, _ = _run_gate(active=(_strategy("market_direction_regime", "context"),), rows=(failed_row,))
    invalid_result, _, _ = _run_gate(active=(_strategy("market_direction_regime", "context"),), rows=(invalid_row,))

    assert failed_result.should_block_pipeline is True
    assert invalid_result.should_block_pipeline is True
    assert any(issue.error_code == "active_strategy_status_failed" for issue in failed_result.failed_checks)
    assert any(issue.error_code == "active_strategy_status_invalid" for issue in invalid_result.failed_checks)


def test_common_payload_json_parse_failure_blocks() -> None:
    row = FakeStrategyResultRow(
        strategy_name="support_resistance_strategy",
        strategy_role="support_resistance",
        common_payload_json="{not json",
    )
    result, _, _ = _run_gate(active=(_strategy("support_resistance_strategy", "support_resistance"),), rows=(row,))

    assert result.should_block_pipeline is True
    assert any(issue.error_code == "common_payload_json_parse_failed" for issue in result.failed_checks)


def test_support_resistance_missing_key_levels_blocks() -> None:
    row = _row("support_resistance_strategy", "support_resistance", {})
    result, _, _ = _run_gate(active=(_strategy("support_resistance_strategy", "support_resistance"),), rows=(row,))

    assert result.should_block_pipeline is True
    assert "support_resistance.key_levels" in result.missing_fields


def test_filter_missing_trigger_state_blocks() -> None:
    row = _row("breakout_pullback_trigger_strategy", "filter", {})
    result, _, _ = _run_gate(active=(_strategy("breakout_pullback_trigger_strategy", "filter"),), rows=(row,))

    assert result.should_block_pipeline is True
    assert "filter.trigger_state" in result.missing_fields


def test_risk_control_missing_risk_gate_decision_blocks() -> None:
    row = _row("volatility_risk_control_strategy", "risk_control", {})
    active = (_strategy("volatility_risk_control_strategy", "risk_control", can_veto=True),)
    result, _, _ = _run_gate(active=active, rows=(row,))

    assert result.should_block_pipeline is True
    assert "risk_control.risk_gate_decision" in result.missing_fields


def test_gann_placeholder_observe_only_zero_weight_is_not_normal_operating_strategy() -> None:
    names = {
        item.strategy_name
        for item in StrategyEvidenceQualityConfigProvider().list_normal_operating_strategies()
    }

    assert "gann_placeholder" not in names
    assert "volatility_risk_control_strategy" in names


def test_experimental_or_internship_strategy_missing_does_not_block_when_not_active_required() -> None:
    result, repo, alert = _run_gate(active=(), rows=(), required_roles=())

    assert result.should_block_pipeline is False
    assert result.status == StrategyEvidenceQualityStatus.PASSED
    assert repo.persisted_payloads[-1].status == "passed"
    assert alert.calls == []


def test_required_role_missing_in_aggregation_blocks() -> None:
    aggregation = FakeAggregation(role_coverage_matrix_json=_role_matrix(covered={"context": False}))
    result, _, _ = _run_gate(
        active=(_strategy("market_direction_regime", "context"),),
        rows=(_row("market_direction_regime", "context", _context_payload()),),
        aggregation=aggregation,
    )

    assert result.should_block_pipeline is True
    assert "context" in result.failed_roles
    assert any(issue.error_code == "required_role_missing" for issue in result.failed_checks)


def test_hermes_alert_failure_does_not_rollback_quality_result() -> None:
    alert = FakeAlertDispatcher(fail=True)
    session = FakeSession()
    result, repo, _ = _run_gate(
        active=(_strategy("market_direction_regime", "context"),),
        rows=(),
        session=session,
        alert=alert,
    )

    assert result.should_block_pipeline is True
    assert result.alert_status == "submit_failed"
    assert result.alert_error_message == "Hermes failed"
    assert repo.persisted_payloads
    assert ("submit_failed", None) in repo.alert_updates
    assert session.commits >= 2


def test_gate_disabled_records_skipped_and_does_not_block_or_alert() -> None:
    alert = FakeAlertDispatcher()
    result, repo, _ = _run_gate(
        active=(_strategy("market_direction_regime", "context"),),
        rows=(),
        settings=AppSettings(
            strategy_evidence_quality_gate_enabled=False,
            strategy_evidence_quality_gate_alert_enabled=True,
        ),
        alert=alert,
    )

    assert result.should_block_pipeline is False
    assert result.status == StrategyEvidenceQualityStatus.WARNING
    assert result.details["gate_skipped_by_config"] is True
    assert repo.persisted_payloads[-1].error_code == "gate_skipped_by_config"
    assert alert.calls == []


def test_same_sea_different_pipeline_ids_create_independent_quality_records() -> None:
    repo = FakeQualityRepository()

    passed_result, _, _ = _run_gate(
        active=(),
        rows=(),
        required_roles=(),
        repo=repo,
        pipeline_run_id="SP-pass",
    )
    failed_result, _, _ = _run_gate(
        active=(_strategy("market_direction_regime", "context"),),
        rows=(),
        repo=repo,
        pipeline_run_id="SP-failed",
    )

    assert passed_result.status == StrategyEvidenceQualityStatus.PASSED
    assert failed_result.status == StrategyEvidenceQualityStatus.FAILED
    assert len(repo.persisted_rows) == 2
    assert repo.persisted_rows[("SP-pass", "pipeline")].status == "passed"
    assert repo.persisted_rows[("SP-failed", "pipeline")].status == "failed"
    assert passed_result.quality_check_id == "EQC-SP-pass"
    assert failed_result.quality_check_id == "EQC-SP-failed"


def test_quality_check_id_uses_current_pipeline_run_id_when_sea_is_reused() -> None:
    repo = FakeQualityRepository()

    first_result, _, _ = _run_gate(
        active=(),
        rows=(),
        required_roles=(),
        repo=repo,
        pipeline_run_id="SP-BTCUSDT-4H-1D-first",
    )
    second_result, _, _ = _run_gate(
        active=(),
        rows=(),
        required_roles=(),
        repo=repo,
        pipeline_run_id="SP-BTCUSDT-4H-1D-second",
    )

    assert first_result.strategy_evidence_aggregation_id == "SEA-test"
    assert second_result.strategy_evidence_aggregation_id == "SEA-test"
    assert first_result.quality_check_id == "EQC-SP-BTCUSDT-4H-1D-first"
    assert second_result.quality_check_id == "EQC-SP-BTCUSDT-4H-1D-second"
    assert first_result.quality_check_id != second_result.quality_check_id


def test_same_pipeline_run_id_reuses_one_quality_record_idempotently() -> None:
    repo = FakeQualityRepository()

    first_result, _, _ = _run_gate(
        active=(),
        rows=(),
        required_roles=(),
        repo=repo,
        pipeline_run_id="SP-repeat",
        trace_id="trace-first",
    )
    second_result, _, _ = _run_gate(
        active=(),
        rows=(),
        required_roles=(),
        repo=repo,
        pipeline_run_id="SP-repeat",
        trace_id="trace-second",
    )

    assert len(repo.persisted_rows) == 1
    assert first_result.quality_check_id == "EQC-SP-repeat"
    assert second_result.quality_check_id == "EQC-SP-repeat"
    assert first_result.database_action == "created"
    assert second_result.database_action == "updated"


def test_existing_legacy_quality_check_id_is_corrected_to_current_pipeline_id() -> None:
    repo = FakeQualityRepository()
    repo.persisted_rows[("SP-current", "pipeline")] = SimpleNamespace(
        quality_check_id="EQC-SP-old",
        alert_status="not_required",
        alert_message_id=None,
    )

    result, _, _ = _run_gate(
        active=(),
        rows=(),
        required_roles=(),
        repo=repo,
        pipeline_run_id="SP-current",
    )

    assert result.database_action == "updated"
    assert result.quality_check_id == "EQC-SP-current"
    assert repo.persisted_rows[("SP-current", "pipeline")].quality_check_id == "EQC-SP-current"


def test_cli_is_read_only_query_and_does_not_write_or_send_hermes(capsys: Any) -> None:
    row = StrategyEvidenceQualityRowSummary(
        quality_check_id="EQC-1",
        pipeline_run_id="SP-1",
        strategy_signal_run_id="SSR-1",
        evidence_aggregation_id="SEA-1",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        kline_slot_utc=SLOT,
        status="passed",
        severity="info",
        should_block_pipeline=False,
        error_code=None,
        error_message=None,
        alert_status="not_required",
        alert_message_id=None,
        trace_id="trace-cli",
    )
    service = SimpleNamespace(
        calls=[],
        query_strategy_evidence_quality_results=lambda db_session, request: StrategyEvidenceQualityQueryReport(
            request=request,
            rows=(row,),
            exit_code=EXIT_SUCCESS,
        ),
    )

    exit_code = quality_cli.main(
        ["--symbol", "BTCUSDT", "--base-interval", "4h", "--higher-interval", "1d", "--limit", "5"],
        service=service,
        settings=AppSettings(),
        session_scope_factory=_fake_session_scope_factory,
    )

    captured = capsys.readouterr().out
    assert exit_code == EXIT_SUCCESS
    assert "quality_check_id=EQC-1" in captured
    assert "不自动交易，不读取账户，不生成订单" in captured


def test_cli_parameter_error_returns_exit_code_2() -> None:
    exit_code = quality_cli.main(["--base-interval", "1h"], settings=AppSettings())

    assert exit_code == EXIT_PARAMETER_OR_DATABASE_ERROR


def _run_gate(
    *,
    active: tuple[NormalOperatingStrategyDefinition, ...],
    rows: tuple[FakeStrategyResultRow, ...],
    required_roles: tuple[str, ...] = ("context", "support_resistance", "filter", "risk_control"),
    aggregation: FakeAggregation | None = None,
    settings: AppSettings | None = None,
    session: FakeSession | None = None,
    alert: FakeAlertDispatcher | None = None,
    repo: FakeQualityRepository | None = None,
    pipeline_run_id: str = "SP-test",
    strategy_evidence_aggregation_id: str = "SEA-test",
    trace_id: str = "trace-test",
) -> tuple[Any, FakeQualityRepository, FakeAlertDispatcher]:
    active_alert = alert or FakeAlertDispatcher()
    active_repo = repo or FakeQualityRepository(aggregation=aggregation, rows=rows)
    active_repo.aggregation = aggregation or active_repo.aggregation
    active_repo.rows = rows
    service = StrategyEvidenceQualityGateService(
        settings=settings or AppSettings(
            strategy_evidence_quality_gate_enabled=True,
            strategy_evidence_quality_gate_alert_enabled=True,
        ),
        repository=active_repo,
        config_provider=FakeConfigProvider(active=active, required_roles=required_roles),
        alert_dispatcher=active_alert,
    )
    result = service.run_strategy_evidence_quality_gate(
        session or FakeSession(),
        request=StrategyEvidenceQualityGateRequest(
            pipeline_run_id=pipeline_run_id,
            strategy_signal_run_id="SSR-test",
            strategy_evidence_aggregation_id=strategy_evidence_aggregation_id,
            symbol="BTCUSDT",
            base_interval="4h",
            higher_interval="1d",
            kline_slot_utc=SLOT,
            trace_id=trace_id,
        ),
    )
    return result, active_repo, active_alert


def _strategy(
    name: str,
    role: str,
    *,
    mode: str = "decision_participant",
    can_veto: bool = False,
) -> NormalOperatingStrategyDefinition:
    return NormalOperatingStrategyDefinition(
        strategy_name=name,
        strategy_role=role,
        provides=(),
        maturity_stage="active",
        participation_mode=mode,
        decision_weight="1",
        can_veto=can_veto,
    )


def _row(name: str, role: str, payload: dict[str, Any], *, strategy_status: str = "success") -> FakeStrategyResultRow:
    return FakeStrategyResultRow(
        strategy_name=name,
        strategy_role=role,
        common_payload_json=json.dumps(payload, ensure_ascii=False),
        strategy_status=strategy_status,
    )


def _context_payload() -> dict[str, Any]:
    return {"primary_regime": "uptrend", "market_environment_context": "uptrend context"}


def _role_matrix(*, covered: dict[str, bool] | None = None) -> str:
    flags = covered or {}
    role_provides = {
        "context": ["primary_regime", "market_environment_context"],
        "support_resistance": ["key_levels"],
        "filter": ["trigger_state"],
        "risk_control": ["risk_gate_decision"],
    }
    roles = {}
    for role, provides in role_provides.items():
        is_covered = flags.get(role, True)
        roles[role] = {
            "role": role,
            "covered": is_covered,
            "provided": provides if is_covered else [],
            "missing_provides": [] if is_covered else provides,
            "effective_coverage_count": 1 if is_covered else 0,
        }
    return json.dumps({"roles": roles}, ensure_ascii=False)


@contextmanager
def _fake_session_scope_factory(**_: Any) -> Any:
    yield FakeSession()
