"""运行状态观测只读读取器。

本文件属于 `app/monitoring` 模块，负责 systemd 状态读取和 MySQL 只读查询。
本文件不写 MySQL，不写 Redis，不请求 Binance，不发送 Hermes，不调用
DeepSeek，不执行任何修复、回补、采集或交易动作。
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any, Callable

from app.core.config import AppSettings
from app.market_data.collector.types import COLLECTOR_EVENT_TYPE
from app.market_data.kline_quality.types import CHECK_TYPE_DAILY_KLINE_INTEGRITY
from app.storage.mysql.models.alert_message import AlertMessage
from app.storage.mysql.models.collector_event_log import CollectorEventLog
from app.storage.mysql.models.data_quality_check import DataQualityCheck
from app.storage.mysql.models.market_kline_4h import MarketKline4h
from app.storage.mysql.session import session_scope

try:
    from sqlalchemy import select, text
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = text = None  # type: ignore[assignment]


class SystemdStatusChecker:
    """只读查询 systemd 服务状态。"""

    def is_active(self, service_name: str) -> str:
        """执行 `systemctl is-active`，本方法不启动、停止或重启服务。"""

        try:
            completed = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return "unknown"
        return (completed.stdout or completed.stderr or "unknown").strip() or "unknown"


class DefaultRuntimeMySqlReader:
    """基于 SQLAlchemy ORM 的运行状态只读查询实现。"""

    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings

    def _query(self, fn: Callable[[Any], Any]) -> Any:
        if select is None or text is None:
            raise RuntimeError("SQLAlchemy is required for runtime status MySQL checks")
        with session_scope(settings=self.settings, commit_on_success=False) as db_session:
            return fn(db_session)

    def check_connection(self) -> None:
        self._query(lambda session: session.execute(text("SELECT 1")).scalar_one())

    def get_latest_kline(self, *, symbol: str, interval_value: str) -> Any | None:
        def _read(session: Any) -> Any | None:
            stmt = (
                select(MarketKline4h)
                .where(MarketKline4h.symbol == symbol)
                .where(MarketKline4h.interval_value == interval_value)
                .order_by(MarketKline4h.open_time_ms.desc())
                .limit(1)
            )
            return session.execute(stmt).scalar_one_or_none()

        return self._query(_read)

    def list_recent_klines(self, *, symbol: str, interval_value: str, limit: int) -> list[Any]:
        def _read(session: Any) -> list[Any]:
            stmt = (
                select(MarketKline4h)
                .where(MarketKline4h.symbol == symbol)
                .where(MarketKline4h.interval_value == interval_value)
                .order_by(MarketKline4h.open_time_ms.desc())
                .limit(limit)
            )
            return list(session.execute(stmt).scalars().all())

        return self._query(_read)

    def get_latest_collector_event(self, *, symbol: str, interval_value: str, since_utc: Any) -> Any | None:
        def _read(session: Any) -> Any | None:
            stmt = (
                select(CollectorEventLog)
                .where(CollectorEventLog.symbol == symbol)
                .where(CollectorEventLog.interval_value == interval_value)
                .where(CollectorEventLog.event_type == COLLECTOR_EVENT_TYPE)
                .where(CollectorEventLog.started_at_utc >= since_utc)
                .order_by(CollectorEventLog.started_at_utc.desc())
                .limit(1)
            )
            return session.execute(stmt).scalar_one_or_none()

        return self._query(_read)

    def get_latest_daily_quality_check(self, *, symbol: str, interval_value: str, since_utc: Any) -> Any | None:
        def _read(session: Any) -> Any | None:
            stmt = (
                select(DataQualityCheck)
                .where(DataQualityCheck.symbol == symbol)
                .where(DataQualityCheck.interval_value == interval_value)
                .where(DataQualityCheck.check_type == CHECK_TYPE_DAILY_KLINE_INTEGRITY)
                .where(DataQualityCheck.created_at_utc >= since_utc)
                .order_by(DataQualityCheck.created_at_utc.desc())
                .limit(1)
            )
            return session.execute(stmt).scalar_one_or_none()

        return self._query(_read)

    def list_recent_alert_messages(self, *, since_utc: Any, limit: int) -> list[Any]:
        def _read(session: Any) -> list[Any]:
            stmt = (
                select(
                    AlertMessage.id,
                    AlertMessage.alert_type,
                    AlertMessage.severity,
                    AlertMessage.status,
                    AlertMessage.sent_at_utc,
                    AlertMessage.created_at_utc,
                    AlertMessage.error_message,
                    AlertMessage.trace_id,
                )
                .where(AlertMessage.created_at_utc >= since_utc)
                .order_by(AlertMessage.created_at_utc.desc())
                .limit(limit)
            )
            return [SimpleNamespace(**dict(row._mapping)) for row in session.execute(stmt).all()]

        return self._query(_read)
