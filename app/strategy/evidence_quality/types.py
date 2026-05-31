"""26B strategy evidence quality gate DTOs and formatting helpers.

本文件属于 `app/strategy/evidence_quality` 模块。
本文件负责定义 26B 质量闸门的请求、结果、持久化负载和只读 CLI 输出结构。
本文件不负责数据库查询，不负责发送 Hermes，不负责调用大模型，不负责生成策略建议，
不读取账户或仓位，不涉及任何交易执行。
主要被 `service.py`、`repository.py`、`scripts/check_strategy_evidence_quality.py`
和测试调用。
外部服务：无。MySQL：无直接读写。Redis：无。Hermes：无。DeepSeek：无。交易执行：无。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

STRATEGY_EVIDENCE_QUALITY_STEP = "26b_strategy_evidence_quality_gate"
STRATEGY_EVIDENCE_QUALITY_ERROR_CODE = "strategy_evidence_quality_failed"
STRATEGY_EVIDENCE_QUALITY_VERSION = "strategy_evidence_quality_gate_v1"
STRATEGY_EVIDENCE_QUALITY_TRIGGER_PIPELINE = "pipeline"

EXIT_SUCCESS = 0
EXIT_FAILED = 1
EXIT_PARAMETER_OR_DATABASE_ERROR = 2

NON_TRADING_STATEMENT = (
    "本检查只用于策略证据质量观测，不是交易建议；不自动交易，不读取账户，不生成订单。"
)


class StrategyEvidenceQualityStatus(str, Enum):
    """Stable 26B check statuses.

    `PASSED` allows the pipeline to continue. `WARNING` is non-blocking and is
    used for an explicit config skip. `FAILED` blocks the pipeline before 18.
    """

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class StrategyEvidenceQualitySeverity(str, Enum):
    """Severity values stored with a 26B quality result."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class NormalOperatingStrategyDefinition:
    """Config-derived definition of a strategy that must appear in evidence."""

    strategy_name: str
    strategy_role: str
    provides: tuple[str, ...]
    maturity_stage: str
    participation_mode: str
    decision_weight: str
    can_veto: bool


