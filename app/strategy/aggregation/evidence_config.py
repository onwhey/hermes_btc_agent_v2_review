"""Configuration loader for stage-23F strategy evidence aggregation.

This file belongs to `app/strategy/aggregation`. It reads non-sensitive local
YAML config files for strategy governance metadata and 23F aggregation defaults.
It does not instantiate strategies, rerun strategies, read strategy private
payloads, write databases, request Binance, send Hermes, call DeepSeek or any
large language model, read private trading state, generate final advice, or
perform trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.strategy.aggregation.evidence_types import ParticipationMode, StrategyGovernance

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STRATEGY_CONFIG_DIR = PROJECT_ROOT / "configs" / "strategies"
DEFAULT_EVIDENCE_AGGREGATION_CONFIG_PATH = PROJECT_ROOT / "configs" / "strategy_aggregation" / "evidence_aggregation.yaml"

ALLOWED_PARTICIPATION_MODES = {mode.value for mode in ParticipationMode}
ALLOWED_VETO_SCOPES = {"none", "long_candidate", "short_candidate", "current_candidate", "all_candidates"}


class EvidenceAggregationConfigError(RuntimeError):
    """Raised when stage-23F local config files are invalid."""


@dataclass(frozen=True)
class EvidenceAggregationConfig:
    """Runtime config for the stage-23F evidence aggregator.

    Parameters: required roles/provides and default governance values loaded
    from local YAML. Return value: immutable config object.
    Failure scenarios: invalid config is rejected before aggregation starts.
    External effects: none.
    """

    required_roles: tuple[str, ...]
    required_role_provides: dict[str, tuple[str, ...]]
    default_governance: StrategyGovernance


class StrategyGovernanceProvider:
    """Load governance metadata from strategy YAML without loading strategies.

    Parameters: strategy config directory and 23F config path.
    Return value: provider instance.
    Failure scenarios: unreadable or invalid YAML raises
    `EvidenceAggregationConfigError`.
    External effects: reads local config files only.
    """

    def __init__(
        self,
        *,
        strategy_config_dir: Path | None = None,
        aggregation_config_path: Path | None = None,
    ) -> None:
        self._strategy_config_dir = strategy_config_dir or DEFAULT_STRATEGY_CONFIG_DIR
        self._aggregation_config_path = aggregation_config_path or DEFAULT_EVIDENCE_AGGREGATION_CONFIG_PATH
        self._aggregation_config: EvidenceAggregationConfig | None = None
        self._governance_by_strategy: dict[str, StrategyGovernance] | None = None

    def get_aggregation_config(self) -> EvidenceAggregationConfig:
        """Return cached 23F aggregation config."""

        if self._aggregation_config is None:
            self._aggregation_config = _load_evidence_aggregation_config(self._aggregation_config_path)
        return self._aggregation_config

    def get_strategy_governance(self, *, strategy_name: str, strategy_role: str | None = None) -> StrategyGovernance:
        """Return governance metadata for one strategy, defaulting safely."""

        if self._governance_by_strategy is None:
            self._governance_by_strategy = _load_strategy_governance_map(
                self._strategy_config_dir,
                default_governance=self.get_aggregation_config().default_governance,
            )
        configured = self._governance_by_strategy.get(strategy_name)
        if configured is not None:
            return configured
        default = self.get_aggregation_config().default_governance
        return StrategyGovernance(
            strategy_name=strategy_name,
            strategy_role=strategy_role or "",
            provides=(),
            enabled=default.enabled,
            maturity_stage=default.maturity_stage,
            participation_mode=default.participation_mode,
            decision_weight=default.decision_weight,
            can_veto=default.can_veto,
            veto_scope=default.veto_scope,
            notification_required=default.notification_required,
        )


def create_default_strategy_governance_provider() -> StrategyGovernanceProvider:
    """Create the default 23F strategy governance provider."""

    return StrategyGovernanceProvider()


def _load_evidence_aggregation_config(path: Path) -> EvidenceAggregationConfig:
    raw = _read_simple_yaml(path)
    required_roles = tuple(_string_list(raw.get("required_roles", ())))
    required_role_provides = {
        str(role): tuple(_comma_list(provides))
        for role, provides in dict(raw.get("required_role_provides") or {}).items()
    }
    default_governance = _governance_from_raw(
        {
            "strategy_name": "default",
            "strategy_role": "",
            "provides": [],
            "enabled": True,
            "maturity_stage": raw.get("default_maturity_stage", "experimental"),
            "participation_mode": raw.get("default_participation_mode", ParticipationMode.OBSERVE_ONLY.value),
            "decision_weight": raw.get("default_decision_weight", "0"),
            "can_veto": raw.get("default_can_veto", False),
            "veto_scope": raw.get("default_veto_scope", "none"),
            "notification_required": raw.get("default_notification_required", True),
        }
    )
    if not required_roles:
        raise EvidenceAggregationConfigError("evidence_aggregation.yaml must define required_roles")
    return EvidenceAggregationConfig(
        required_roles=required_roles,
        required_role_provides=required_role_provides,
        default_governance=default_governance,
    )


def _load_strategy_governance_map(
    config_dir: Path,
    *,
    default_governance: StrategyGovernance,
) -> dict[str, StrategyGovernance]:
    if not config_dir.exists():
        raise EvidenceAggregationConfigError(f"strategy config directory not found: {config_dir}")
    result: dict[str, StrategyGovernance] = {}
    for path in sorted(config_dir.glob("*.yaml")):
        if path.name == "strategy_registry.yaml":
            continue
        raw = _read_simple_yaml(path)
        strategy_name = str(raw.get("strategy_name") or path.stem).strip()
        if not strategy_name:
            continue
        merged = {
            "strategy_name": strategy_name,
            "strategy_role": raw.get("strategy_role", ""),
            "provides": raw.get("provides", ()),
            "enabled": raw.get("enabled", True),
            "maturity_stage": raw.get("maturity_stage", default_governance.maturity_stage),
            "participation_mode": raw.get("participation_mode", default_governance.participation_mode),
            "decision_weight": raw.get("decision_weight", str(default_governance.decision_weight)),
            "can_veto": raw.get("can_veto", default_governance.can_veto),
            "veto_scope": raw.get("veto_scope", default_governance.veto_scope),
            "notification_required": raw.get("notification_required", default_governance.notification_required),
        }
        result[strategy_name] = _governance_from_raw(merged)
    return result


def _governance_from_raw(raw: dict[str, Any]) -> StrategyGovernance:
    participation_mode = str(raw.get("participation_mode") or ParticipationMode.OBSERVE_ONLY.value).strip()
    if participation_mode not in ALLOWED_PARTICIPATION_MODES:
        participation_mode = ParticipationMode.OBSERVE_ONLY.value
    veto_scope = str(raw.get("veto_scope") or "none").strip()
    if veto_scope not in ALLOWED_VETO_SCOPES:
        veto_scope = "none"
    return StrategyGovernance(
        strategy_name=str(raw.get("strategy_name") or "").strip(),
        strategy_role=str(raw.get("strategy_role") or "").strip(),
        provides=tuple(_string_list(raw.get("provides", ()))),
        enabled=bool(raw.get("enabled", True)),
        maturity_stage=str(raw.get("maturity_stage") or "experimental").strip(),
        participation_mode=participation_mode,
        decision_weight=_decimal(raw.get("decision_weight", "0")),
        can_veto=bool(raw.get("can_veto", False)),
        veto_scope=veto_scope,
        notification_required=bool(raw.get("notification_required", True)),
    )


def _read_simple_yaml(path: Path) -> dict[str, Any]:
    """Read the small YAML subset used by strategy and 23F configs.

    The parser supports top-level scalars, top-level lists, one-level nested
    mappings, and list items shaped as one-level mappings. This is enough for
    `provides`, `requires`, `consumes`, thresholds, and governance metadata
    without adding a YAML dependency.
    """

    if not path.exists():
        raise EvidenceAggregationConfigError(f"config file not found: {path}")
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
            raise EvidenceAggregationConfigError(f"nested config line without parent key in {path}: {raw_line}")
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


def _parse_top_level_line(path: Path, raw_line: str, stripped: str, result: dict[str, Any]) -> str:
    if stripped.startswith("- "):
        raise EvidenceAggregationConfigError(f"top-level list item without key in {path}: {raw_line}")
    if ":" not in stripped:
        raise EvidenceAggregationConfigError(f"invalid config line in {path}: {raw_line}")
    key, value = stripped.split(":", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        raise EvidenceAggregationConfigError(f"empty config key in {path}")
    result[key] = [] if value == "" else _parse_scalar(value)
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
            raise EvidenceAggregationConfigError(f"list item under mapping in {path}: {raw_line}")
        item_text = stripped[2:].strip()
        if ":" in item_text and not item_text.startswith(("'", '"')):
            key, value = item_text.split(":", 1)
            item = {key.strip(): _parse_scalar(value.strip()) if value.strip() else {}}
            parent.append(item)
            return item, indent
        parent.append(_parse_scalar(item_text))
        return None, None
    if ":" not in stripped:
        raise EvidenceAggregationConfigError(f"invalid nested config line in {path}: {raw_line}")
    if (
        isinstance(parent, list)
        and active_list_item is not None
        and active_list_indent is not None
        and indent > active_list_indent
    ):
        key, value = stripped.split(":", 1)
        active_list_item[key.strip()] = _parse_scalar(value.strip()) if value.strip() else {}
        return active_list_item, active_list_indent
    if isinstance(parent, list):
        if parent:
            raise EvidenceAggregationConfigError(f"cannot mix list and mapping values in {path}: {active_key}")
        result[active_key] = {}
        parent = result[active_key]
    if not isinstance(parent, dict):
        raise EvidenceAggregationConfigError(f"nested mapping under scalar key in {path}: {active_key}")
    key, value = stripped.split(":", 1)
    parent[key.strip()] = _parse_scalar(value.strip()) if value.strip() else {}
    return None, None


def _parse_scalar(value: str) -> Any:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none", "~"}:
        return None
    if (normalized.startswith('"') and normalized.endswith('"')) or (
        normalized.startswith("'") and normalized.endswith("'")
    ):
        return normalized[1:-1]
    try:
        if "." in normalized:
            return float(normalized)
        return int(normalized)
    except ValueError:
        return normalized


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip() if value is not None else ""
    return [text] if text else []


def _comma_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _string_list(value)
    text = str(value).strip() if value is not None else ""
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


__all__ = [
    "EvidenceAggregationConfig",
    "EvidenceAggregationConfigError",
    "StrategyGovernanceProvider",
    "create_default_strategy_governance_provider",
]
