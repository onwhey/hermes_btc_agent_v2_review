"""Strategy registry for stage-16 enabled strategy loading.

This file belongs to `app/strategy`. It loads non-sensitive strategy config
files and returns enabled `BaseStrategy` instances.
It does not run strategies, write databases, request Binance, send Hermes, call
large language models, read account/position state, generate final advice, or
trade.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from app.strategy.base import BaseStrategy
from app.strategy.strategies.gann_placeholder_strategy import GannPlaceholderStrategy
from app.strategy.strategies.trend_structure_strategy import TrendStructureStrategy
from app.strategy.strategies.volatility_risk_strategy import VolatilityRiskStrategy
from app.strategy.types import StrategyConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRATEGY_CONFIG_DIR = PROJECT_ROOT / "configs" / "strategies"


class StrategyRegistry:
    """Load and validate enabled strategy objects.

    Parameters: config directory and optional strategy class map.
    Return value: registry instance.
    Failure scenarios: missing config, duplicate names, disabled/missing strategy
    config, or invalid strategy class raises `StrategyConfigError`.
    External service access: none.
    Data impact: reads local non-sensitive config files only.
    """

    def __init__(
        self,
        *,
        config_dir: Path | None = None,
        strategy_classes: Mapping[str, type[BaseStrategy]] | None = None,
    ) -> None:
        self._config_dir = config_dir or DEFAULT_STRATEGY_CONFIG_DIR
        self._strategy_classes = dict(strategy_classes or _default_strategy_classes())

    def load_enabled_strategies(self) -> tuple[BaseStrategy, ...]:
        """Return enabled strategies in registry-config order."""

        registry_config = _read_simple_yaml(self._config_dir / "strategy_registry.yaml")
        enabled_names = registry_config.get("enabled_strategies")
        if not isinstance(enabled_names, list) or not enabled_names:
            raise StrategyConfigError("strategy_registry.yaml must define enabled_strategies")

        strategies: list[BaseStrategy] = []
        seen: set[str] = set()
        for raw_name in enabled_names:
            strategy_name = str(raw_name).strip()
            if not strategy_name:
                raise StrategyConfigError("enabled strategy name must not be empty")
            if strategy_name in seen:
                raise StrategyConfigError(f"duplicate strategy name configured: {strategy_name}")
            seen.add(strategy_name)
            strategy_class = self._strategy_classes.get(strategy_name)
            if strategy_class is None:
                raise StrategyConfigError(f"unsupported strategy configured: {strategy_name}")
            strategy_config = _read_simple_yaml(self._config_dir / f"{strategy_name}_strategy.yaml")
            if not bool(strategy_config.get("enabled", True)):
                continue
            strategy = strategy_class(strategy_config)
            _validate_strategy(strategy, expected_name=strategy_name)
            strategies.append(strategy)

        if not strategies:
            raise StrategyConfigError("no enabled strategies loaded")
        return tuple(strategies)


def create_default_strategy_registry() -> StrategyRegistry:
    """Create the default stage-16 strategy registry."""

    return StrategyRegistry()


def _default_strategy_classes() -> dict[str, type[BaseStrategy]]:
    return {
        TrendStructureStrategy.strategy_name: TrendStructureStrategy,
        VolatilityRiskStrategy.strategy_name: VolatilityRiskStrategy,
        GannPlaceholderStrategy.strategy_name: GannPlaceholderStrategy,
    }


def _validate_strategy(strategy: BaseStrategy, *, expected_name: str) -> None:
    if not isinstance(strategy, BaseStrategy):
        raise StrategyConfigError(f"strategy {expected_name} must inherit BaseStrategy")
    if strategy.strategy_name != expected_name:
        raise StrategyConfigError(
            f"strategy name mismatch: config={expected_name}, object={strategy.strategy_name}"
        )
    if not strategy.strategy_version:
        raise StrategyConfigError(f"strategy {expected_name} must define strategy_version")


def _read_simple_yaml(path: Path) -> dict[str, Any]:
    """Read the small YAML subset used by stage-16 strategy configs.

    The parser intentionally supports only `key: value` scalars and a top-level
    `key:` followed by `- item` list. This avoids adding a dependency while
    keeping configs readable and non-sensitive.
    """

    if not path.exists():
        raise StrategyConfigError(f"strategy config file not found: {path}")

    result: dict[str, Any] = {}
    active_list_key: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if active_list_key is None:
                raise StrategyConfigError(f"list item without key in {path}")
            result.setdefault(active_list_key, []).append(_parse_scalar(stripped[2:].strip()))
            continue
        active_list_key = None
        if ":" not in stripped:
            raise StrategyConfigError(f"invalid config line in {path}: {raw_line}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise StrategyConfigError(f"empty config key in {path}")
        if value == "":
            result[key] = []
            active_list_key = key
        else:
            result[key] = _parse_scalar(value)
    return result


def _parse_scalar(value: str) -> Any:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in normalized:
            return float(normalized)
        return int(normalized)
    except ValueError:
        return normalized.strip("'\"")


__all__ = [
    "StrategyRegistry",
    "create_default_strategy_registry",
]

