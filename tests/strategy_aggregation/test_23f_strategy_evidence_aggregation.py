from __future__ import annotations

import json
import inspect
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.strategy.aggregation.evidence_aggregator import StrategyEvidenceAggregator
from app.strategy.aggregation.evidence_config import EvidenceAggregationConfig, StrategyGovernanceProvider
from app.strategy.aggregation.evidence_service import StrategyEvidenceAggregationService
from app.strategy.aggregation.evidence_types import (
    CandidateBias,
    DecisionReadiness,
    EvidenceAggregationRequest,
    ParticipationMode,
    StrategyGovernance,
)
from app.strategy.aggregation.material_builder import build_material_pack
from app.strategy.aggregation.types import (
    AggregationDecision,
    AggregationRiskLevel,
    AnalysisHypothesisConfidence,
    AnalysisHypothesisDirection,
    ConflictLevel,
    RiskGateStatus,
    StrategyVoteSummary,
)
from scripts import run_strategy_evidence_aggregation as evidence_cli


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class PoisonPrivatePayloadRow:
    def __init__(
        self,
        strategy_name: str,
        *,
        strategy_role: str,
        common_payload: dict[str, Any],
        strategy_status: str = "success",
        validation_status: str | None = "passed",
        signal_strength: str = "0.80",
    ) -> None:
        self.id = len(strategy_name)
        self.run_id = "SSR-23F"
        self.strategy_name = strategy_name
        self.strategy_version = "v1"
        self.strategy_role = strategy_role
        self.strategy_status = strategy_status
        self.validation_status = validation_status
        self.common_payload_json = json.dumps(common_payload, ensure_ascii=False)
        self.reason_text = common_payload.get("reason_text", "")
        self.signal_strength = Decimal(signal_strength)

    @property
    def strategy_payload_json(self) -> str:
        raise AssertionError("23F must not read strategy_payload_json")


