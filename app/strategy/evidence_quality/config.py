"""Local config loader for the 26B strategy evidence quality gate.

本文件属于 `app/strategy/evidence_quality` 模块。
本文件负责读取本地策略 registry 和 23F governance 配置，识别 26B 第一版
“正常运行策略”。本文件不负责质量判定，不访问 MySQL，不读写 Redis，不发送
Hermes，不调用 DeepSeek 或其他大模型，不读取账户或仓位，不生成订单，不自动交易。
主要被 `service.py` 调用。
外部服务：无。数据库：无。Redis：无。Hermes：无。交易执行：无。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from app.strategy.aggregation.evidence_config import (
    DEFAULT_STRATEGY_CONFIG_DIR,
    EvidenceAggregationConfigError,
    StrategyGovernanceProvider,
    create_default_strategy_governance_provider,
)
from app.strategy.evidence_quality.types import NormalOperatingStrategyDefinition

DEFAULT_STRATEGY_REGISTRY_PATH = DEFAULT_STRATEGY_CONFIG_DIR / "strategy_registry.yaml"


class StrategyEvidenceQualityConfigProvider:
    """Load 26B strategy-quality config from local strategy YAML files.

    Parameters: optional strategy registry path and existing 23F governance
    provider.
    Return value: provider instance.
    Failure scenarios: missing/invalid local config raises
    `EvidenceAggregationConfigError`.
    External services: none.
    Data impact: reads local config files only; no MySQL/Redis/Hermes/model/trade
    side effects.
    """

    def __init__(
        self,
        *,
        registry_path: Path | None = None,
        governance_provider: StrategyGovernanceProvider | Any | None = None,
    ) -> None:
        self._registry_path = registry_path or DEFAULT_STRATEGY_REGISTRY_PATH
        self._governance_provider = governance_provider or create_default_strategy_governance_provider()

    def list_normal_operating_strategies(self) -> tuple[NormalOperatingStrategyDefinition, ...]:
        """Return active strategy definitions that must participate in 26B."""

        result: list[NormalOperatingStrategyDefinition] = []
        for strategy_name in _read_enabled_strategy_names(self._registry_path):
            governance = self._governance_provider.get_strategy_governance(strategy_name=strategy_name)
            if not _is_normal_operating_strategy(governance):
                continue
            result.append(
                NormalOperatingStrategyDefinition(
                    strategy_name=str(getattr(governance, "strategy_name", strategy_name) or strategy_name),
                    strategy_role=str(getattr(governance, "strategy_role", "") or ""),
                    provides=tuple(str(item) for item in getattr(governance, "provides", ()) or ()),
                    maturity_stage=str(getattr(governance, "maturity_stage", "") or ""),
                    participation_mode=str(getattr(governance, "participation_mode", "") or ""),
                    decision_weight=str(getattr(governance, "decision_weight", "0") or "0"),
                    can_veto=bool(getattr(governance, "can_veto", False)),
                )
            )
        return tuple(result)

    def required_roles(self) -> tuple[str, ...]:
        """Return 23F required roles reused by 26B."""

        return tuple(self._governance_provider.get_aggregation_config().required_roles)

    def required_role_provides(self) -> dict[str, tuple[str, ...]]:
        """Return role-level required provides reused by 26B."""

        return {
            str(role): tuple(str(item) for item in provides)
            for role, provides in self._governance_provider.get_aggregation_config().required_role_provides.items()
        }


def _is_normal_operating_strategy(governance: Any) -> bool:
    if not bool(getattr(governance, "enabled", False)):
        return False
    if str(getattr(governance, "maturity_stage", "") or "").strip() != "active":
        return False
    mode = str(getattr(governance, "participation_mode", "") or "").strip()
    can_veto = bool(getattr(governance, "can_veto", False))
    decision_weight = _decimal(getattr(governance, "decision_weight", "0"))
    if mode == "observe_only" and decision_weight == Decimal("0") and not can_veto:
        return False
    return mode == "decision_participant" or can_veto


def _read_enabled_strategy_names(path: Path) -> tuple[str, ...]:
    if not path.exists():
        raise EvidenceAggregationConfigError(f"strategy registry not found: {path}")
    names: list[str] = []
    active_key: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            active_key = stripped.split(":", 1)[0].strip() if ":" in stripped else None
            continue
        if active_key == "enabled_strategies" and stripped.startswith("- "):
            value = stripped[2:].strip()
            if value:
                names.append(value)
    return tuple(names)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - invalid weights should not make placeholders required.
        return Decimal("0")


__all__ = ["StrategyEvidenceQualityConfigProvider"]
