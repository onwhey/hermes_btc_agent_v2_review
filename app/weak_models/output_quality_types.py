"""DTOs and formatting helpers for 27B weak model output quality checks.

本文件属于 `app/weak_models` 模块，负责定义 27B 弱模型输出质量审查的
请求、问题、结果、持久化 payload 和 CLI 展示结构。
本文件不读取数据库，不写数据库，不请求 Binance，不读写 Redis，不发送 Hermes，
不调用 DeepSeek/GPT/Claude，不读取账户或仓位，不生成订单，不自动交易。
主要被 `output_quality_service.py`、`output_quality_repository.py` 和
`scripts/check_weak_model_output_quality.py` 调用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.market_data.kline_constants import DEFAULT_KLINE_SYMBOL, KLINE_1D_INTERVAL_VALUE, KLINE_4H_INTERVAL_VALUE

EXIT_SUCCESS = 0
EXIT_PARAMETER_OR_DATABASE_ERROR = 2

WEAK_MODEL_OUTPUT_QUALITY_VERSION = "weak_model_output_quality_v1"
NON_TRADING_STATEMENT = (
    "本检查只用于弱模型输出质量观测，不是交易建议；不自动交易，不读取账户，不生成订单。"
)


class WeakModelQualityStatus(str, Enum):
    """Stable 27B quality check statuses."""

    PASSED = "passed"
    WARNING = "warning"
    CRITICAL = "critical"


class WeakModelQualitySeverity(str, Enum):
    """Severity values used by 27B quality issues and summary rows."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class WeakModelQualityIssue:
    """One bounded quality issue detected from persisted 27A output.

    The issue stores identifiers, observed value and conservative calibration
    suggestion only. It never stores raw Kline windows, prompts, model responses
    or private trading state.
    """

    error_code: str
    reason: str
    severity: str = WeakModelQualitySeverity.WARNING.value
    model_key: str | None = None
    field_name: str | None = None
    observed_value: Any | None = None
    expected: str | None = None
    calibration_suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return compact JSON-safe issue data for `issues_json`."""

        return {
            "error_code": self.error_code,
            "reason": self.reason,
            "severity": self.severity,
            "model_key": self.model_key,
            "field_name": self.field_name,
            "observed_value": self.observed_value,
            "expected": self.expected,
            "calibration_suggestion": self.calibration_suggestion,
        }


@dataclass(frozen=True)
class WeakModelQualityTarget:
    """Read-only package of one persisted 27A run and its outputs."""

    run: Any
    aggregation: Any | None
    results: tuple[Any, ...]


@dataclass(frozen=True)
class WeakModelQualityCheckRequest:
    """Input request for 27B quality checking.

    `weak_model_run_id` performs an exact read-only check. When it is not set,
    the service reads recent persisted 27A runs by symbol/base/higher/limit.
    `confirm_write=True` allows writing only `weak_model_quality_check`.
    """

    weak_model_run_id: str | None = None
    symbol: str = DEFAULT_KLINE_SYMBOL
    base_interval: str = KLINE_4H_INTERVAL_VALUE
    higher_interval: str = KLINE_1D_INTERVAL_VALUE
    limit: int = 10
    dry_run: bool = True
    confirm_write: bool = False
    trace_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class WeakModelQualityPersistencePayload:
    """Repository payload for one `weak_model_quality_check` row."""

    quality_check_id: str
    weak_model_run_id: str
    weak_model_aggregation_id: str | None
    strategy_signal_run_id: str
    snapshot_id: str | None
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None
    status: str
    severity: str
    issue_count: int
    warning_count: int
    critical_count: int
    should_block_pipeline: bool
    issues: tuple[Mapping[str, Any], ...]
    checked_models: tuple[Mapping[str, Any], ...]
    summary_text: str
    trace_id: str
    details: Mapping[str, Any]


@dataclass(frozen=True)
class WeakModelQualityCheckResult:
    """One 27B quality check result returned by the service."""

    status: WeakModelQualityStatus
    severity: WeakModelQualitySeverity
    quality_check_id: str
    weak_model_run_id: str
    weak_model_aggregation_id: str | None
    strategy_signal_run_id: str
    snapshot_id: str | None
    symbol: str
    base_interval: str
    higher_interval: str
    kline_slot_utc: datetime | None
    issue_count: int
    warning_count: int
    critical_count: int
    should_block_pipeline: bool
    issues: tuple[WeakModelQualityIssue, ...]
    checked_models: tuple[Mapping[str, Any], ...]
    summary_text: str
    database_written: bool = False
    database_action: str = "dry_run"
    trace_id: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WeakModelQualityCheckReport:
    """Report returned to the 27B CLI."""

    request: WeakModelQualityCheckRequest
    results: tuple[WeakModelQualityCheckResult, ...]
    exit_code: int


def build_weak_model_quality_check_id(weak_model_run_id: str) -> str:
    """Build a stable idempotency id for one persisted 27A run."""

    safe_run_id = "".join(ch if ch.isalnum() else "-" for ch in weak_model_run_id.strip())[:160]
    return f"WMQC-{safe_run_id}"


def quality_status_from_counts(warning_count: int, critical_count: int) -> tuple[WeakModelQualityStatus, WeakModelQualitySeverity]:
    """Return status/severity from warning and critical counts."""

    if critical_count > 0:
        return WeakModelQualityStatus.CRITICAL, WeakModelQualitySeverity.CRITICAL
    if warning_count > 0:
        return WeakModelQualityStatus.WARNING, WeakModelQualitySeverity.WARNING
    return WeakModelQualityStatus.PASSED, WeakModelQualitySeverity.INFO


def quality_persistence_payload_from_result(result: WeakModelQualityCheckResult) -> WeakModelQualityPersistencePayload:
    """Convert a result into a compact repository payload."""

    return WeakModelQualityPersistencePayload(
        quality_check_id=result.quality_check_id,
        weak_model_run_id=result.weak_model_run_id,
        weak_model_aggregation_id=result.weak_model_aggregation_id,
        strategy_signal_run_id=result.strategy_signal_run_id,
        snapshot_id=result.snapshot_id,
        symbol=result.symbol,
        base_interval=result.base_interval,
        higher_interval=result.higher_interval,
        kline_slot_utc=result.kline_slot_utc,
        status=result.status.value,
        severity=result.severity.value,
        issue_count=result.issue_count,
        warning_count=result.warning_count,
        critical_count=result.critical_count,
        should_block_pipeline=result.should_block_pipeline,
        issues=tuple(issue.to_dict() for issue in result.issues),
        checked_models=tuple(dict(item) for item in result.checked_models),
        summary_text=result.summary_text,
        trace_id=result.trace_id,
        details=dict(result.details),
    )


def json_dumps_compact(value: Any) -> str:
    """Serialize bounded 27B audit fields as compact JSON."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def format_weak_model_quality_report_lines(report: WeakModelQualityCheckReport) -> list[str]:
    """Render the 27B CLI report in Chinese."""

    lines = [
        "27B 弱模型输出质量检查",
        f"weak_model_run_id={report.request.weak_model_run_id or ''}",
        f"symbol={report.request.symbol}",
        f"base_interval={report.request.base_interval}",
        f"higher_interval={report.request.higher_interval}",
        f"limit={report.request.limit}",
        f"confirm_write={str(report.request.confirm_write).lower()}",
        f"result_count={len(report.results)}",
    ]
    if not report.results:
        lines.append("未查询到可检查的 27A weak_model_run；本 CLI 不会自动运行弱模型。")
    for result in report.results:
        lines.extend(
            [
                "",
                f"quality_check_id={result.quality_check_id}",
                f"weak_model_run_id={result.weak_model_run_id}",
                f"weak_model_aggregation_id={result.weak_model_aggregation_id or ''}",
                f"strategy_signal_run_id={result.strategy_signal_run_id}",
                f"snapshot_id={result.snapshot_id or ''}",
                f"symbol={result.symbol}",
                f"base_interval={result.base_interval}",
                f"higher_interval={result.higher_interval}",
                f"kline_slot_utc={result.kline_slot_utc.isoformat() if result.kline_slot_utc else ''}",
                f"status={result.status.value}",
                f"severity={result.severity.value}",
                f"issue_count={result.issue_count}",
                f"warning_count={result.warning_count}",
                f"critical_count={result.critical_count}",
                f"should_block_pipeline={str(result.should_block_pipeline).lower()}",
                f"database_written={str(result.database_written).lower()}",
                f"database_action={result.database_action}",
                f"summary_text={result.summary_text}",
                "issues_json=" + json_dumps_compact(tuple(issue.to_dict() for issue in result.issues)),
            ]
        )
    lines.append(NON_TRADING_STATEMENT)
    return lines


__all__ = [
    "EXIT_PARAMETER_OR_DATABASE_ERROR",
    "EXIT_SUCCESS",
    "NON_TRADING_STATEMENT",
    "WEAK_MODEL_OUTPUT_QUALITY_VERSION",
    "WeakModelQualityCheckReport",
    "WeakModelQualityCheckRequest",
    "WeakModelQualityCheckResult",
    "WeakModelQualityIssue",
    "WeakModelQualityPersistencePayload",
    "WeakModelQualitySeverity",
    "WeakModelQualityStatus",
    "WeakModelQualityTarget",
    "build_weak_model_quality_check_id",
    "format_weak_model_quality_report_lines",
    "json_dumps_compact",
    "quality_persistence_payload_from_result",
    "quality_status_from_counts",
]