class FakeGovernanceProvider:
    def __init__(
        self,
        governance: dict[str, StrategyGovernance],
        *,
        required_roles: tuple[str, ...] = (),
        required_role_provides: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.governance = governance
        self.config = EvidenceAggregationConfig(
            required_roles=required_roles,
            required_role_provides=required_role_provides or {},
            default_governance=StrategyGovernance(
                strategy_name="default",
                strategy_role="",
                participation_mode=ParticipationMode.OBSERVE_ONLY.value,
                decision_weight=Decimal("0"),
            ),
        )

    def get_aggregation_config(self) -> EvidenceAggregationConfig:
        return self.config

    def get_strategy_governance(self, *, strategy_name: str, strategy_role: str | None = None) -> StrategyGovernance:
        return self.governance.get(
            strategy_name,
            StrategyGovernance(
                strategy_name=strategy_name,
                strategy_role=strategy_role or "",
                participation_mode=ParticipationMode.OBSERVE_ONLY.value,
                decision_weight=Decimal("0"),
            ),
        )


class FakeEvidenceRepository:
    def __init__(self, *, rows: tuple[Any, ...], run: Any | None = None) -> None:
        self.run = run or strategy_run()
        self.rows = rows
        self.persisted: list[Any] = []
        self.result_read_count = 0

    def get_strategy_signal_run(self, _session: Any, *, run_id: str) -> Any | None:
        return self.run if self.run.run_id == run_id else None

    def list_public_strategy_signal_results(self, _session: Any, *, run_id: str) -> tuple[Any, ...]:
        self.result_read_count += 1
        return self.rows if self.run.run_id == run_id else ()

    def get_existing_aggregation(self, _session: Any, *, strategy_signal_run_id: str) -> Any | None:
        for row in self.persisted:
            if row.strategy_signal_run_id == strategy_signal_run_id:
                return row
        return None

    def upsert_aggregation_result(self, _session: Any, *, payload: Any) -> tuple[Any, str]:
        existing = self.get_existing_aggregation(_session, strategy_signal_run_id=payload.aggregation.strategy_signal_run_id)
        if existing is None:
            row = SimpleNamespace(**payload.aggregation.to_jsonable())
            row.strategy_evidence_summary_json = json.dumps(payload.aggregation.strategy_evidence_summary, ensure_ascii=False)
            row.decision_source_chain_json = json.dumps(list(payload.aggregation.decision_source_chain), ensure_ascii=False)
            row.model_review_focus_json = json.dumps(payload.aggregation.model_review_focus, ensure_ascii=False)
            self.persisted.append(row)
            return row, "created"
        existing.status = payload.aggregation.status.value
        existing.candidate_bias = payload.aggregation.candidate_bias.value
        existing.decision_readiness = payload.aggregation.decision_readiness.value
        return existing, "updated"


def strategy_run(*, status: str = "success") -> Any:
    return SimpleNamespace(
        run_id="SSR-23F",
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        status=status,
    )


def governance(
    name: str,
    role: str,
    *,
    mode: str = ParticipationMode.DECISION_PARTICIPANT.value,
    weight: str = "1.0",
    provides: tuple[str, ...] = (),
    can_veto: bool = False,
    veto_scope: str = "none",
) -> StrategyGovernance:
    return StrategyGovernance(
        strategy_name=name,
        strategy_role=role,
        provides=provides,
        maturity_stage="active",
        participation_mode=mode,
        decision_weight=Decimal(weight),
        can_veto=can_veto,
        veto_scope=veto_scope,
    )


def aggregate(rows: tuple[Any, ...], provider: FakeGovernanceProvider) -> Any:
    return StrategyEvidenceAggregator(governance_provider=provider).aggregate_strategy_evidence(
        aggregation_id="SEA-test",
        strategy_signal_run=strategy_run(),
        strategy_signal_results=rows,
        trace_id="trace-23f",
    )


def test_23f_reads_all_results_and_is_not_fixed_to_23b_23e_names() -> None:
    rows = (
        PoisonPrivatePayloadRow(
            "future_direction_module",
            strategy_role="directional",
            common_payload={"market_bias": "bullish_bias", "reason_codes": ["future_long"]},
        ),
        PoisonPrivatePayloadRow(
            "unknown_context_module",
            strategy_role="context",
            common_payload={"primary_regime": "range", "reason_codes": ["context_wait"]},
            signal_strength="0.40",
        ),
    )
    provider = FakeGovernanceProvider(
        {
            "future_direction_module": governance(
                "future_direction_module",
                "directional",
                provides=("direction_bias",),
            ),
            "unknown_context_module": governance(
                "unknown_context_module",
                "context",
                mode=ParticipationMode.EVIDENCE_ONLY.value,
                weight="0",
                provides=("market_environment_context",),
            ),
        }
    )

    result = aggregate(rows, provider)

    assert result.strategy_evidence_summary["strategy_result_count"] == 2
    assert result.candidate_bias == CandidateBias.LONG
    assert "future_direction_module" in result.strategy_evidence_summary["roles"]["directional"]


def test_default_governance_provider_reads_strategy_yaml_fields() -> None:
    provider = StrategyGovernanceProvider()

    market = provider.get_strategy_governance(strategy_name="market_direction_regime", strategy_role="context")
    risk = provider.get_strategy_governance(
        strategy_name="volatility_risk_control_strategy",
        strategy_role="risk_control",
    )

    assert market.participation_mode == ParticipationMode.DECISION_PARTICIPANT.value
    assert market.decision_weight == Decimal("1.0")
    assert "primary_regime" in market.provides
    assert risk.can_veto is True
    assert risk.veto_scope == "current_candidate"


def test_23f_repository_source_does_not_select_private_strategy_payload() -> None:
    import app.strategy.aggregation.evidence_repository as repository_module

    source = inspect.getsource(repository_module)

    assert "StrategySignalResult.strategy_payload_json" not in source


def test_23f_model_and_migration_create_only_evidence_aggregation_table() -> None:
    from sqlalchemy import UniqueConstraint

    from app.storage.mysql.models.strategy_aggregation import StrategyEvidenceAggregationResult

    table = StrategyEvidenceAggregationResult.__table__
    assert table.name == "strategy_evidence_aggregation_result"
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_strategy_evidence_aggregation_id" in unique_names
    assert "uq_strategy_evidence_signal_run_id" in unique_names
    assert "strategy_payload_json" not in table.columns

    migration_source = Path("migrations/versions/20260602_23f_strategy_evidence_aggregation.py").read_text(
        encoding="utf-8"
    )
    assert "strategy_evidence_aggregation_result" in migration_source
    assert "strategy_signal_result" not in migration_source
    assert "market_kline_4h" not in migration_source


def test_observe_only_and_evidence_only_do_not_change_candidate_bias() -> None:
    rows = (
        PoisonPrivatePayloadRow("formal_long", strategy_role="directional", common_payload={"market_bias": "bullish_bias"}),
        PoisonPrivatePayloadRow("observe_short", strategy_role="directional", common_payload={"market_bias": "bearish_bias"}),
        PoisonPrivatePayloadRow("evidence_short", strategy_role="context", common_payload={"primary_regime": "downtrend"}),
    )
    provider = FakeGovernanceProvider(
        {
            "formal_long": governance("formal_long", "directional"),
            "observe_short": governance(
                "observe_short",
                "directional",
                mode=ParticipationMode.OBSERVE_ONLY.value,
                weight="0",
            ),
            "evidence_short": governance(
                "evidence_short",
                "context",
                mode=ParticipationMode.EVIDENCE_ONLY.value,
                weight="0",
            ),
        }
    )

    result = aggregate(rows, provider)

    assert result.candidate_bias == CandidateBias.LONG
    assert result.observe_only_summary["strategy_count"] == 1
    assert result.observe_only_summary["observe_only_disagreement"] == [
        {"strategy_name": "observe_short", "effect": "support_short"}
    ]


def test_advisory_cannot_veto_even_when_common_result_blocks_candidate() -> None:
    rows = (
        PoisonPrivatePayloadRow("formal_long", strategy_role="directional", common_payload={"market_bias": "bullish_bias"}),
        PoisonPrivatePayloadRow(
            "advisory_risk",
            strategy_role="risk_control",
            common_payload={"risk_gate_decision": "block_current_candidate", "risk_scope": "current_candidate"},
        ),
    )
    provider = FakeGovernanceProvider(
        {
            "formal_long": governance("formal_long", "directional"),
            "advisory_risk": governance(
                "advisory_risk",
                "risk_control",
                mode=ParticipationMode.ADVISORY.value,
                weight="0.2",
                can_veto=True,
                veto_scope="current_candidate",
            ),
        }
    )

    result = aggregate(rows, provider)

    assert result.candidate_bias == CandidateBias.LONG
    assert result.risk_gate_summary["formal_veto_applied"] is False


def test_can_veto_false_cannot_formally_block_but_can_veto_true_blocks_current_candidate() -> None:
    long_row = PoisonPrivatePayloadRow(
        "formal_long",
        strategy_role="filter",
        common_payload={"trigger_state": "breakout_confirmed", "filter_decision": "pass"},
    )
    risk_row = PoisonPrivatePayloadRow(
        "risk_gate",
        strategy_role="risk_control",
        common_payload={"risk_gate_decision": "block_current_candidate", "risk_scope": "current_candidate"},
    )
    no_veto_provider = FakeGovernanceProvider(
        {
            "formal_long": governance("formal_long", "filter", provides=("trigger_state",)),
            "risk_gate": governance(
                "risk_gate",
                "risk_control",
                provides=("risk_gate_decision",),
                can_veto=False,
                veto_scope="current_candidate",
            ),
        }
    )
    veto_provider = FakeGovernanceProvider(
        {
            "formal_long": governance("formal_long", "filter", provides=("trigger_state",)),
            "risk_gate": governance(
                "risk_gate",
                "risk_control",
                provides=("risk_gate_decision",),
                can_veto=True,
                veto_scope="current_candidate",
            ),
        }
    )

    no_veto = aggregate((long_row, risk_row), no_veto_provider)
    veto = aggregate((long_row, risk_row), veto_provider)

    assert no_veto.candidate_bias == CandidateBias.LONG
    assert no_veto.risk_gate_summary["formal_veto_applied"] is False
    assert veto.candidate_bias == CandidateBias.BLOCKED
    assert veto.decision_readiness == DecisionReadiness.BLOCKED_BY_RISK
    assert veto.risk_gate_summary["formal_veto_applied"] is True
    assert veto.risk_gate_summary["veto_strategies"][0]["strategy_name"] == "risk_gate"


def test_missing_context_or_risk_control_outputs_evidence_missing() -> None:
    rows = (
        PoisonPrivatePayloadRow(
            "formal_long",
            strategy_role="filter",
            common_payload={"trigger_state": "breakout_confirmed"},
        ),
    )
    provider = FakeGovernanceProvider(
        {"formal_long": governance("formal_long", "filter", provides=("trigger_state",))},
        required_roles=("context", "risk_control"),
        required_role_provides={"context": ("primary_regime",), "risk_control": ("risk_gate_decision",)},
    )

    result = aggregate(rows, provider)

    assert result.candidate_bias == CandidateBias.INSUFFICIENT_EVIDENCE
    assert {item["role"] for item in result.evidence_missing} == {"context", "risk_control"}


def test_dry_run_confirm_write_and_repeated_confirm_write_are_idempotent() -> None:
    rows = (
        PoisonPrivatePayloadRow("formal_long", strategy_role="directional", common_payload={"market_bias": "bullish_bias"}),
    )
    repo = FakeEvidenceRepository(rows=rows)
    provider = FakeGovernanceProvider({"formal_long": governance("formal_long", "directional")})
    service = StrategyEvidenceAggregationService(
        repository=repo,
        aggregator=StrategyEvidenceAggregator(governance_provider=provider),
    )
    session = FakeSession()

    dry = service.run_strategy_evidence_aggregation(
        session,
        request=EvidenceAggregationRequest(
            strategy_signal_run_id="SSR-23F",
            trigger_source="cli",
            dry_run=True,
            confirm_write=False,
            trace_id="trace-dry",
        ),
    )
    assert dry.database_written is False
    assert repo.persisted == []

    first = service.run_strategy_evidence_aggregation(
        session,
        request=EvidenceAggregationRequest(
            strategy_signal_run_id="SSR-23F",
            trigger_source="cli",
            dry_run=False,
            confirm_write=True,
            trace_id="trace-write-1",
        ),
    )
    second = service.run_strategy_evidence_aggregation(
        session,
        request=EvidenceAggregationRequest(
            strategy_signal_run_id="SSR-23F",
            trigger_source="cli",
            dry_run=False,
            confirm_write=True,
            trace_id="trace-write-2",
        ),
    )

    assert first.database_written is True
    assert first.database_action == "created"
    assert second.database_written is True
    assert second.database_action == "updated"
    assert second.aggregation_id == first.aggregation_id
    assert len(repo.persisted) == 1
    assert session.commits == 2


def test_cli_only_parses_args_and_calls_23f_service(monkeypatch: Any, capsys: Any) -> None:
    fake_session = object()
    captured: list[EvidenceAggregationRequest] = []

    @contextmanager
    def fake_session_scope(**_kwargs: Any) -> Any:
        yield fake_session

    def fake_run_strategy_evidence_aggregation(*, db_session: Any, request: EvidenceAggregationRequest) -> Any:
        assert db_session is fake_session
        captured.append(request)
        return SimpleNamespace(
            status=SimpleNamespace(value="success"),
            exit_code=0,
            aggregation_id="SEA-cli",
            strategy_signal_run_id=request.strategy_signal_run_id,
            trace_id="trace-cli",
            database_written=request.confirm_write,
            database_action="created" if request.confirm_write else "dry_run",
            candidate_bias=SimpleNamespace(value="long"),
            candidate_confidence=Decimal("0.8000"),
            decision_readiness=SimpleNamespace(value="ready_for_model_review"),
            message="ok",
            error_code=None,
            error_message=None,
        )

    monkeypatch.setattr(evidence_cli, "session_scope", fake_session_scope)
    monkeypatch.setattr(evidence_cli, "run_strategy_evidence_aggregation", fake_run_strategy_evidence_aggregation)

    dry_exit = evidence_cli.main(["--strategy-signal-run-id", "SSR-cli", "--trigger-source", "cli"])
    dry_output = _captured_key_values(capsys)
    write_exit = evidence_cli.main(
        ["--strategy-signal-run-id", "SSR-cli", "--trigger-source", "cli", "--confirm-write"]
    )
    write_output = _captured_key_values(capsys)

    assert dry_exit == 0
    assert dry_output["database_written"] == "false"
    assert captured[0].dry_run is True
    assert write_exit == 0
    assert write_output["database_written"] == "true"
    assert captured[1].confirm_write is True


def test_stage18_material_pack_can_include_23f_aggregation_and_omits_it_when_absent() -> None:
    decision = AggregationDecision(
        analysis_hypothesis_direction=AnalysisHypothesisDirection.LONG,
        analysis_hypothesis_confidence=AnalysisHypothesisConfidence.MEDIUM,
        risk_level=AggregationRiskLevel.LOW,
        risk_gate_status=RiskGateStatus.PASS,
        conflict_level=ConflictLevel.NONE,
        direction_consensus="long_only",
        message="ok",
    )
    vote_summary = StrategyVoteSummary(
        effective_strategy_count=1,
        long_strategies=({"strategy_name": "formal_long", "signal_strength": 0.8},),
        long_strength=0.8,
        max_risk_level=AggregationRiskLevel.LOW,
    )
    evidence_row = SimpleNamespace(
        aggregation_id="SEA-existing",
        strategy_signal_run_id="SSR-23F",
        status="success",
        candidate_bias="long",
        candidate_confidence=Decimal("0.8000"),
        decision_readiness="ready_for_model_review",
        strategy_evidence_summary_json=json.dumps({"candidate_bias": "long"}, ensure_ascii=False),
        decision_source_chain_json=json.dumps([{"strategy_name": "formal_long"}], ensure_ascii=False),
        model_review_focus_json=json.dumps({"review_points": ["check evidence"]}, ensure_ascii=False),
        not_trading_advice=True,
    )

    with_evidence = build_material_pack(
        strategy_signal_run=strategy_run(),
        strategy_signal_results=(),
        restored_snapshot=restored_snapshot(),
        vote_summary=vote_summary,
        decision=decision,
        candidate_scenarios_json={"candidate_scenarios": []},
        strategy_evidence_aggregation=evidence_row,
    )
    without_evidence = build_material_pack(
        strategy_signal_run=strategy_run(),
        strategy_signal_results=(),
        restored_snapshot=restored_snapshot(),
        vote_summary=vote_summary,
        decision=decision,
        candidate_scenarios_json={"candidate_scenarios": []},
    )

    bridge = with_evidence.material_json["strategy_evidence_aggregation"]
    assert bridge["aggregation_id"] == "SEA-existing"
    assert bridge["strategy_evidence_summary"]["candidate_bias"] == "long"
    assert bridge["decision_source_chain"] == [{"strategy_name": "formal_long"}]
    assert "strategy_evidence_aggregation" not in without_evidence.material_json


def restored_snapshot() -> Any:
    rows_4h = kline_rows(24, interval_ms=14_400_000, interval_value="4h")
    rows_1d = kline_rows(2, interval_ms=86_400_000, interval_value="1d")
    snapshot = SimpleNamespace(
        snapshot_id="MCS-23F",
        symbol="BTCUSDT",
        base_interval_value="4h",
        higher_interval_value="1d",
        start_4h_open_time_ms=rows_4h[0].open_time_ms,
        end_4h_open_time_ms=rows_4h[-1].open_time_ms,
        start_1d_open_time_ms=rows_1d[0].open_time_ms,
        end_1d_open_time_ms=rows_1d[-1].open_time_ms,
    )
    return SimpleNamespace(snapshot=snapshot, rows_4h=rows_4h, rows_1d=rows_1d)


def kline_rows(count: int, *, interval_ms: int, interval_value: str) -> tuple[Any, ...]:
    start_ms = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    rows: list[Any] = []
    for index in range(count):
        close = Decimal("60000") + Decimal(index * 10)
        open_time_ms = start_ms + index * interval_ms
        rows.append(
            SimpleNamespace(
                symbol="BTCUSDT",
                interval_value=interval_value,
                open_time_ms=open_time_ms,
                open_time_utc=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
                open_price=close - Decimal("5"),
                high_price=close + Decimal("40"),
                low_price=close - Decimal("35"),
                close_price=close,
                volume=Decimal("1000") + Decimal(index),
            )
        )
    return tuple(rows)


def _captured_key_values(capsys: Any) -> dict[str, str]:
    captured = capsys.readouterr().out.strip().splitlines()
    return dict(line.split("=", 1) for line in captured if "=" in line)