@dataclass(frozen=True)
class StrategyEvidenceQualityGateRequest:
    """Input for one 26B gate run inside the 25 pipeline.

    Parameters identify the current pipeline, stage-16/17 SSR, stage-23F/24
    SEA, market scope, Kline slot, and trace id.
    Failure scenarios are returned as `StrategyEvidenceQualityGateResult`.
    External effects are controlled by service settings: the service may write
    only `strategy_evidence_quality_check_result` and may send a fixed Hermes
    system alert when a blocking failure is found.
    """

    pipeline_run_id: str
    strategy_signal_run_id: str
    strategy_evidence_aggregation_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None
    trigger_source: str = STRATEGY_EVIDENCE_QUALITY_TRIGGER_PIPELINE
    created_by: str = "strategy_pipeline"
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class StrategyEvidenceQualityCheckIssue:
    """One failed or warning quality check item.

    `strategy_name`, `strategy_role`, and `field_name` are optional because
    some checks are chain-level, such as SSR/SEA mismatch or missing required
    role coverage.
    """

    error_code: str
    reason: str
    strategy_name: str | None = None
    strategy_role: str | None = None
    field_name: str | None = None
    severity: str = StrategyEvidenceQualitySeverity.CRITICAL.value

    def to_dict(self) -> dict[str, Any]:
        """Return a compact JSON-safe representation for audit details."""

        return {
            "error_code": self.error_code,
            "reason": self.reason,
            "strategy_name": self.strategy_name,
            "strategy_role": self.strategy_role,
            "field_name": self.field_name,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class StrategyEvidenceQualityPersistencePayload:
    """Repository payload for `strategy_evidence_quality_check_result`."""

    quality_check_id: str
    pipeline_run_id: str | None
    strategy_signal_run_id: str
    evidence_aggregation_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None
    status: str
    severity: str
    should_block_pipeline: bool
    error_code: str | None
    error_message: str | None
    failed_checks: tuple[Mapping[str, Any], ...]
    warning_checks: tuple[Mapping[str, Any], ...]
    strategy_quality: Mapping[str, Any]
    role_quality: Mapping[str, Any]
    config_snapshot: Mapping[str, Any]
    alert_required: bool
    alert_status: str
    alert_message_id: int | None
    not_trading_advice: bool
    trigger_source: str
    trace_id: str


@dataclass(frozen=True)
class StrategyEvidenceQualityGateResult:
    """Result returned by the 26B gate service to the 25 pipeline."""

    status: StrategyEvidenceQualityStatus
    quality_check_id: str
    pipeline_run_id: str | None
    strategy_signal_run_id: str
    strategy_evidence_aggregation_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None
    should_block_pipeline: bool
    severity: StrategyEvidenceQualitySeverity
    error_code: str | None = None
    error_message: str | None = None
    failed_checks: tuple[StrategyEvidenceQualityCheckIssue, ...] = ()
    warning_checks: tuple[StrategyEvidenceQualityCheckIssue, ...] = ()
    failed_strategies: tuple[str, ...] = ()
    failed_roles: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    alert_required: bool = False
    alert_status: str = "not_required"
    alert_message_id: int | None = None
    alert_error_message: str | None = None
    database_written: bool = False
    database_action: str | None = None
    not_trading_advice: bool = True
    trace_id: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        """Return a Chinese summary fit for pipeline_event_log.error_message."""

        if self.error_message:
            return self.error_message
        if self.should_block_pipeline:
            return "策略证据质量重大异常，已阻断 18 材料包。"
        if self.status == StrategyEvidenceQualityStatus.WARNING:
            return "策略证据质量闸门按配置跳过，pipeline 继续运行。"
        return "策略证据质量闸门通过。"


@dataclass(frozen=True)
class StrategyEvidenceQualityQueryRequest:
    """Read-only CLI query request for existing 26B result rows."""

    evidence_aggregation_id: str | None = None
    symbol: str = "BTCUSDT"
    base_interval: str = "4h"
    higher_interval: str = "1d"
    limit: int = 20


@dataclass(frozen=True)
class StrategyEvidenceQualityRowSummary:
    """Small read-only projection for CLI output."""

    quality_check_id: str
    pipeline_run_id: str | None
    strategy_signal_run_id: str
    evidence_aggregation_id: str
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None
    status: str
    severity: str
    should_block_pipeline: bool
    error_code: str | None
    error_message: str | None
    alert_status: str | None
    alert_message_id: int | None
    trace_id: str


@dataclass(frozen=True)
class StrategyEvidenceQualityQueryReport:
    """Read-only report returned to the 26B auxiliary CLI."""

    request: StrategyEvidenceQualityQueryRequest
    rows: tuple[StrategyEvidenceQualityRowSummary, ...]
    exit_code: int


def build_quality_check_id(
    *,
    pipeline_run_id: str | None,
    evidence_aggregation_id: str,
    trace_id: str,
) -> str:
    """Build a bounded business id for one 26B quality check.

    Pipeline-triggered 26B checks are audited per `pipeline_run_id`, because a
    later pipeline may legitimately reuse the same SEA. CLI/non-pipeline
    callers fall back to the older SEA + trace shape when no pipeline id exists.
    """

    pipeline_text = str(pipeline_run_id or "").strip()
    if pipeline_text:
        pipeline_part = "".join(ch if ch.isalnum() else "-" for ch in pipeline_text)[:140]
        return f"EQC-{pipeline_part}"

    evidence_part = "".join(ch if ch.isalnum() else "-" for ch in evidence_aggregation_id.strip())[:96]
    trace_part = "".join(ch for ch in trace_id if ch.isalnum())[:16] or uuid4().hex[:16]
    return f"EQC-{evidence_part}-{trace_part}"


def status_value(value: Any) -> str:
    """Return enum/string status values safely."""

    if hasattr(value, "value"):
        return str(value.value)
    return str(value or "")


def result_to_pipeline_details(result: StrategyEvidenceQualityGateResult) -> dict[str, Any]:
    """Return compact details for `pipeline_event_log.details_json`.

    The details intentionally store identifiers, status, failed strategy/role/
    field summaries, alert status and trace id. They do not store full strategy
    payloads, Kline windows, model prompts, model responses or account data.
    """

    return {
        "quality_check_id": result.quality_check_id,
        "status": result.status.value,
        "should_block_pipeline": result.should_block_pipeline,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "failed_strategies": list(result.failed_strategies),
        "failed_roles": list(result.failed_roles),
        "missing_fields": list(result.missing_fields),
        "alert_required": result.alert_required,
        "alert_status": result.alert_status,
        "alert_message_id": result.alert_message_id,
        "alert_error_message": result.alert_error_message,
        "database_written": result.database_written,
        "database_action": result.database_action,
        "not_trading_advice": result.not_trading_advice,
        "trace_id": result.trace_id,
        "details": dict(result.details),
    }


def format_strategy_evidence_quality_report_lines(report: StrategyEvidenceQualityQueryReport) -> list[str]:
    """Format the read-only 26B CLI report in Chinese."""

    lines = [
        "26B 策略证据质量闸门只读观测",
        f"evidence_aggregation_id={report.request.evidence_aggregation_id or ''}",
        f"symbol={report.request.symbol}",
        f"base_interval={report.request.base_interval}",
        f"higher_interval={report.request.higher_interval}",
        f"limit={report.request.limit}",
        f"result_count={len(report.rows)}",
    ]
    if not report.rows:
        lines.append("未查询到 26B 质量检查结果；本 CLI 不会自动运行闸门、不写库、不发送 Hermes。")
    for row in report.rows:
        lines.extend(
            [
                "",
                f"quality_check_id={row.quality_check_id}",
                f"pipeline_run_id={row.pipeline_run_id or ''}",
                f"strategy_signal_run_id={row.strategy_signal_run_id}",
                f"strategy_evidence_aggregation_id={row.evidence_aggregation_id}",
                f"symbol={row.symbol}",
                f"base_interval={row.base_interval}",
                f"higher_interval={row.higher_interval}",
                f"kline_slot_utc={row.kline_slot_utc.isoformat() if row.kline_slot_utc else ''}",
                f"status={row.status}",
                f"severity={row.severity}",
                f"should_block_pipeline={str(row.should_block_pipeline).lower()}",
                f"error_code={row.error_code or ''}",
                f"error_message={row.error_message or ''}",
                f"alert_status={row.alert_status or ''}",
                f"alert_message_id={row.alert_message_id or ''}",
                f"trace_id={row.trace_id}",
            ]
        )
    lines.append(NON_TRADING_STATEMENT)
    return lines


__all__ = [
    "EXIT_FAILED",
    "EXIT_PARAMETER_OR_DATABASE_ERROR",
    "EXIT_SUCCESS",
    "NON_TRADING_STATEMENT",
    "STRATEGY_EVIDENCE_QUALITY_ERROR_CODE",
    "STRATEGY_EVIDENCE_QUALITY_STEP",
    "STRATEGY_EVIDENCE_QUALITY_TRIGGER_PIPELINE",
    "STRATEGY_EVIDENCE_QUALITY_VERSION",
    "NormalOperatingStrategyDefinition",
    "StrategyEvidenceQualityCheckIssue",
    "StrategyEvidenceQualityGateRequest",
    "StrategyEvidenceQualityGateResult",
    "StrategyEvidenceQualityPersistencePayload",
    "StrategyEvidenceQualityQueryReport",
    "StrategyEvidenceQualityQueryRequest",
    "StrategyEvidenceQualityRowSummary",
    "StrategyEvidenceQualitySeverity",
    "StrategyEvidenceQualityStatus",
    "build_quality_check_id",
    "format_strategy_evidence_quality_report_lines",
    "result_to_pipeline_details",
    "status_value",
]
