"""DTOs and constants for 27A weak model / factor layer.

本文件属于 `app/weak_models` 模块，负责定义 27A 的 profile、输入、输出、
聚合结果、持久化 payload 和 CLI 渲染。
本文件不访问数据库，不请求 Binance，不发送 Hermes，不读写 Redis，不调用
DeepSeek/GPT/Claude，不读取账户或仓位，不生成订单，不自动交易。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import (
    DEFAULT_KLINE_SYMBOL,
    KLINE_1D_INTERVAL_VALUE,
    KLINE_4H_INTERVAL_VALUE,
    TRIGGER_SOURCE_CLI,
)

EXIT_SUCCESS = 0
EXIT_PARAMETER_ERROR = 1
EXIT_BLOCKED = 2
EXIT_FAILED = 4

WEAK_MODEL_ERROR_INVALID_OR_MISSING_SNAPSHOT = "invalid_or_missing_snapshot"
WEAK_MODEL_ERROR_INVALID_STRATEGY_SIGNAL_RUN_STATUS = "invalid_strategy_signal_run_status"
NON_TRADING_STATEMENT = "本运行只用于弱模型 / 因子层观测，不是交易建议；不自动交易，不读取账户，不生成订单。"


class WeakModelRole(str, Enum):
    """Supported role-specific output contracts."""

    DIRECTIONAL = "directional"
    RISK = "risk"
    CONFIRMATION = "confirmation"
    CONTEXT = "context"


class WeakModelMaturityStage(str, Enum):
    """Weak model maturity stages."""

    EXPERIMENTAL = "experimental"
    OBSERVE_ONLY = "observe_only"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class WeakModelRunStatus(str, Enum):
    """Batch status for one weak model run."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    BLOCKED = "blocked"
    FAILED = "failed"
    DRY_RUN = "dry_run"


