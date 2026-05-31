"""DTOs and rendering helpers for strategy pipeline observability.

本文件属于 `app/strategy_observability` 模块，负责定义 26A 只读观测所需的
输入、输出、状态枚举和中文 CLI 渲染。
本文件不负责数据库查询，不负责调用 25 pipeline，不负责调用真实模型，不负责发送
Hermes，不读写 Redis，不读取账户或持仓，不生成订单，不涉及自动交易。

主要调用方：
- `app/strategy_observability/service.py`
- `scripts/check_strategy_pipeline_status.py`

外部服务：不访问。
MySQL：不读写。
Redis：不读写。
Hermes：不发送。
DeepSeek/其他大模型：不调用。
交易执行：不涉及。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping

from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE

EXIT_SUCCESS = 0
EXIT_UNHEALTHY = 1
EXIT_PARAMETER_OR_DATABASE_ERROR = 2

OBSERVATION_STATUS_HEALTHY = "healthy"
OBSERVATION_STATUS_EXPECTED_BLOCKED = "expected_blocked"
OBSERVATION_STATUS_FAILED = "failed"
OBSERVATION_STATUS_MISSING = "missing"
OBSERVATION_STATUS_DUPLICATE = "duplicate"
OBSERVATION_STATUS_UNKNOWN = "unknown"

NON_TRADING_STATEMENT = "本检查只用于策略链路运行观测，不是交易建议；不自动交易，不读取账户，不生成订单。"
KLINE_SCOPE_STATEMENT = "26A 只观测已入库 K线对应的策略链路。"
KLINE_QUALITY_SCOPE_STATEMENT = (
    "K线本身是否漏采、是否连续，仍由 07/11 K线质量检查负责；26A 不请求 Binance REST 推断理论应收盘 slot。"
)


class SlotObservationStatus(str, Enum):
    """Stable per-slot observability status values."""

    HEALTHY = OBSERVATION_STATUS_HEALTHY
    EXPECTED_BLOCKED = OBSERVATION_STATUS_EXPECTED_BLOCKED
    FAILED = OBSERVATION_STATUS_FAILED
    MISSING = OBSERVATION_STATUS_MISSING
    DUPLICATE = OBSERVATION_STATUS_DUPLICATE
    UNKNOWN = OBSERVATION_STATUS_UNKNOWN


@dataclass(frozen=True)
class StrategyPipelineStatusRequest:
    """Input for the read-only strategy pipeline status check.

    参数：
    - `symbol`：交易对，第一版默认 BTCUSDT。
    - `base_interval`：基础周期，第一版只读正式 4h K线表。
    - `higher_interval`：高级别周期，第一版默认 1d。
    - `limit`：最近 N 根已收盘 4h K线 slot。

    返回值：由 service 转换为 `StrategyPipelineStatusReport`。
    失败场景：参数非法由 CLI/service 返回 exit_code=2。
    外部服务：不访问。
    数据影响：本对象不读写 MySQL、Redis，不发送 Hermes，不调用模型。
    """

    symbol: str = DEFAULT_KLINE_SYMBOL
    base_interval: str = KLINE_4H_INTERVAL_VALUE
    higher_interval: str = KLINE_1D_INTERVAL_VALUE
    limit: int = 5


@dataclass(frozen=True)
class ObservabilityConfigSnapshot:
    """Small non-sensitive config snapshot used to judge expected blocking."""

    strategy_pipeline_enabled: bool
    strategy_pipeline_scheduler_enabled: bool
    strategy_evidence_aggregation_enabled: bool
    strategy_pipeline_real_model_enabled: bool
    strategy_pipeline_confirm_real_model_cost: bool
    model_review_real_model_enabled: bool
    strategy_pipeline_notification_send_enabled: bool
    strategy_advice_notification_send_enabled: bool

    @property
    def real_model_allowed_for_pipeline(self) -> bool:
        """Return whether all pipeline-level real-model gates are enabled."""

        return (
            self.strategy_pipeline_real_model_enabled
            and self.strategy_pipeline_confirm_real_model_cost
            and self.model_review_real_model_enabled
        )

    @property
    def real_hermes_allowed_for_pipeline(self) -> bool:
        """Return whether all pipeline-level real-Hermes gates are enabled."""

        return self.strategy_pipeline_notification_send_enabled and self.strategy_advice_notification_send_enabled

    def as_display_items(self) -> Mapping[str, bool]:
        """Return the exact 26A config keys required in the CLI output."""

        return {
            "STRATEGY_PIPELINE_ENABLED": self.strategy_pipeline_enabled,
            "STRATEGY_PIPELINE_SCHEDULER_ENABLED": self.strategy_pipeline_scheduler_enabled,
            "STRATEGY_EVIDENCE_AGGREGATION_ENABLED": self.strategy_evidence_aggregation_enabled,
            "STRATEGY_PIPELINE_REAL_MODEL_ENABLED": self.strategy_pipeline_real_model_enabled,
            "STRATEGY_PIPELINE_CONFIRM_REAL_MODEL_COST": self.strategy_pipeline_confirm_real_model_cost,
            "MODEL_REVIEW_REAL_MODEL_ENABLED": self.model_review_real_model_enabled,
            "STRATEGY_PIPELINE_NOTIFICATION_SEND_ENABLED": self.strategy_pipeline_notification_send_enabled,
            "STRATEGY_ADVICE_NOTIFICATION_SEND_ENABLED": self.strategy_advice_notification_send_enabled,
        }


@dataclass(frozen=True)
class KlineSlotRecord:
    """One observed closed base Kline slot from the formal Kline table."""

    open_time_utc: datetime
    open_time_ms: int | None = None


@dataclass(frozen=True)
class StrategyPipelineRunRecord:
    """Compact read-only projection of one `strategy_pipeline_event_log` row."""

    pipeline_run_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None
    status: str
    current_step: str | None
    strategy_signal_run_id: str | None
    strategy_evidence_aggregation_id: str | None
    material_pack_id: str | None
    review_aggregation_run_id: str | None
    advice_id: str | None
    review_id: str | None
    notification_status: str | None
    real_model_called: bool
    hermes_real_sent: bool
    error_code: str | None
    error_message: str | None
    created_at_utc: datetime | None = None
    id: int | None = None


@dataclass(frozen=True)
class StrategyPipelineLinkRecord:
    """Resolved SP/SSR/SEA/AMP/MRAG/ADVR ids and table-existence flags."""

    pipeline_run_id: str | None = None
    strategy_signal_run_id: str | None = None
    strategy_signal_run_exists: bool = False
    strategy_evidence_aggregation_id: str | None = None
    strategy_evidence_aggregation_exists: bool = False
    material_pack_id: str | None = None
    material_pack_exists: bool = False
    review_aggregation_run_id: str | None = None
    review_aggregation_run_exists: bool = False
    advice_lifecycle_review_id: str | None = None
    advice_lifecycle_review_exists: bool = False


@dataclass(frozen=True)
class StrategyPipelineSlotObservation:
    """One slot-level 26A observation result.

    `blocked_reasonable` is `True` only when the observed block matches current
    safe-mode configuration. `None` means the slot is not a blocked pipeline.
    """

    slot_utc: datetime
    status: SlotObservationStatus
    reason: str
    kline_open_time_ms: int | None = None
    pipeline_run_ids: tuple[str, ...] = ()
    pipeline_status: str | None = None
    current_step: str | None = None
    links: StrategyPipelineLinkRecord = field(default_factory=StrategyPipelineLinkRecord)
    real_model_called: bool = False
    hermes_real_sent: bool = False
    error_code: str | None = None
    error_message: str | None = None
    blocked_reasonable: bool | None = None


@dataclass(frozen=True)
class StrategyPipelineStatusReport:
    """Full 26A read-only report returned by the service and rendered by CLI."""

    request: StrategyPipelineStatusRequest
    config: ObservabilityConfigSnapshot
    observations: tuple[StrategyPipelineSlotObservation, ...]
    exit_code: int
    summary_counts: Mapping[str, int]


def format_strategy_pipeline_status_report_lines(report: StrategyPipelineStatusReport) -> list[str]:
    """Render a compact Chinese CLI report without dumping large JSON columns."""

    lines: list[str] = [
        "策略链路运行观测",
        (
            f"symbol={report.request.symbol} "
            f"base_interval={report.request.base_interval} "
            f"higher_interval={report.request.higher_interval} "
            f"limit={report.request.limit}"
        ),
        f"观测范围：{KLINE_SCOPE_STATEMENT}",
        f"质量边界：{KLINE_QUALITY_SCOPE_STATEMENT}",
        "",
        "汇总：",
        f"- 检查 slot 数：{len(report.observations)}",
    ]
    for status in (
        SlotObservationStatus.HEALTHY.value,
        SlotObservationStatus.EXPECTED_BLOCKED.value,
        SlotObservationStatus.FAILED.value,
        SlotObservationStatus.MISSING.value,
        SlotObservationStatus.DUPLICATE.value,
        SlotObservationStatus.UNKNOWN.value,
    ):
        lines.append(f"- {status}：{int(report.summary_counts.get(status, 0))}")
    lines.extend(
        [
            f"- 当前真实模型：{_enabled_text(report.config.real_model_allowed_for_pipeline)}",
            f"- 当前真实 Hermes：{_enabled_text(report.config.real_hermes_allowed_for_pipeline)}",
            f"- 退出码：{report.exit_code}",
            "",
            "关键配置：",
        ]
    )
    for key, value in report.config.as_display_items().items():
        lines.append(f"- {key}：{_bool_text(value)}")
    lines.append("")
    lines.append("明细：")
    for observation in report.observations:
        lines.extend(_format_observation_lines(observation))
    lines.append(NON_TRADING_STATEMENT)
    return lines


def _format_observation_lines(observation: StrategyPipelineSlotObservation) -> list[str]:
    pipeline_ids = ", ".join(observation.pipeline_run_ids)
    if not pipeline_ids:
        pipeline_ids = "缺失"
    elif len(observation.pipeline_run_ids) > 1:
        pipeline_ids = f"重复 {len(observation.pipeline_run_ids)} 条：{pipeline_ids}"
    links = observation.links
    return [
        f"[{_format_utc(observation.slot_utc)}]",
        f"状态：{observation.status.value}",
        f"说明：{observation.reason}",
        f"- SP / pipeline_run_id：{pipeline_ids}",
        f"- pipeline_status：{observation.pipeline_status or ''}",
        f"- current_step：{observation.current_step or ''}",
        _format_link("SSR", links.strategy_signal_run_id, links.strategy_signal_run_exists),
        _format_link("SEA", links.strategy_evidence_aggregation_id, links.strategy_evidence_aggregation_exists),
        _format_link("AMP", links.material_pack_id, links.material_pack_exists),
        _format_link("MRAG", links.review_aggregation_run_id, links.review_aggregation_run_exists),
        _format_link("ADVR", links.advice_lifecycle_review_id, links.advice_lifecycle_review_exists),
        f"- real_model_called：{_bool_text(observation.real_model_called)}",
        f"- hermes_real_sent：{_bool_text(observation.hermes_real_sent)}",
        f"- error_code：{observation.error_code or ''}",
        f"- error_message：{observation.error_message or ''}",
        f"- blocked 是否合理：{_blocked_text(observation.blocked_reasonable)}",
        "",
    ]


def _format_link(label: str, link_id: str | None, exists: bool) -> str:
    if link_id and exists:
        return f"- {label}：存在 {link_id}"
    if link_id:
        return f"- {label}：ID存在但未确认 {link_id}"
    return f"- {label}：缺失"


def _blocked_text(value: bool | None) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "不适用"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _enabled_text(value: bool) -> str:
    return "开启" if value else "关闭"


def _format_utc(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "EXIT_PARAMETER_OR_DATABASE_ERROR",
    "EXIT_SUCCESS",
    "EXIT_UNHEALTHY",
    "KLINE_QUALITY_SCOPE_STATEMENT",
    "KLINE_SCOPE_STATEMENT",
    "KlineSlotRecord",
    "NON_TRADING_STATEMENT",
    "OBSERVATION_STATUS_DUPLICATE",
    "OBSERVATION_STATUS_EXPECTED_BLOCKED",
    "OBSERVATION_STATUS_FAILED",
    "OBSERVATION_STATUS_HEALTHY",
    "OBSERVATION_STATUS_MISSING",
    "OBSERVATION_STATUS_UNKNOWN",
    "ObservabilityConfigSnapshot",
    "SlotObservationStatus",
    "StrategyPipelineLinkRecord",
    "StrategyPipelineRunRecord",
    "StrategyPipelineSlotObservation",
    "StrategyPipelineStatusReport",
    "StrategyPipelineStatusRequest",
    "format_strategy_pipeline_status_report_lines",
]
