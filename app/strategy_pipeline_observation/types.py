"""DTOs and rendering helpers for 26C strategy pipeline observation index.

本文件属于 `app/strategy_pipeline_observation` 模块。
本文件负责定义 26C-A 的请求、候选 pipeline、质量摘要、observation payload、
service 结果和中文 CLI 输出。
本文件不负责数据库查询或写入，不请求 Binance，不发送 Hermes，不调用
DeepSeek 或其他大模型，不读写 Redis，不读取账户或仓位，不生成订单，不自动交易。

主要调用方：
- `app/strategy_pipeline_observation/service.py`
- `app/strategy_pipeline_observation/repository.py`
- `scripts/build_strategy_pipeline_observations.py`

外部服务：不访问。
MySQL：不读写。
Redis：不读写。
Hermes：不发送。
模型：不调用。
交易执行：不涉及。
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
EXIT_PARAMETER_OR_DATABASE_ERROR = 2

OBSERVATION_STATUS_MISSING_PIPELINE = "missing_pipeline"
OBSERVATION_STATUS_ONLY_CLI_RUNS = "only_cli_runs"
OBSERVATION_STATUS_PIPELINE_FAILED = "pipeline_failed"
OBSERVATION_STATUS_QUALITY_BLOCKED = "quality_blocked"
OBSERVATION_STATUS_EXPECTED_BLOCKED_BY_MODEL_CONFIG = "expected_blocked_by_model_config"
OBSERVATION_STATUS_MODEL_REVIEW_COMPLETED = "model_review_completed"
OBSERVATION_STATUS_ADVICE_GENERATED = "advice_generated"
OBSERVATION_STATUS_NOTIFICATION_PREPARED = "notification_prepared"
OBSERVATION_STATUS_NOTIFICATION_SENT = "notification_sent"
OBSERVATION_STATUS_UNKNOWN = "unknown"

CANONICAL_REASON_NO_PIPELINE = "no_pipeline_for_kline_slot"
CANONICAL_REASON_ONLY_CLI_RUNS = "only_cli_runs_excluded_from_formal_sample"
CANONICAL_REASON_SCHEDULER_SELECTED = "scheduler_pipeline_selected"

NON_TRADING_STATEMENT = (
    "本检查只用于策略链路观察索引构建，不是交易建议；不自动交易，不读取账户，不生成订单。"
)
READ_ONLY_SOURCE_STATEMENT = (
    "26C-A 只读取已入库 K线和已有策略链路结果；不请求 Binance REST，不重新运行 16/23F/26B/18/20/21。"
)


class StrategyPipelineObservationStatus(str, Enum):
    """Stable 26C observation status values stored in MySQL."""

    MISSING_PIPELINE = OBSERVATION_STATUS_MISSING_PIPELINE
    ONLY_CLI_RUNS = OBSERVATION_STATUS_ONLY_CLI_RUNS
    PIPELINE_FAILED = OBSERVATION_STATUS_PIPELINE_FAILED
    QUALITY_BLOCKED = OBSERVATION_STATUS_QUALITY_BLOCKED
    EXPECTED_BLOCKED_BY_MODEL_CONFIG = OBSERVATION_STATUS_EXPECTED_BLOCKED_BY_MODEL_CONFIG
    MODEL_REVIEW_COMPLETED = OBSERVATION_STATUS_MODEL_REVIEW_COMPLETED
    ADVICE_GENERATED = OBSERVATION_STATUS_ADVICE_GENERATED
    NOTIFICATION_PREPARED = OBSERVATION_STATUS_NOTIFICATION_PREPARED
    NOTIFICATION_SENT = OBSERVATION_STATUS_NOTIFICATION_SENT
    UNKNOWN = OBSERVATION_STATUS_UNKNOWN


@dataclass(frozen=True)
class StrategyPipelineObservationBuildRequest:
    """Input for one 26C-A observation-index build.

    参数：
    - `symbol/base_interval/higher_interval`：观察索引的 market scope。
    - `limit`：没有指定单个 slot 时读取最近 N 根已入库 4h K线。
    - `kline_slot_utc`：可选的精确 4h K线 open time。
    - `dry_run`：只计算不写库。
    - `confirm_write`：显式确认后才写 `strategy_pipeline_observation`。
    - `refresh_existing`：写库时允许更新既有 observation。
    - `trigger_source`：第一版只允许用户 CLI。

    返回值：由 service 转换为 `StrategyPipelineObservationBuildReport`。
    失败场景：参数非法由 CLI 返回 exit_code=2。
    外部服务：不访问。
    数据影响：本对象不读写 MySQL、Redis，不发送 Hermes，不调用模型。
    """

    symbol: str = DEFAULT_KLINE_SYMBOL
    base_interval: str = KLINE_4H_INTERVAL_VALUE
    higher_interval: str = KLINE_1D_INTERVAL_VALUE
    limit: int = 10
    kline_slot_utc: datetime | None = None
    dry_run: bool = True
    confirm_write: bool = False
    refresh_existing: bool = False
    trigger_source: str = TRIGGER_SOURCE_CLI
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class ObservationConfigSnapshot:
    """Small non-sensitive config snapshot used for model-block classification."""

    strategy_pipeline_real_model_enabled: bool
    strategy_pipeline_confirm_real_model_cost: bool
    model_review_real_model_enabled: bool
    strategy_pipeline_notification_send_enabled: bool
    strategy_advice_notification_send_enabled: bool

    @property
    def real_model_allowed_for_pipeline(self) -> bool:
        """Return whether all pipeline-level real-model gates are open."""

        return (
            self.strategy_pipeline_real_model_enabled
            and self.strategy_pipeline_confirm_real_model_cost
            and self.model_review_real_model_enabled
        )

    @property
    def real_hermes_allowed_for_pipeline(self) -> bool:
        """Return whether real Hermes notification gates are open."""

        return self.strategy_pipeline_notification_send_enabled and self.strategy_advice_notification_send_enabled


@dataclass(frozen=True)
class KlineSlotObservationSource:
    """One formal 4h Kline slot read from `market_kline_4h`."""

    open_time_utc: datetime
    open_time_prc: datetime | None = None
    close_time_utc: datetime | None = None
    close_time_prc: datetime | None = None


@dataclass(frozen=True)
class PipelineRunCandidate:
    """Compact projection of one existing `strategy_pipeline_event_log` row."""

    pipeline_run_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None
    trigger_source: str
    status: str
    current_step: str | None = None
    strategy_signal_run_id: str | None = None
    strategy_evidence_aggregation_id: str | None = None
    material_pack_id: str | None = None
    model_analysis_run_id: str | None = None
    review_aggregation_run_id: str | None = None
    advice_id: str | None = None
    review_id: str | None = None
    notification_status: str | None = None
    model_review_invoked: bool = False
    model_review_reused: bool = False
    real_model_called: bool = False
    hermes_real_sent: bool = False
    error_code: str | None = None
    error_message: str | None = None
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None
    id: int | None = None


@dataclass(frozen=True)
class EvidenceQualitySummary:
    """Compact 26B quality check facts for one pipeline run."""

    quality_check_id: str | None = None
    status: str | None = None
    should_block_pipeline: bool = False
    failed_roles: tuple[str, ...] = ()
    failed_strategies: tuple[str, ...] = ()
    alert_message_id: int | None = None


@dataclass(frozen=True)
class AdviceLinkSummary:
    """Compact stage-21/advice facts resolved for one pipeline run."""

    advice_id: str | None = None
    review_id: str | None = None
    alert_message_id: int | None = None


@dataclass(frozen=True)
class ExcludedPipelineSummary:
    """One non-canonical pipeline and the reason it was excluded."""

    pipeline_run_id: str
    trigger_source: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        """Return a compact JSON-safe representation."""

        return {
            "pipeline_run_id": self.pipeline_run_id,
            "trigger_source": self.trigger_source,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StrategyPipelineObservationPayload:
    """Complete compact payload persisted to `strategy_pipeline_observation`."""

    observation_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime
    kline_open_time_prc: datetime | None
    kline_close_time_utc: datetime | None
    kline_close_time_prc: datetime | None
    canonical_pipeline_run_id: str | None
    canonical_trigger_source: str | None
    canonical_reason: str
    duplicate_pipeline_count: int
    excluded_pipeline_run_ids: tuple[Mapping[str, Any], ...]
    observation_status: str
    eligible_for_review: bool
    eligible_for_advice_performance_review: bool
    pipeline_status: str | None
    pipeline_current_step: str | None
    pipeline_error_code: str | None
    pipeline_error_message: str | None
    strategy_signal_run_id: str | None
    strategy_evidence_aggregation_id: str | None
    evidence_quality_check_id: str | None
    material_pack_id: str | None
    model_analysis_run_id: str | None
    review_aggregation_run_id: str | None
    advice_id: str | None
    review_id: str | None
    alert_message_id: int | None
    evidence_quality_status: str | None
    evidence_quality_should_block: bool
    evidence_quality_failed_roles: tuple[str, ...]
    evidence_quality_failed_strategies: tuple[str, ...]
    model_review_invoked: bool
    model_review_reused: bool
    real_model_called: bool
    real_model_blocked_by_config: bool
    hermes_real_sent: bool
    notification_status: str | None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyPipelineObservationResult:
    """One 26C-A per-slot build result."""

    payload: StrategyPipelineObservationPayload
    database_action: str
    database_written: bool
    excluded_reason_summary: Mapping[str, int]


@dataclass(frozen=True)
class StrategyPipelineObservationBuildReport:
    """Full 26C-A report returned by the service and rendered by CLI."""

    request: StrategyPipelineObservationBuildRequest
    results: tuple[StrategyPipelineObservationResult, ...]
    exit_code: int
    dry_run: bool
    confirm_write: bool


def build_strategy_pipeline_observation_id(
    *,
    symbol: str,
    base_interval: str,
    higher_interval: str,
    kline_slot_utc: datetime,
) -> str:
    """Build the stable 26C business id from market scope and Kline slot."""

    slot_text = kline_slot_utc.strftime("%Y%m%dT%H%M%SZ")
    return f"SPO-{symbol}-{base_interval.upper()}-{higher_interval.upper()}-{slot_text}"


def format_strategy_pipeline_observation_report_lines(
    report: StrategyPipelineObservationBuildReport,
) -> list[str]:
    """Render a compact Chinese CLI report without dumping large JSON bodies."""

    lines: list[str] = [
        "策略链路观察索引构建",
        (
            f"symbol={report.request.symbol} "
            f"base_interval={report.request.base_interval} "
            f"higher_interval={report.request.higher_interval} "
            f"limit={report.request.limit}"
        ),
        f"kline_slot_utc={_format_optional_utc(report.request.kline_slot_utc)}",
        f"dry_run={_bool_text(report.dry_run)} confirm_write={_bool_text(report.confirm_write)}",
        f"观测边界：{READ_ONLY_SOURCE_STATEMENT}",
        "",
        "明细：",
    ]
    for result in report.results:
        payload = result.payload
        lines.extend(
            [
                f"[{_format_utc(payload.kline_slot_utc)}]",
                f"- canonical_pipeline_run_id：{payload.canonical_pipeline_run_id or '无'}",
                f"- observation_status：{payload.observation_status}",
                f"- eligible_for_review：{_bool_text(payload.eligible_for_review)}",
                (
                    "- eligible_for_advice_performance_review："
                    f"{_bool_text(payload.eligible_for_advice_performance_review)}"
                ),
                f"- 26B 状态：{payload.evidence_quality_status or '无'}",
                f"- 模型状态：{_model_status_text(payload)}",
                f"- advice 状态：{_advice_status_text(payload)}",
                f"- duplicate_pipeline_count：{payload.duplicate_pipeline_count}",
                f"- excluded pipeline 数量：{len(payload.excluded_pipeline_run_ids)}",
                f"- excluded 原因摘要：{_json_dumps(dict(result.excluded_reason_summary))}",
                f"- database_action：{result.database_action}",
                f"- database_written：{_bool_text(result.database_written)}",
                "",
            ]
        )
    lines.append(NON_TRADING_STATEMENT)
    return lines


def json_dumps_compact(value: Any) -> str:
    """Serialize compact JSON for bounded observation details fields."""

    return _json_dumps(value)


def _model_status_text(payload: StrategyPipelineObservationPayload) -> str:
    if payload.real_model_blocked_by_config:
        return "真实模型关闭，合理阻断"
    if payload.real_model_called:
        return "已调用真实模型"
    if payload.model_review_invoked or payload.review_aggregation_run_id:
        return "模型审查已进入或已聚合"
    return "未进入模型审查"


def _advice_status_text(payload: StrategyPipelineObservationPayload) -> str:
    if payload.hermes_real_sent:
        return "Hermes 已真实发送"
    if payload.notification_status:
        return f"通知状态={payload.notification_status}"
    if payload.advice_id or payload.review_id:
        return "advice 已生成"
    return "无 advice"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _format_optional_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    return _format_utc(value)


def _format_utc(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


__all__ = [
    "CANONICAL_REASON_NO_PIPELINE",
    "CANONICAL_REASON_ONLY_CLI_RUNS",
    "CANONICAL_REASON_SCHEDULER_SELECTED",
    "EXIT_PARAMETER_OR_DATABASE_ERROR",
    "EXIT_SUCCESS",
    "EvidenceQualitySummary",
    "KlineSlotObservationSource",
    "NON_TRADING_STATEMENT",
    "OBSERVATION_STATUS_ADVICE_GENERATED",
    "OBSERVATION_STATUS_EXPECTED_BLOCKED_BY_MODEL_CONFIG",
    "OBSERVATION_STATUS_MISSING_PIPELINE",
    "OBSERVATION_STATUS_MODEL_REVIEW_COMPLETED",
    "OBSERVATION_STATUS_NOTIFICATION_PREPARED",
    "OBSERVATION_STATUS_NOTIFICATION_SENT",
    "OBSERVATION_STATUS_ONLY_CLI_RUNS",
    "OBSERVATION_STATUS_PIPELINE_FAILED",
    "OBSERVATION_STATUS_QUALITY_BLOCKED",
    "OBSERVATION_STATUS_UNKNOWN",
    "ObservationConfigSnapshot",
    "PipelineRunCandidate",
    "READ_ONLY_SOURCE_STATEMENT",
    "StrategyPipelineObservationBuildReport",
    "StrategyPipelineObservationBuildRequest",
    "StrategyPipelineObservationPayload",
    "StrategyPipelineObservationResult",
    "StrategyPipelineObservationStatus",
    "AdviceLinkSummary",
    "ExcludedPipelineSummary",
    "build_strategy_pipeline_observation_id",
    "format_strategy_pipeline_observation_report_lines",
    "json_dumps_compact",
]
