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
from app.strategy.strategies.market_direction_regime_strategy import MarketDirectionRegimeStrategy
from app.strategy.strategies.breakout_pullback_trigger_strategy import BreakoutPullbackTriggerStrategy
from app.strategy.strategies.short_term_range_strategy import ShortTermRangeStrategy
from app.strategy.strategies.support_resistance_strategy import SupportResistanceStrategy
from app.strategy.strategies.trend_structure_strategy import TrendStructureStrategy
from app.strategy.strategies.volatility_risk_control_strategy import VolatilityRiskControlStrategy
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
            strategy_config = _read_simple_yaml(_strategy_config_path(self._config_dir, strategy_name))
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
        MarketDirectionRegimeStrategy.strategy_name: MarketDirectionRegimeStrategy,
        ShortTermRangeStrategy.strategy_name: ShortTermRangeStrategy,
        SupportResistanceStrategy.strategy_name: SupportResistanceStrategy,
        BreakoutPullbackTriggerStrategy.strategy_name: BreakoutPullbackTriggerStrategy,
        VolatilityRiskControlStrategy.strategy_name: VolatilityRiskControlStrategy,
        VolatilityRiskStrategy.strategy_name: VolatilityRiskStrategy,
        GannPlaceholderStrategy.strategy_name: GannPlaceholderStrategy,
    }


def _strategy_config_path(config_dir: Path, strategy_name: str) -> Path:
    exact_path = config_dir / f"{strategy_name}.yaml"
    if exact_path.exists():
        return exact_path
    return config_dir / f"{strategy_name}_strategy.yaml"


def _validate_strategy(strategy: BaseStrategy, *, expected_name: str) -> None:
    if not isinstance(strategy, BaseStrategy):
        raise StrategyConfigError(f"strategy {expected_name} must inherit BaseStrategy")
    if strategy.strategy_name != expected_name:
        raise StrategyConfigError(
            f"strategy name mismatch: config={expected_name}, object={strategy.strategy_name}"
        )
    if not strategy.strategy_version:
        raise StrategyConfigError(f"strategy {expected_name} must define strategy_version")
    strategy_role = getattr(strategy, "strategy_role", None)
    if strategy_role is not None and not str(strategy_role).strip():
        raise StrategyConfigError(f"strategy {expected_name} must define strategy_role")
    provides = getattr(strategy, "provides", None)
    if provides is not None and not tuple(provides):
        raise StrategyConfigError(f"strategy {expected_name} must define provides")


def _read_simple_yaml(path: Path) -> dict[str, Any]:
    """Read the small YAML subset used by strategy configs.

    The parser intentionally supports only top-level scalars, top-level lists,
    and one-level nested mappings. This keeps `provides`, `lookback_bars`,
    `minimum_required_bars`, `thresholds`, and `output_limits` readable without
    adding a YAML dependency.
    """

    if not path.exists():
        raise StrategyConfigError(f"strategy config file not found: {path}")

    result: dict[str, Any] = {}
    active_key: str | None = None
    active_list_item: dict[str, Any] | None = None
    active_list_indent: int | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            active_key = _parse_top_level_line(path, raw_line, stripped, result)
            active_list_item = None
            active_list_indent = None
            continue
        if active_key is None:
            raise StrategyConfigError(f"nested config line without parent key in {path}: {raw_line}")
        active_list_item, active_list_indent = _parse_nested_line(
            path,
            raw_line,
            stripped,
            result,
            active_key,
            active_list_item=active_list_item,
            active_list_indent=active_list_indent,
            indent=indent,
        )
    return result


def _parse_top_level_line(
    path: Path,
    raw_line: str,
    stripped: str,
    result: dict[str, Any],
) -> str:
    if stripped.startswith("- "):
        raise StrategyConfigError(f"top-level list item without key in {path}: {raw_line}")
    if ":" not in stripped:
        raise StrategyConfigError(f"invalid config line in {path}: {raw_line}")
    key, value = stripped.split(":", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        raise StrategyConfigError(f"empty config key in {path}")
    if value == "":
        result[key] = []
    else:
        result[key] = _parse_scalar(value)
    return key


def _parse_nested_line(
    path: Path,
    raw_line: str,
    stripped: str,
    result: dict[str, Any],
    active_key: str,
    *,
    active_list_item: dict[str, Any] | None,
    active_list_indent: int | None,
    indent: int,
) -> tuple[dict[str, Any] | None, int | None]:
    parent = result.setdefault(active_key, [])
    if stripped.startswith("- "):
        if not isinstance(parent, list):
            raise StrategyConfigError(f"list item under mapping in {path}: {raw_line}")
        item_text = stripped[2:].strip()
        if ":" in item_text and not item_text.startswith(("'", '"')):
            key, value = item_text.split(":", 1)
            item = {key.strip(): _parse_scalar(value.strip()) if value.strip() else {}}
            parent.append(item)
            return item, indent
        parent.append(_parse_scalar(item_text))
        return None, None
    if ":" not in stripped:
        raise StrategyConfigError(f"invalid nested config line in {path}: {raw_line}")
    if (
        isinstance(parent, list)
        and active_list_item is not None
        and active_list_indent is not None
        and indent > active_list_indent
    ):
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise StrategyConfigError(f"empty nested config key in {path}")
        active_list_item[key] = _parse_scalar(value) if value else {}
        return active_list_item, active_list_indent
    if isinstance(parent, list):
        if parent:
            raise StrategyConfigError(f"cannot mix list and mapping values in {path}: {active_key}")
        result[active_key] = {}
        parent = result[active_key]
    if not isinstance(parent, dict):
        raise StrategyConfigError(f"nested mapping under scalar key in {path}: {active_key}")
    key, value = stripped.split(":", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        raise StrategyConfigError(f"empty nested config key in {path}")
    parent[key] = _parse_scalar(value) if value else {}
    return None, None


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
