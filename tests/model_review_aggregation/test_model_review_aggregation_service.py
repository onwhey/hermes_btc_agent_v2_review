"""Tests for stage-20A model review aggregation and reuse checks.

These tests use an in-memory repository. They do not request Binance, connect
MySQL/Redis, send Hermes, call large model providers, or modify Kline tables.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.core.config import AppSettings
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI
from app.model_analysis.prompt_builder import PROMPT_TEMPLATE_HASH
from app.model_review_aggregation.fingerprint import build_material_fingerprint
from app.model_review_aggregation.schema import (
    EXIT_BLOCKED,
    EXIT_SUCCESS,
    ModelReviewAggregationRequest,
    ModelReviewAggregationStatus,
    format_model_review_aggregation_result_lines,
)
from app.model_review_aggregation.service import ModelReviewAggregationService


BASE_TIME_MS = 1_700_000_000_000
BASE_INTERVAL_MS = 14_400_000
CREATED_AT = datetime(2026, 5, 21, 1, 0, tzinfo=timezone.utc)


class FakeRepository:
    """In-memory stage-20A repository used to keep tests side-effect free."""

    def __init__(self, *, materials=None, runs=None, candidates=None):
        self.materials = materials or {}
        self.runs = runs or {}
        self.candidates = candidates or []
        self.written_payloads = []

    def get_material_pack_by_id(self, db_session, *, material_pack_id):
        return self.materials.get(material_pack_id)

    def list_model_analysis_runs_for_material_pack(self, db_session, *, material_pack_id):
        return tuple(self.runs.get(material_pack_id, ()))

    def list_success_model_review_candidates(self, db_session, *, symbol, base_interval, higher_interval, limit=20):
        del db_session, limit
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.model_analysis_run.symbol == symbol
            and candidate.model_analysis_run.base_interval == base_interval
            and candidate.model_analysis_run.higher_interval == higher_interval
        )

    def create_model_review_aggregation_run(self, db_session, *, payload):
        del db_session
        self.written_payloads.append(payload)
        return SimpleNamespace(review_aggregation_run_id=payload.review_aggregation_run_id)


class FakeSession:
    """Small session double that records commit/rollback calls."""

    def __init__(self):
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


def test_missing_material_pack_is_blocked():
    repo = FakeRepository()
    result, session = _run(repo, material_pack_id="AMP-missing")

    assert result.status == ModelReviewAggregationStatus.BLOCKED
    assert result.exit_code == EXIT_BLOCKED
    assert result.error_code == "material_pack_not_found"
    assert result.model_review_invoked is False
    assert "本轮未调用大模型" in result.summary_text
    assert repo.written_payloads == []
    assert session.commit_count == 0


def test_material_pack_without_successful_model_result_is_blocked():
    material = _material_pack("AMP-current", base_end_ms=BASE_TIME_MS)
    failed_run = _model_run("MAR-failed", material_pack_id="AMP-current", status="failed")
    blocked_run = _model_run("MAR-blocked", material_pack_id="AMP-current", status="blocked")
    repo = FakeRepository(
        materials={material.material_pack_id: material},
        runs={material.material_pack_id: [failed_run, blocked_run]},
    )

    result, _ = _run(repo, material_pack_id=material.material_pack_id)

    assert result.status == ModelReviewAggregationStatus.BLOCKED
    assert result.error_code == "no_model_review_result"
    assert result.failed_model_result_count == 1
    assert result.blocked_model_result_count == 1
    assert result.model_review_reused is False
    assert result.model_review_invoked is False
    assert "MODEL_REVIEW_REAL_MODEL_ENABLED=false" in result.model_review_skip_reason


def test_single_successful_stage19_result_generates_stage20a_summary():
    material = _material_pack("AMP-current", base_end_ms=BASE_TIME_MS)
    run = _model_run("MAR-current", material_pack_id=material.material_pack_id)
    result_row = _model_result(run.model_analysis_run_id, material.material_pack_id)
    candidate = _candidate(run, result_row, material)
    repo = FakeRepository(
        materials={material.material_pack_id: material},
        runs={material.material_pack_id: [run]},
        candidates=[candidate],
    )

    result, _ = _run(repo, material_pack_id=material.material_pack_id)

    assert result.status == ModelReviewAggregationStatus.SUCCESS
    assert result.exit_code == EXIT_SUCCESS
    assert result.accepted_model_result_count == 1
    assert result.model_review_reused is False
    assert result.reused_model_analysis_run_id is None
    assert result.model_review_basis == "current_model_review"
    assert result.review_decision_summary == "accept_for_further_review"
    assert result.evidence_quality_summary == "sufficient"
    assert result.risk_acceptability_summary == "acceptable"
    assert result.strategy_conflict_summary == "low"
    assert "本轮未调用大模型" in result.summary_text


def test_old_stage19_result_within_three_base_bars_can_be_reused():
    old_material = _material_pack("AMP-old", base_end_ms=BASE_TIME_MS)
    current_material = _material_pack("AMP-current", base_end_ms=BASE_TIME_MS + 2 * BASE_INTERVAL_MS)
    old_run = _model_run("MAR-old", material_pack_id=old_material.material_pack_id)
    old_result = _model_result(old_run.model_analysis_run_id, old_material.material_pack_id)
    repo = FakeRepository(
        materials={current_material.material_pack_id: current_material},
        runs={current_material.material_pack_id: []},
        candidates=[_candidate(old_run, old_result, old_material)],
    )

    result, _ = _run(repo, material_pack_id=current_material.material_pack_id)

    assert result.status == ModelReviewAggregationStatus.SUCCESS
    assert result.model_review_reused is True
    assert result.reused_model_analysis_run_id == "MAR-old"
    assert result.model_review_reuse_base_bars == 2
    assert result.model_review_reuse_status == "reused_within_base_bar_ttl"
    assert result.model_review_invoked is False
    assert "本轮未调用大模型" in result.model_review_skip_reason


def test_support_resistance_price_changes_make_material_fingerprint_different():
    first_material = _material_pack(
        "AMP-first",
        base_end_ms=BASE_TIME_MS,
        support_candidates=[{"price": "65000"}, {"low": "64100", "high": "64500"}],
        resistance_candidates=[{"zone": {"lower": "68200", "upper": "68900"}}],
    )
    second_material = _material_pack(
        "AMP-second",
        base_end_ms=BASE_TIME_MS,
        support_candidates=[{"price": "66000"}, {"low": "64100", "high": "64500"}],
        resistance_candidates=[{"zone": {"lower": "68200", "upper": "68900"}}],
    )

    first_fingerprint = build_material_fingerprint(first_material)
    second_fingerprint = build_material_fingerprint(second_material)

    assert first_fingerprint.details["support_candidate_count"] == 2
    assert second_fingerprint.details["support_candidate_count"] == 2
    assert first_fingerprint.details["resistance_candidate_count"] == 1
    assert second_fingerprint.details["resistance_candidate_count"] == 1
    assert "support_candidates_summary" in first_fingerprint.details
    assert "resistance_candidates_summary" in first_fingerprint.details
    assert first_fingerprint.details["support_candidates_summary"] != second_fingerprint.details[
        "support_candidates_summary"
    ]
    assert first_fingerprint.fingerprint != second_fingerprint.fingerprint


def test_old_stage19_result_after_three_base_bars_is_expired_and_not_latest_review():
    old_material = _material_pack("AMP-old", base_end_ms=BASE_TIME_MS)
    current_material = _material_pack("AMP-current", base_end_ms=BASE_TIME_MS + 4 * BASE_INTERVAL_MS)
    old_run = _model_run("MAR-old", material_pack_id=old_material.material_pack_id)
    old_result = _model_result(old_run.model_analysis_run_id, old_material.material_pack_id)
    repo = FakeRepository(
        materials={current_material.material_pack_id: current_material},
        runs={current_material.material_pack_id: []},
        candidates=[_candidate(old_run, old_result, old_material)],
    )

    result, _ = _run(repo, material_pack_id=current_material.material_pack_id)

    assert result.status == ModelReviewAggregationStatus.BLOCKED
    assert result.error_code == "model_review_expired_but_real_model_disabled"
    assert result.model_review_expired is True
    assert result.model_review_reused is False
    assert result.model_review_reuse_base_bars == 4
    assert result.model_review_basis == "expired_model_review_not_used"
    assert "旧模型审查已过期" in result.model_review_skip_reason
    assert "MODEL_REVIEW_REAL_MODEL_ENABLED=false" in result.model_review_skip_reason


def test_real_model_disabled_is_reported_and_never_invoked():
    material = _material_pack("AMP-current", base_end_ms=BASE_TIME_MS)
    repo = FakeRepository(materials={material.material_pack_id: material})

    result, _ = _run(repo, material_pack_id=material.material_pack_id)

    assert result.status == ModelReviewAggregationStatus.BLOCKED
    assert result.model_review_invoked is False
    assert result.model_review_invocation_mode == "none"
    assert result.model_review_block_reason == "MODEL_REVIEW_REAL_MODEL_ENABLED=false"
    assert "本轮未调用大模型" in result.summary_text


def test_dry_run_does_not_write_stage20a_row():
    material = _material_pack("AMP-current", base_end_ms=BASE_TIME_MS)
    run = _model_run("MAR-current", material_pack_id=material.material_pack_id)
    repo = FakeRepository(
        materials={material.material_pack_id: material},
        runs={material.material_pack_id: [run]},
        candidates=[_candidate(run, _model_result(run.model_analysis_run_id, material.material_pack_id), material)],
    )

    result, session = _run(repo, material_pack_id=material.material_pack_id, confirm_write=False)

    assert result.status == ModelReviewAggregationStatus.SUCCESS
    assert repo.written_payloads == []
    assert session.commit_count == 0


def test_confirm_write_persists_stage20a_row():
    material = _material_pack("AMP-current", base_end_ms=BASE_TIME_MS)
    run = _model_run("MAR-current", material_pack_id=material.material_pack_id)
    repo = FakeRepository(
        materials={material.material_pack_id: material},
        runs={material.material_pack_id: [run]},
        candidates=[_candidate(run, _model_result(run.model_analysis_run_id, material.material_pack_id), material)],
    )

    result, session = _run(repo, material_pack_id=material.material_pack_id, confirm_write=True)

    assert result.status == ModelReviewAggregationStatus.SUCCESS
    assert len(repo.written_payloads) == 1
    assert repo.written_payloads[0].material_pack_id == material.material_pack_id
    assert repo.written_payloads[0].model_review_invoked is False
    assert session.commit_count == 1


def test_output_boundary_fields_are_always_false():
    material = _material_pack("AMP-current", base_end_ms=BASE_TIME_MS)
    run = _model_run("MAR-current", material_pack_id=material.material_pack_id)
    repo = FakeRepository(
        materials={material.material_pack_id: material},
        runs={material.material_pack_id: [run]},
        candidates=[_candidate(run, _model_result(run.model_analysis_run_id, material.material_pack_id), material)],
    )

    result, _ = _run(repo, material_pack_id=material.material_pack_id, confirm_write=True)
    payload = repo.written_payloads[0]

    assert result.is_final_trading_advice is False
    assert result.is_trading_signal is False
    assert result.is_executable is False
    assert result.auto_trading_allowed is False
    assert payload.is_final_trading_advice is False
    assert payload.is_trading_signal is False
    assert payload.is_executable is False
    assert payload.auto_trading_allowed is False
    assert payload.directional_trade_allowed is False


def test_model_participation_status_fields_are_complete_in_cli_output():
    material = _material_pack("AMP-current", base_end_ms=BASE_TIME_MS)
    run = _model_run("MAR-current", material_pack_id=material.material_pack_id)
    repo = FakeRepository(
        materials={material.material_pack_id: material},
        runs={material.material_pack_id: [run]},
        candidates=[_candidate(run, _model_result(run.model_analysis_run_id, material.material_pack_id), material)],
    )

    result, _ = _run(repo, material_pack_id=material.material_pack_id)
    lines = format_model_review_aggregation_result_lines(result)

    assert any(line == "model_review_invoked=false" for line in lines)
    assert any(line == "model_review_reused=false" for line in lines)
    assert any(line.startswith("reused_model_analysis_run_id=") for line in lines)
    assert any(line.startswith("model_review_skip_reason=本轮未调用大模型") for line in lines)
    assert any(line.startswith("model_review_basis=current_model_review") for line in lines)
    assert any(line == "is_final_trading_advice=false" for line in lines)
    assert any(line == "auto_trading_allowed=false" for line in lines)


def _run(repo, *, material_pack_id, confirm_write=False, settings=None):
    settings = settings or AppSettings(
        model_review_real_model_enabled=False,
        model_review_reuse_max_base_bars=3,
    )
    service = ModelReviewAggregationService(settings=settings, repository=repo)
    request = ModelReviewAggregationRequest(
        material_pack_id=material_pack_id,
        trigger_source=TRIGGER_SOURCE_CLI,
        dry_run=not confirm_write,
        confirm_write=confirm_write,
        created_by="pytest",
        trace_id="trace-stage20a",
    )
    session = FakeSession()
    return service.run_model_review_aggregation(session, request=request), session


def _material_pack(
    material_pack_id,
    *,
    base_end_ms,
    support_candidates=None,
    resistance_candidates=None,
):
    support_candidates = support_candidates or [{"level": "support-zone"}]
    resistance_candidates = resistance_candidates or [{"level": "resistance-zone"}]
    material_json = {
        "symbol": "BTCUSDT",
        "base_interval": "4h",
        "higher_interval": "1d",
        "analysis_hypothesis_direction": "long_bias",
        "risk_gate_status": "allowed_for_review",
        "risk_level": "medium",
        "swing": {"structure_state": "range_break_attempt"},
        "volatility": {"volatility_state": "normal"},
        "strategy_conflict_points": {"conflict_level": "low"},
        "support_resistance": {
            "support_candidates": support_candidates,
            "resistance_candidates": resistance_candidates,
        },
        "hypothesis_invalidation_check": "close below structure support",
        "hypothesis_target_observation_zone": "prior resistance zone",
    }
    summary_json = {
        "analysis_hypothesis_direction": "long_bias",
        "risk_gate_status": "allowed_for_review",
        "risk_level": "medium",
        "structure_state": "range_break_attempt",
        "volatility_state": "normal",
        "conflict_level": "low",
    }
    return SimpleNamespace(
        material_pack_id=material_pack_id,
        aggregation_run_id=f"AGR-{material_pack_id}",
        strategy_signal_run_id=f"SSR-{material_pack_id}",
        snapshot_id=f"SNAP-{material_pack_id}",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        status="success",
        material_json=json.dumps(material_json, ensure_ascii=False),
        summary_json=json.dumps(summary_json, ensure_ascii=False),
        data_window_json=json.dumps({"base_open_time_end_ms": base_end_ms}, ensure_ascii=False),
        created_at_utc=CREATED_AT,
    )


def _model_run(model_analysis_run_id, *, material_pack_id, status="success"):
    return SimpleNamespace(
        model_analysis_run_id=model_analysis_run_id,
        review_version_key=f"RVK-{model_analysis_run_id}",
        material_pack_id=material_pack_id,
        aggregation_run_id=f"AGR-{material_pack_id}",
        strategy_signal_run_id=f"SSR-{material_pack_id}",
        snapshot_id=f"SNAP-{material_pack_id}",
        symbol="BTCUSDT",
        base_interval="4h",
        higher_interval="1d",
        status=status,
        review_schema_version="review_schema_v1",
        prompt_template_version="review_gate_v1",
        prompt_template_hash=PROMPT_TEMPLATE_HASH,
        model_provider="mock",
        model_name="mock-reviewer",
        model_version="mock_v1",
        model_key="mock_review",
        model_role="review_gate",
        profile_hash="mock-profile-hash",
        is_final_trading_advice=False,
        is_trading_signal=False,
        is_executable=False,
        auto_trading_allowed=False,
        created_at_utc=CREATED_AT,
    )


def _model_result(model_analysis_run_id, material_pack_id):
    return SimpleNamespace(
        model_analysis_result_id=f"MARSR-{model_analysis_run_id}",
        model_analysis_run_id=model_analysis_run_id,
        review_version_key=f"RVK-{model_analysis_run_id}",
        material_pack_id=material_pack_id,
        aggregation_run_id=f"AGR-{material_pack_id}",
        strategy_signal_run_id=f"SSR-{material_pack_id}",
        review_decision="accept_for_further_review",
        human_review_required=False,
        evidence_quality="sufficient",
        logic_consistency="consistent",
        risk_acceptability="acceptable",
        strategy_conflict_level="low",
        missing_evidence_json=json.dumps(["await next candle close"], ensure_ascii=False),
        risk_warnings_json=json.dumps(["volatility may expand"], ensure_ascii=False),
        human_review_questions_json=json.dumps(["confirm invalidation condition"], ensure_ascii=False),
        summary_text="Mock review says the candidate can be reviewed further.",
        created_at_utc=CREATED_AT,
    )


def _candidate(run, result, material):
    return SimpleNamespace(
        model_analysis_run=run,
        model_analysis_result=result,
        material_pack=material,
    )
