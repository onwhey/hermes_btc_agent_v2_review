"""Stage-23F strategy evidence aggregation algorithm.

This file belongs to `app/strategy/aggregation`. It groups already persisted
strategy signal results by role, provides, maturity, participation, and veto
metadata, then builds a strategy-domain evidence summary.

Called by: `app/strategy/aggregation/evidence_service.py` and tests.

External services: none. MySQL: none in this file. Redis: none. Hermes: none.
DeepSeek/large models: none. Trading execution: none. This module only reads
row metadata and `common_payload_json`; it never reads `strategy_payload_json`,
never calls strategy internals, never reruns strategies, and never generates
final advice or trade setup.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from app.strategy.aggregation.evidence_config import EvidenceAggregationConfig, StrategyGovernanceProvider
from app.strategy.aggregation.evidence_types import (
    CandidateBias,
    DecisionReadiness,
    EvidenceAggregationStatus,
    ParticipationMode,
    StrategyEvidenceAggregation,
    StrategyGovernance,
)

SUCCESS_STATUSES = {"success", "partial_success"}
INVALID_VALIDATION_STATUSES = {"failed", "invalid"}
RISK_BLOCK_DECISIONS = {"block_current_candidate", "block_all_candidates", "block_all", "block_long", "block_short"}


@dataclass(frozen=True)
class StrategyEvidenceItem:
    """Normalized public strategy evidence item.

    Parameters: one persisted strategy result row plus its public common payload
    and governance metadata.
    Return value: immutable item used only inside the 23F aggregator.
    Failure scenarios: none after JSON parsing.
    External effects: none.
    """

    strategy_name: str
    strategy_version: str
    strategy_role: str
    strategy_status: str
    validation_status: str | None
    governance: StrategyGovernance
    common_result: Mapping[str, Any]
    common_payload_parse_failed: bool
    effect: str
    candidate_direction: str
    reason_codes: tuple[str, ...]
    reason_text: str
    signal_strength: Decimal
    confidence_score: Decimal

    @property
    def participates_in_candidate_bias(self) -> bool:
        """Return whether this item can change candidate direction."""

        return (
            self.strategy_status == "success"
            and self.validation_status not in INVALID_VALIDATION_STATUSES
            and self.governance.enabled
            and self.governance.participation_mode == ParticipationMode.DECISION_PARTICIPANT.value
            and self.governance.decision_weight > Decimal("0")
        )

    @property
    def can_apply_veto(self) -> bool:
        """Return whether this item is eligible for scoped veto evaluation."""

        return (
            self.strategy_status == "success"
            and self.validation_status not in INVALID_VALIDATION_STATUSES
            and self.governance.enabled
            and self.governance.participation_mode == ParticipationMode.DECISION_PARTICIPANT.value
            and self.governance.can_veto
            and self.effect in RISK_BLOCK_DECISIONS
            and self.governance.veto_scope != "none"
            and not self.common_payload_parse_failed
        )

    def to_chain_item(self) -> Mapping[str, Any]:
        """Return compact JSON-ready evidence-chain item."""

        return {
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "strategy_role": self.strategy_role,
            "provides": list(self.governance.provides),
            "maturity_stage": self.governance.maturity_stage,
            "participation_mode": self.governance.participation_mode,
            "decision_weight": str(self.governance.decision_weight),
            "can_veto": self.governance.can_veto,
            "veto_scope": self.governance.veto_scope,
            "strategy_status": self.strategy_status,
            "validation_status": self.validation_status,
            "common_payload_parse_failed": self.common_payload_parse_failed,
            "effect": self.effect,
            "candidate_direction": self.candidate_direction,
            "reason_codes": list(self.reason_codes),
            "reason_text": self.reason_text,
            "signal_strength": str(self.signal_strength),
            "confidence_score": str(self.confidence_score),
        }


class StrategyEvidenceAggregator:
    """Aggregate public strategy results into one stage-23F evidence summary.

    Parameters: governance provider for config metadata.
    Return value: aggregator instance.
    Failure scenarios: invalid config or malformed common JSON raises and is
    converted by the service.
    External effects: none; this class is pure computation.
    """

    def __init__(self, *, governance_provider: StrategyGovernanceProvider) -> None:
        self._governance_provider = governance_provider

    def aggregate_strategy_evidence(
        self,
        *,
        aggregation_id: str,
        strategy_signal_run: Any,
        strategy_signal_results: tuple[Any, ...],
        trace_id: str,
    ) -> StrategyEvidenceAggregation:
        """Build the 23F aggregation from all rows in one strategy run."""

        aggregation_config = self._governance_provider.get_aggregation_config()
        evidence_items = tuple(self._normalize_row(row) for row in strategy_signal_results)
        scores = _score_candidate_bias(evidence_items)
        coverage = _build_role_coverage_matrix(evidence_items, aggregation_config=aggregation_config)
        missing = tuple(coverage["evidence_missing"])
        initial_conflicts = _build_conflict_summary(
            evidence_items=evidence_items,
            scores=scores,
            veto_items=(),
            veto_scope_mismatches=(),
        )
        initial_candidate_bias, initial_readiness, initial_status = _decide_candidate_bias(
            scores=scores,
            missing=missing,
            conflicts=initial_conflicts,
        )
        scoped_veto_candidates = tuple(item for item in evidence_items if item.can_apply_veto)
        veto_items = tuple(
            item for item in scoped_veto_candidates if _veto_matches_candidate(item, candidate_bias=initial_candidate_bias)
        )
        veto_scope_mismatches = tuple(item for item in scoped_veto_candidates if item not in veto_items)
        conflicts = _build_conflict_summary(
            evidence_items=evidence_items,
            scores=scores,
            veto_items=veto_items,
            veto_scope_mismatches=veto_scope_mismatches,
        )
        candidate_bias, readiness, status = _apply_scoped_veto_to_candidate(
            candidate_bias=initial_candidate_bias,
            readiness=initial_readiness,
            status=initial_status,
            veto_items=veto_items,
        )
        confidence = _candidate_confidence(scores=scores, missing=missing, conflicts=conflicts)
        chain = tuple(item.to_chain_item() for item in evidence_items)
        participation_summary = _build_participation_summary(evidence_items)
        observe_only_summary = _build_observe_only_summary(evidence_items, candidate_bias=candidate_bias)
        risk_gate_summary = _build_risk_gate_summary(
            evidence_items=evidence_items,
            veto_items=veto_items,
            veto_scope_mismatches=veto_scope_mismatches,
        )
        evidence_summary = _build_strategy_evidence_summary(
            evidence_items=evidence_items,
            scores=scores,
            candidate_bias=candidate_bias,
            confidence=confidence,
        )
        model_review_focus = _build_model_review_focus(
            candidate_bias=candidate_bias,
            readiness=readiness,
            missing=missing,
            conflicts=conflicts,
            risk_gate_summary=risk_gate_summary,
        )
        return StrategyEvidenceAggregation(
            aggregation_id=aggregation_id,
            strategy_signal_run_id=str(getattr(strategy_signal_run, "run_id", "")),
            symbol=str(getattr(strategy_signal_run, "symbol", "")),
            base_interval=str(getattr(strategy_signal_run, "base_interval_value", "")),
            higher_interval=str(getattr(strategy_signal_run, "higher_interval_value", "")),
            status=status,
            candidate_bias=candidate_bias,
            candidate_confidence=confidence,
            decision_readiness=readiness,
            strategy_evidence_summary=evidence_summary,
            decision_source_chain=chain,
            role_coverage_matrix=coverage,
            evidence_missing=missing,
            strategy_conflict_summary=conflicts,
            participation_summary=participation_summary,
            observe_only_summary=observe_only_summary,
            risk_gate_summary=risk_gate_summary,
            model_review_focus=model_review_focus,
            not_trading_advice=True,
            trace_id=trace_id,
        )

    def _normalize_row(self, row: Any) -> StrategyEvidenceItem:
        common_result, common_payload_parse_failed = _load_common_payload(getattr(row, "common_payload_json", None))
        strategy_name = str(getattr(row, "strategy_name", "") or "")
        row_role = str(getattr(row, "strategy_role", "") or "")
        governance = self._governance_provider.get_strategy_governance(
            strategy_name=strategy_name,
            strategy_role=row_role,
        )
        strategy_role = row_role or governance.strategy_role
        effect, candidate_direction = _classify_public_effect(
            strategy_role=strategy_role,
            common_result=common_result,
            strategy_status=str(getattr(row, "strategy_status", "") or ""),
        )
        return StrategyEvidenceItem(
            strategy_name=strategy_name,
            strategy_version=str(getattr(row, "strategy_version", "") or ""),
            strategy_role=strategy_role,
            strategy_status=str(getattr(row, "strategy_status", "") or ""),
            validation_status=_optional_text(getattr(row, "validation_status", None)),
            governance=governance,
            common_result=common_result,
            common_payload_parse_failed=common_payload_parse_failed,
            effect=effect,
            candidate_direction=candidate_direction,
            reason_codes=tuple(_string_list(common_result.get("reason_codes"))),
            reason_text=str(common_result.get("reason_text") or getattr(row, "reason_text", "") or ""),
            signal_strength=_decimal(common_result.get("signal_strength", getattr(row, "signal_strength", "0"))),
            confidence_score=_decimal(common_result.get("confidence_score", "0")),
        )


def _score_candidate_bias(evidence_items: tuple[StrategyEvidenceItem, ...]) -> Mapping[str, Decimal]:
    scores = {"long": Decimal("0"), "short": Decimal("0"), "wait": Decimal("0")}
    for item in evidence_items:
        if not item.participates_in_candidate_bias:
            continue
        weight = item.governance.decision_weight
        strength = item.signal_strength if item.signal_strength > Decimal("0") else Decimal("0.5")
        contribution = (weight * strength).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if item.effect in {"support_long", "oppose_short"}:
            scores["long"] += contribution
        elif item.effect in {"support_short", "oppose_long"}:
            scores["short"] += contribution
        elif item.effect in {"support_wait", "uncertain", "not_applicable"}:
            scores["wait"] += contribution
    return scores


def _build_role_coverage_matrix(
    evidence_items: tuple[StrategyEvidenceItem, ...],
    *,
    aggregation_config: EvidenceAggregationConfig,
) -> Mapping[str, Any]:
    by_role: dict[str, list[StrategyEvidenceItem]] = {}
    for item in evidence_items:
        by_role.setdefault(item.strategy_role or "unknown", []).append(item)
    role_rows: dict[str, Any] = {}
    missing: list[Mapping[str, Any]] = []
    all_roles = sorted(set(aggregation_config.required_roles) | set(by_role))
    for role in all_roles:
        role_items = tuple(by_role.get(role, ()))
        effective_items = tuple(item for item in role_items if _is_effective_result_for_coverage(item))
        provided = sorted({provided for item in effective_items for provided in item.governance.provides})
        required_provides = tuple(aggregation_config.required_role_provides.get(role, ()))
        missing_provides = tuple(provide for provide in required_provides if provide not in provided)
        parse_failed_items = tuple(item for item in role_items if item.common_payload_parse_failed)
        role_rows[role] = {
            "role": role,
            "required": role in aggregation_config.required_roles,
            "strategy_count": len(role_items),
            "effective_coverage_count": len(effective_items),
            "parse_failed_count": len(parse_failed_items),
            "provided": provided,
            "required_provides": list(required_provides),
            "missing_provides": list(missing_provides),
            "covered": bool(effective_items) and not missing_provides,
            "strategies": [item.strategy_name for item in role_items],
            "coverage_strategies": [item.strategy_name for item in effective_items],
        }
        for item in parse_failed_items:
            missing.append(
                {
                    "reason": "common_payload_parse_failed",
                    "role": role,
                    "strategy_name": item.strategy_name,
                    "strategy_version": item.strategy_version,
                    "impact": "coverage_ignored",
                }
            )
        if role in aggregation_config.required_roles and (not effective_items or missing_provides):
            missing.append(
                {
                    "role": role,
                    "missing_provides": list(missing_provides),
                    "strategy_count": len(role_items),
                    "effective_coverage_count": len(effective_items),
                    "impact": "candidate_bias_degraded",
                }
            )
    return {
        "required_roles": list(aggregation_config.required_roles),
        "roles": role_rows,
        "evidence_missing": missing,
    }


def _build_conflict_summary(
    *,
    evidence_items: tuple[StrategyEvidenceItem, ...],
    scores: Mapping[str, Decimal],
    veto_items: tuple[StrategyEvidenceItem, ...],
    veto_scope_mismatches: tuple[StrategyEvidenceItem, ...],
) -> Mapping[str, Any]:
    conflicts: list[Mapping[str, Any]] = []
    payload_parse_failures = tuple(item for item in evidence_items if item.common_payload_parse_failed)
    for item in payload_parse_failures:
        conflicts.append(
            {
                "conflict_type": "common_payload_parse_failed",
                "strategy_name": item.strategy_name,
                "strategy_version": item.strategy_version,
                "strategy_role": item.strategy_role,
            }
        )
    if scores["long"] > Decimal("0") and scores["short"] > Decimal("0"):
        conflicts.append(
            {
                "conflict_type": "direction_conflict",
                "long_score": str(scores["long"]),
                "short_score": str(scores["short"]),
            }
        )
    if veto_items and (scores["long"] > Decimal("0") or scores["short"] > Decimal("0")):
        conflicts.append(
            {
                "conflict_type": "trigger_vs_risk_conflict",
                "risk_veto_strategies": [item.strategy_name for item in veto_items],
            }
        )
    if veto_scope_mismatches:
        conflicts.append(
            {
                "conflict_type": "veto_scope_mismatch",
                "risk_veto_strategies": [
                    {
                        "strategy_name": item.strategy_name,
                        "effect": item.effect,
                        "veto_scope": item.governance.veto_scope,
                    }
                    for item in veto_scope_mismatches
                ],
            }
        )
    if any(item.strategy_role == "support_resistance" and item.strategy_status != "success" for item in evidence_items):
        conflicts.append({"conflict_type": "support_resistance_missing"})
    level = "none"
    if conflicts:
        level = "high" if any(item["conflict_type"] in {"trigger_vs_risk_conflict", "direction_conflict"} for item in conflicts) else "low"
    return {
        "conflict_level": level,
        "conflicts": conflicts,
        "long_score": str(scores["long"]),
        "short_score": str(scores["short"]),
        "wait_score": str(scores["wait"]),
    }


def _decide_candidate_bias(
    *,
    scores: Mapping[str, Decimal],
    missing: tuple[Mapping[str, Any], ...],
    conflicts: Mapping[str, Any],
) -> tuple[CandidateBias, DecisionReadiness, EvidenceAggregationStatus]:
    if missing:
        return (
            CandidateBias.INSUFFICIENT_EVIDENCE,
            DecisionReadiness.NEEDS_MORE_EVIDENCE,
            EvidenceAggregationStatus.INSUFFICIENT_EVIDENCE,
        )
    if conflicts.get("conflict_level") == "high":
        return CandidateBias.CONFLICT, DecisionReadiness.CONFLICT_REQUIRES_REVIEW, EvidenceAggregationStatus.PARTIAL_SUCCESS
    if scores["wait"] > scores["long"] and scores["wait"] > scores["short"]:
        return CandidateBias.WAIT, DecisionReadiness.WAIT_FOR_CONFIRMATION, EvidenceAggregationStatus.SUCCESS
    if scores["long"] > scores["short"] and scores["long"] > Decimal("0"):
        return CandidateBias.LONG, DecisionReadiness.READY_FOR_MODEL_REVIEW, EvidenceAggregationStatus.SUCCESS
    if scores["short"] > scores["long"] and scores["short"] > Decimal("0"):
        return CandidateBias.SHORT, DecisionReadiness.READY_FOR_MODEL_REVIEW, EvidenceAggregationStatus.SUCCESS
    if scores["wait"] > Decimal("0"):
        return CandidateBias.WAIT, DecisionReadiness.WAIT_FOR_CONFIRMATION, EvidenceAggregationStatus.SUCCESS
    return CandidateBias.NEUTRAL, DecisionReadiness.NOT_READY, EvidenceAggregationStatus.INSUFFICIENT_EVIDENCE


def _apply_scoped_veto_to_candidate(
    *,
    candidate_bias: CandidateBias,
    readiness: DecisionReadiness,
    status: EvidenceAggregationStatus,
    veto_items: tuple[StrategyEvidenceItem, ...],
) -> tuple[CandidateBias, DecisionReadiness, EvidenceAggregationStatus]:
    if not veto_items:
        return candidate_bias, readiness, status
    return CandidateBias.BLOCKED, DecisionReadiness.BLOCKED_BY_RISK, EvidenceAggregationStatus.PARTIAL_SUCCESS


def _candidate_confidence(
    *,
    scores: Mapping[str, Decimal],
    missing: tuple[Mapping[str, Any], ...],
    conflicts: Mapping[str, Any],
) -> Decimal:
    total = scores["long"] + scores["short"] + scores["wait"]
    if total <= Decimal("0"):
        return Decimal("0.0000")
    dominant = max(scores["long"], scores["short"], scores["wait"])
    confidence = dominant / total
    if missing:
        confidence *= Decimal("0.60")
    if conflicts.get("conflict_level") == "high":
        confidence *= Decimal("0.50")
    return confidence.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _build_strategy_evidence_summary(
    *,
    evidence_items: tuple[StrategyEvidenceItem, ...],
    scores: Mapping[str, Decimal],
    candidate_bias: CandidateBias,
    confidence: Decimal,
) -> Mapping[str, Any]:
    by_role: dict[str, list[str]] = {}
    for item in evidence_items:
        by_role.setdefault(item.strategy_role or "unknown", []).append(item.strategy_name)
    return {
        "strategy_result_count": len(evidence_items),
        "candidate_bias": candidate_bias.value,
        "candidate_confidence": str(confidence),
        "score_summary": {key: str(value) for key, value in scores.items()},
        "roles": {role: sorted(names) for role, names in by_role.items()},
        "not_trading_advice": True,
    }


def _build_participation_summary(evidence_items: tuple[StrategyEvidenceItem, ...]) -> Mapping[str, Any]:
    summary: dict[str, Any] = {}
    for item in evidence_items:
        mode = item.governance.participation_mode
        bucket = summary.setdefault(
            mode,
            {
                "strategy_count": 0,
                "strategies": [],
                "total_decision_weight": "0",
            },
        )
        bucket["strategy_count"] += 1
        bucket["strategies"].append(item.strategy_name)
        bucket["total_decision_weight"] = str(
            Decimal(str(bucket["total_decision_weight"])) + item.governance.decision_weight
        )
    return summary


def _build_observe_only_summary(
    evidence_items: tuple[StrategyEvidenceItem, ...],
    *,
    candidate_bias: CandidateBias,
) -> Mapping[str, Any]:
    observed = tuple(
        item for item in evidence_items if item.governance.participation_mode == ParticipationMode.OBSERVE_ONLY.value
    )
    disagreements: list[Mapping[str, Any]] = []
    for item in observed:
        if candidate_bias == CandidateBias.LONG and item.effect == "support_short":
            disagreements.append({"strategy_name": item.strategy_name, "effect": item.effect})
        if candidate_bias == CandidateBias.SHORT and item.effect == "support_long":
            disagreements.append({"strategy_name": item.strategy_name, "effect": item.effect})
    return {
        "strategy_count": len(observed),
        "strategies": [item.strategy_name for item in observed],
        "observed_effects": [item.to_chain_item() for item in observed],
        "observe_only_disagreement": disagreements,
    }


def _build_risk_gate_summary(
    *,
    evidence_items: tuple[StrategyEvidenceItem, ...],
    veto_items: tuple[StrategyEvidenceItem, ...],
    veto_scope_mismatches: tuple[StrategyEvidenceItem, ...],
) -> Mapping[str, Any]:
    risk_items = tuple(item for item in evidence_items if item.strategy_role == "risk_control")
    return {
        "risk_strategy_count": len(risk_items),
        "formal_veto_applied": bool(veto_items),
        "veto_strategies": [
            {
                "strategy_name": item.strategy_name,
                "risk_gate_decision": item.common_result.get("risk_gate_decision"),
                "risk_scope": item.common_result.get("risk_scope") or item.governance.veto_scope,
                "veto_scope": item.governance.veto_scope,
            }
            for item in veto_items
        ],
        "veto_scope_mismatches": [
            {
                "strategy_name": item.strategy_name,
                "risk_gate_decision": item.common_result.get("risk_gate_decision"),
                "risk_scope": item.common_result.get("risk_scope") or item.governance.veto_scope,
                "veto_scope": item.governance.veto_scope,
                "effect": item.effect,
                "reason": "veto_scope_does_not_match_candidate_bias",
            }
            for item in veto_scope_mismatches
        ],
        "risk_evidence": [item.to_chain_item() for item in risk_items],
    }


def _veto_matches_candidate(item: StrategyEvidenceItem, *, candidate_bias: CandidateBias) -> bool:
    scope = item.governance.veto_scope
    effect = item.effect
    if scope == "none":
        return False
    if scope == "all_candidates" or effect == "block_all":
        return True
    if candidate_bias not in {CandidateBias.LONG, CandidateBias.SHORT}:
        return False
    if scope == "current_candidate":
        if effect == "block_current_candidate":
            return True
        if effect == "block_long":
            return candidate_bias == CandidateBias.LONG
        if effect == "block_short":
            return candidate_bias == CandidateBias.SHORT
        return False
    if scope == "long_candidate":
        return candidate_bias == CandidateBias.LONG and effect in {"block_long", "block_current_candidate"}
    if scope == "short_candidate":
        return candidate_bias == CandidateBias.SHORT and effect in {"block_short", "block_current_candidate"}
    return False


def _is_effective_result_for_coverage(item: StrategyEvidenceItem) -> bool:
    return (
        item.strategy_status == "success"
        and item.validation_status not in INVALID_VALIDATION_STATUSES
        and item.governance.enabled
        and not item.common_payload_parse_failed
        and bool(item.common_result)
    )


def _build_model_review_focus(
    *,
    candidate_bias: CandidateBias,
    readiness: DecisionReadiness,
    missing: tuple[Mapping[str, Any], ...],
    conflicts: Mapping[str, Any],
    risk_gate_summary: Mapping[str, Any],
) -> Mapping[str, Any]:
    focus: list[str] = []
    if missing:
        focus.append("审查缺失的策略角色证据是否足以降级为等待。")
    if conflicts.get("conflict_level") != "none":
        focus.append("审查策略证据之间的方向或风控冲突。")
    if risk_gate_summary.get("formal_veto_applied"):
        focus.append("审查风控阻断是否只作用于策略域候选，而非最终交易建议。")
    if not focus:
        focus.append("审查 strategy_evidence_summary 与市场材料是否一致。")
    return {
        "candidate_bias": candidate_bias.value,
        "decision_readiness": readiness.value,
        "review_points": focus,
        "not_trading_advice": True,
    }


def _classify_public_effect(
    *,
    strategy_role: str,
    common_result: Mapping[str, Any],
    strategy_status: str,
) -> tuple[str, str]:
    if strategy_status != "success":
        if strategy_status in {"not_implemented", "skipped", "disabled"}:
            return "not_applicable", "unknown"
        return "failed", "unknown"
    risk_gate_decision = str(common_result.get("risk_gate_decision") or "").strip()
    if risk_gate_decision:
        return _classify_risk_gate_decision(risk_gate_decision)
    trigger_state = str(common_result.get("trigger_state") or "").strip()
    filter_decision = str(common_result.get("filter_decision") or "").strip()
    if trigger_state or filter_decision:
        return _classify_trigger_effect(trigger_state=trigger_state, common_result=common_result)
    market_bias = str(common_result.get("market_bias") or "").strip()
    primary_regime = str(common_result.get("primary_regime") or "").strip()
    if market_bias or primary_regime:
        return _classify_market_bias(market_bias=market_bias, primary_regime=primary_regime)
    if strategy_role == "support_resistance" and common_result.get("key_levels"):
        return "neutral", "unknown"
    if strategy_role == "placeholder":
        return "not_applicable", "unknown"
    risk_level = str(common_result.get("risk_level") or "").strip()
    if strategy_role == "risk_control" and risk_level in {"high", "extreme"}:
        return "support_wait", "wait"
    return "neutral", "unknown"


def _classify_risk_gate_decision(value: str) -> tuple[str, str]:
    if value in {"block_all_candidates", "block_all"}:
        return "block_all", "wait"
    if value == "block_current_candidate":
        return "block_current_candidate", "wait"
    if value == "block_long":
        return "block_long", "short"
    if value == "block_short":
        return "block_short", "long"
    if value in {"wait", "insufficient_context", "unknown", "not_applicable"}:
        return "support_wait", "wait"
    return "neutral", "unknown"


def _classify_trigger_effect(*, trigger_state: str, common_result: Mapping[str, Any]) -> tuple[str, str]:
    if trigger_state in {"breakout_confirmed", "breakout_attempt"}:
        return "support_long", "long"
    if trigger_state in {"breakdown_confirmed", "breakdown_attempt"}:
        return "support_short", "short"
    if trigger_state in {"pullback_confirmed", "pullback_testing"}:
        return _classify_pullback_direction(common_result)
    if trigger_state in {"insufficient_key_levels", "not_applicable", "no_clear_trigger", "uncertain"}:
        return "support_wait", "wait"
    return "uncertain", "wait"


def _classify_pullback_direction(common_result: Mapping[str, Any]) -> tuple[str, str]:
    tested = common_result.get("tested_level_summary")
    if not isinstance(tested, Mapping):
        return "uncertain", "wait"
    role_flip_status = str(tested.get("role_flip_status") or "")
    level_type = str(tested.get("level_type") or "")
    level_group = str(tested.get("level_group") or "")
    if role_flip_status == "resistance_to_support":
        return "support_long", "long"
    if role_flip_status == "support_to_resistance":
        return "support_short", "short"
    if level_type == "support" or level_group in {"nearest_support", "major_support", "range_lower_boundary"}:
        return "support_long", "long"
    if level_type == "resistance" or level_group in {"nearest_resistance", "major_resistance", "range_upper_boundary"}:
        return "support_short", "short"
    return "uncertain", "wait"


def _classify_market_bias(*, market_bias: str, primary_regime: str) -> tuple[str, str]:
    if market_bias in {"bullish_bias", "bullish"} or primary_regime == "uptrend":
        return "support_long", "long"
    if market_bias in {"bearish_bias", "bearish"} or primary_regime == "downtrend":
        return "support_short", "short"
    if market_bias in {"wait", "neutral", "mixed"} or primary_regime in {"range", "volatile", "mixed"}:
        return "support_wait", "wait"
    return "uncertain", "wait"


def _load_common_payload(value: Any) -> tuple[Mapping[str, Any], bool]:
    if isinstance(value, Mapping):
        return dict(value), False
    if not value:
        return {}, False
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}, True
    if not isinstance(parsed, Mapping):
        return {}, True
    return dict(parsed), False


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _decimal(value: Any) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except Exception:  # noqa: BLE001 - malformed signal strength becomes zero evidence weight.
        return Decimal("0")
    if decimal_value < Decimal("0"):
        return Decimal("0")
    if decimal_value > Decimal("1"):
        return Decimal("1")
    return decimal_value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["StrategyEvidenceAggregator", "StrategyEvidenceItem"]