class WeakModelResultStatus(str, Enum):
    """Status for one weak model output."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class WeakModelProfile:
    """Validated local config for one weak model profile."""

    model_key: str
    model_name: str
    enabled: bool
    maturity_stage: str
    model_role: str
    model_version: str
    config_version: str
    config_hash: str
    input_intervals: tuple[str, ...]
    input_window: Mapping[str, Any]
    static_weight: float
    description: str
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def participation_mode(self) -> str:
        """Return whether this profile can affect formal aggregation."""

        return "active" if self.enabled and self.maturity_stage == WeakModelMaturityStage.ACTIVE.value else "observe_only"

    @property
    def participates_in_aggregation(self) -> bool:
        """Return True only for enabled active profiles."""

        return self.participation_mode == "active" and self.static_weight > 0


@dataclass(frozen=True)
class WeakModelEvaluationInput:
    """Immutable input passed to each weak model.

    It is built only from the SSR-bound MarketContextSnapshot and restored
    formal Kline windows. It contains no account, position, order, model, or
    external-service state.
    """

    pipeline_run_id: str | None
    strategy_signal_run_id: str
    snapshot_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime
    base_klines: tuple[Any, ...]
    higher_klines: tuple[Any, ...]
    trace_id: str


@dataclass(frozen=True)
class WeakModelOutput:
    """Role-specific output from one weak model.

    Only fields for the model role are expected to be populated. This output is
    factor evidence only; it is not a final trading suggestion and does not
    contain entry, exit, stop, target, position size, leverage, or execution fields.
    """

    model_key: str
    model_role: str
    status: WeakModelResultStatus = WeakModelResultStatus.SUCCESS
    error_code: str | None = None
    error_message: str | None = None
    signal_score: float | None = None
    direction_bias: str | None = None
    risk_score: float | None = None
    risk_level: str | None = None
    can_veto: bool = False
    veto_triggered: bool = False
    trade_permission: str | None = None
    confirmation_score: float | None = None
    supports_direction: str | None = None
    context_regime: str | None = None
    context_score: float | None = None
    confidence: float = 0.0
    static_weight: float = 0.0
    effective_score: float = 0.0
    input_summary: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    raw_output: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WeakModelResultPayload:
    """Persistence payload for one `weak_model_result` row."""

    weak_model_result_id: str
    weak_model_run_id: str
    profile: WeakModelProfile
    output: WeakModelOutput
    input_data: WeakModelEvaluationInput


@dataclass(frozen=True)
class WeakModelAggregationSummary:
    """Role-separated weak model aggregation output."""

    weak_model_aggregation_id: str
    weak_model_run_id: str
    pipeline_run_id: str | None
    strategy_signal_run_id: str
    snapshot_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime
    directional_score: float
    directional_bias: str
    directional_confidence: float
    risk_level: str
    trade_permission: str
    veto_triggered: bool
    supporting_factors: tuple[str, ...]
    opposing_factors: tuple[str, ...]
    conflict_factors: tuple[str, ...]
    low_confidence_factors: tuple[str, ...]
    veto_factors: tuple[str, ...]
    context_summary: Mapping[str, Any]
    summary_text: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WeakModelRunRequest:
    """Input request for `WeakModelService`.

    27A 主输入必须是 `strategy_signal_run_id`。`kline_slot_utc` 只用于额外校验
    SSR 绑定 snapshot 的 slot，不会触发 15 ensure snapshot。
    """

    strategy_signal_run_id: str
    pipeline_run_id: str | None = None
    symbol: str = DEFAULT_KLINE_SYMBOL
    base_interval: str = KLINE_4H_INTERVAL_VALUE
    higher_interval: str = KLINE_1D_INTERVAL_VALUE
    kline_slot_utc: datetime | None = None
    trigger_source: str = TRIGGER_SOURCE_CLI
    dry_run: bool = True
    confirm_write: bool = False
    created_by: str = "cli"
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class WeakModelRunPersistencePayload:
    """Persistence payload for one `weak_model_run` row."""

    weak_model_run_id: str
    pipeline_run_id: str | None
    strategy_signal_run_id: str
    snapshot_id: str | None
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None
    run_status: str
    trigger_source: str
    model_count_total: int
    model_count_enabled: int
    model_count_executed: int
    model_count_failed: int
    trace_id: str
    details: Mapping[str, Any]


@dataclass(frozen=True)
class WeakModelRunResult:
    """Service result returned to CLI and tests."""

    status: WeakModelRunStatus
    exit_code: int
    weak_model_run_id: str
    trace_id: str
    strategy_signal_run_id: str
    snapshot_id: str | None = None
    weak_model_aggregation_id: str | None = None
    symbol: str = ""
    base_interval: str = ""
    higher_interval: str = ""
    kline_slot_utc: datetime | None = None
    model_count_total: int = 0
    model_count_enabled: int = 0
    model_count_executed: int = 0
    model_count_failed: int = 0
    database_written: bool = False
    database_action: str = "dry_run"
    blocked_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    outputs: tuple[WeakModelOutput, ...] = ()
    aggregation: WeakModelAggregationSummary | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


def build_weak_model_run_id(strategy_signal_run_id: str, *, trace_id: str) -> str:
    """Build an auditable weak model run id without querying storage."""

    safe_ssr = strategy_signal_run_id.replace(" ", "")[:64]
    return f"WMR-{safe_ssr}-{trace_id[:16]}"


def build_weak_model_result_id(weak_model_run_id: str, model_key: str) -> str:
    """Build a stable result id within one weak model run."""

    return f"WMRR-{weak_model_run_id}-{model_key}"[:180]


def build_weak_model_aggregation_id(weak_model_run_id: str) -> str:
    """Build a stable aggregation id within one weak model run."""

    return f"WMA-{weak_model_run_id}"[:180]


def status_exit_code(status: WeakModelRunStatus) -> int:
    """Map service status to CLI exit code."""

    if status in (WeakModelRunStatus.SUCCESS, WeakModelRunStatus.PARTIAL_SUCCESS, WeakModelRunStatus.DRY_RUN):
        return EXIT_SUCCESS
    if status == WeakModelRunStatus.BLOCKED:
        return EXIT_BLOCKED
    return EXIT_FAILED


def json_dumps_compact(value: Any) -> str:
    """Serialize compact JSON for bounded weak-model audit fields."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def format_weak_model_run_result_lines(result: WeakModelRunResult) -> list[str]:
    """Render CLI output without dumping Kline arrays or raw calculation internals."""

    lines = [
        f"status={result.status.value}",
        f"exit_code={result.exit_code}",
        f"weak_model_run_id={result.weak_model_run_id}",
        f"weak_model_aggregation_id={result.weak_model_aggregation_id or ''}",
        f"strategy_signal_run_id={result.strategy_signal_run_id}",
        f"snapshot_id={result.snapshot_id or ''}",
        f"symbol={result.symbol}",
        f"base_interval={result.base_interval}",
        f"higher_interval={result.higher_interval}",
        f"kline_slot_utc={result.kline_slot_utc.isoformat() if result.kline_slot_utc else ''}",
        f"model_count_total={result.model_count_total}",
        f"model_count_enabled={result.model_count_enabled}",
        f"model_count_executed={result.model_count_executed}",
        f"model_count_failed={result.model_count_failed}",
        f"database_written={str(result.database_written).lower()}",
        f"database_action={result.database_action}",
    ]
    if result.blocked_reason:
        lines.append(f"blocked_reason={result.blocked_reason}")
    if result.error_code:
        lines.append(f"error_code={result.error_code}")
    if result.error_message:
        lines.append(f"error_message={result.error_message}")
    if result.aggregation is not None:
        lines.extend(
            [
                f"directional_bias={result.aggregation.directional_bias}",
                f"directional_score={result.aggregation.directional_score:.4f}",
                f"directional_confidence={result.aggregation.directional_confidence:.4f}",
                f"risk_level={result.aggregation.risk_level}",
                f"trade_permission={result.aggregation.trade_permission}",
                f"veto_triggered={str(result.aggregation.veto_triggered).lower()}",
                f"context_regime={result.aggregation.context_summary.get('regime', '')}",
                f"summary_text={result.aggregation.summary_text}",
            ]
        )
    for output in result.outputs:
        lines.append(
            "model="
            f"{output.model_key} role={output.model_role} status={output.status.value} "
            f"confidence={output.confidence:.4f} effective_score={output.effective_score:.4f}"
        )
    lines.append(NON_TRADING_STATEMENT)
    return lines


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_FAILED",
    "EXIT_PARAMETER_ERROR",
    "EXIT_SUCCESS",
    "NON_TRADING_STATEMENT",
    "WEAK_MODEL_ERROR_INVALID_OR_MISSING_SNAPSHOT",
    "WEAK_MODEL_ERROR_INVALID_STRATEGY_SIGNAL_RUN_STATUS",
    "WeakModelAggregationSummary",
    "WeakModelEvaluationInput",
    "WeakModelMaturityStage",
    "WeakModelOutput",
    "WeakModelProfile",
    "WeakModelResultPayload",
    "WeakModelResultStatus",
    "WeakModelRole",
    "WeakModelRunPersistencePayload",
    "WeakModelRunRequest",
    "WeakModelRunResult",
    "WeakModelRunStatus",
    "build_weak_model_aggregation_id",
    "build_weak_model_result_id",
    "build_weak_model_run_id",
    "format_weak_model_run_result_lines",
    "json_dumps_compact",
    "status_exit_code",
]
