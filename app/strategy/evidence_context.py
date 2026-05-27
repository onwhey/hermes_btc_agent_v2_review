"""Public same-run evidence context for dependent strategies.

This file belongs to `app/strategy`. It carries only public `common_result`
payloads emitted earlier in the same strategy run.
It is called by `app/strategy/runner.py::StrategyRunner.run_strategies` and by
dependent strategies such as stage-23D.
It does not access external services, read or write MySQL, read or write Redis,
send Hermes, call DeepSeek or any large language model, read account or
position state, generate final advice, modify Kline tables, or trade.
It deliberately does not store `strategy_payload_json`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.strategy.types import StrategySignal, StrategySignalStatus


@dataclass(frozen=True)
class PublicRoleOutput:
    """One public strategy output available to later same-run strategies."""

    strategy_name: str
    strategy_role: str
    strategy_status: str
    common_result: Mapping[str, Any]


@dataclass(frozen=True)
class EvidenceContext:
    """Immutable same-run context containing public role outputs only."""

    public_role_outputs: Mapping[str, tuple[PublicRoleOutput, ...]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "EvidenceContext":
        """Return an empty context for the first strategy in a run."""

        return cls(public_role_outputs={})

    def with_signal(self, signal: StrategySignal) -> "EvidenceContext":
        """Return a new context including one successful public signal.

        Failed, invalid, and not-implemented signals are not published as
        evidence. Only `common_payload_json` is copied; private strategy payload
        and model material never enter this context.
        """

        if signal.strategy_status not in (StrategySignalStatus.SUCCESS, StrategySignalStatus.NO_SIGNAL):
            return self
        if not signal.strategy_role or not isinstance(signal.common_payload_json, Mapping):
            return self
        role = str(signal.strategy_role)
        output = PublicRoleOutput(
            strategy_name=signal.strategy_name,
            strategy_role=role,
            strategy_status=signal.strategy_status.value,
            common_result=dict(signal.common_payload_json),
        )
        active = {key: tuple(value) for key, value in self.public_role_outputs.items()}
        active[role] = active.get(role, ()) + (output,)
        return EvidenceContext(public_role_outputs=active)

    def key_levels_for_role(self, role: str) -> tuple[Mapping[str, Any], ...]:
        """Return public key-level summaries for one role."""

        levels: list[Mapping[str, Any]] = []
        for output in self.public_role_outputs.get(role, ()):
            raw_levels = output.common_result.get("key_levels")
            if not isinstance(raw_levels, list):
                continue
            levels.extend(dict(item) for item in raw_levels if isinstance(item, Mapping))
        return tuple(levels)


__all__ = ["EvidenceContext", "PublicRoleOutput"]
