"""运行状态观测类型定义。

本文件属于 `app/monitoring` 模块，负责定义运行状态等级、报告结构和
MySQL 只读查询协议。
本文件不访问 systemd、MySQL、Redis、Hermes、Binance，不调用 DeepSeek，
不写正式 K线表，不涉及任何交易执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol


class RuntimeStatusLevel(str, Enum):
    """运行状态等级。"""

    NORMAL = "normal"
    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


LEVEL_LABELS = {
    RuntimeStatusLevel.NORMAL: "正常",
    RuntimeStatusLevel.NOTICE: "注意",
    RuntimeStatusLevel.WARNING: "警告",
    RuntimeStatusLevel.ERROR: "错误",
    RuntimeStatusLevel.CRITICAL: "严重",
}
LEVEL_RANK = {
    RuntimeStatusLevel.NORMAL: 0,
    RuntimeStatusLevel.NOTICE: 1,
    RuntimeStatusLevel.WARNING: 2,
    RuntimeStatusLevel.ERROR: 3,
    RuntimeStatusLevel.CRITICAL: 4,
}


class RuntimeMySqlReader(Protocol):
    """运行状态所需 MySQL 只读查询协议。"""

    def check_connection(self) -> None:
        ...

    def get_latest_kline(self, *, symbol: str, interval_value: str) -> Any | None:
        ...

    def list_recent_klines(self, *, symbol: str, interval_value: str, limit: int) -> list[Any]:
        ...

    def get_latest_collector_event(self, *, symbol: str, interval_value: str, since_utc: datetime) -> Any | None:
        ...

    def get_latest_daily_quality_check(self, *, symbol: str, interval_value: str, since_utc: datetime) -> Any | None:
        ...

    def list_recent_alert_messages(self, *, since_utc: datetime, limit: int) -> list[Any]:
        ...


@dataclass(frozen=True)
class RuntimeIssue:
    """一条运行状态问题摘要。"""

    level: RuntimeStatusLevel
    source: str
    message: str


@dataclass(frozen=True)
class ServiceRuntimeStatus:
    """一个 systemd 服务状态。"""

    service_name: str
    display_name: str
    raw_status: str
    status_label: str
    level: RuntimeStatusLevel


@dataclass(frozen=True)
class RedisRuntimeStatus:
    """Redis 只读检查摘要。"""

    connection_ok: bool
    level: RuntimeStatusLevel
    bitcoin_price_exists: bool = False
    bitcoin_price_ttl: int | None = None
    scheduler_running_count: int = 0
    scheduler_completed_count: int = 0
    scheduler_status_count: int = 0
    scheduler_job_legacy_count: int = 0
    error_message: str | None = None
    issues: list[RuntimeIssue] = field(default_factory=list)


@dataclass(frozen=True)
class MySqlRuntimeStatus:
    """MySQL 与 K线状态摘要。"""

    connection_ok: bool
    level: RuntimeStatusLevel
    latest_kline_open_time_utc: datetime | None = None
    recent_kline_count: int | None = None
    latest_collector_status: str | None = None
    latest_daily_quality_status: str | None = None
    error_message: str | None = None
    issues: list[RuntimeIssue] = field(default_factory=list)


@dataclass(frozen=True)
class AlertRuntimeStatus:
    """最近 Hermes 报警提交状态摘要。"""

    connection_ok: bool
    level: RuntimeStatusLevel
    latest_status: str | None = None
    latest_gateway_status: str | None = None
    latest_final_delivery_status: str | None = None
    latest_trace_id: str | None = None
    failed_count: int = 0
    legacy_status_count: int = 0
    error_message: str | None = None
    issues: list[RuntimeIssue] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeStatusReport:
    """一次运行状态检查结果。"""

    overall_level: RuntimeStatusLevel
    trace_id: str
    checked_at_utc: datetime
    lookback_hours: int
    services: list[ServiceRuntimeStatus]
    redis: RedisRuntimeStatus
    mysql: MySqlRuntimeStatus
    alert: AlertRuntimeStatus
    issues: list[RuntimeIssue] = field(default_factory=list)
