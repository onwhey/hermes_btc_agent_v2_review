"""alert_message 报警记录模型。

本文件属于 `app/storage/mysql/models` 存储层，负责定义 04 阶段允许的
`alert_message` ORM model。
本文件不负责发送 Hermes，不负责模板渲染，不读写 Redis，不请求 Binance，
不调用 DeepSeek，不涉及任何交易执行。
主要被 Alembic metadata、`alert_message_repository.py` 和测试导入。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.storage.mysql.base import Base

try:
    from sqlalchemy import JSON, DateTime, Integer, String, Text
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError:  # pragma: no cover - 默认单元测试不强制安装 SQLAlchemy。
    JSON = DateTime = Integer = String = Text = None  # type: ignore[assignment]
    Mapped = Any  # type: ignore[assignment]
    mapped_column = None  # type: ignore[assignment]


if mapped_column is not None:

    class AlertMessage(Base):
        """报警记录 ORM model。

        参数：字段由 repository 创建和更新。
        返回值：SQLAlchemy ORM 对象。
        失败场景：依赖缺失或 metadata 不可用时由导入阶段 fallback 避免默认测试失败。
        外部服务：类定义不访问外部服务。
        数据影响：本类只定义结构，不连接 MySQL，不发送 Hermes，不读写 Redis。
        本类不负责 K 线表、策略表、建议表或任何交易执行。
        """

        __tablename__ = "alert_message"

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        alert_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
        severity: Mapped[str] = mapped_column(String(16), nullable=False)
        title: Mapped[str] = mapped_column(String(255), nullable=False)
        message: Mapped[str] = mapped_column(Text, nullable=False)
        channel: Mapped[str] = mapped_column(String(32), nullable=False, default="hermes")
        status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
        source: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
        trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
        channel_response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
        error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
        retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
        http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
        occurred_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        sent_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
        created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

else:

    @dataclass
    class AlertMessage:  # type: ignore[no-redef]
        """SQLAlchemy 未安装时的导入占位。

        参数：字段与 ORM model 保持一致，便于默认单元测试 mock repository。
        返回值：普通 dataclass 对象。
        失败场景：真实数据库写入前必须安装 SQLAlchemy。
        外部服务：不访问外部服务。
        数据影响：不连接 MySQL，不读写 Redis，不发送 Hermes。
        本类不负责 migration 执行或自动交易。
        """

        id: int | None = None
        alert_type: str = ""
        severity: str = ""
        title: str = ""
        message: str = ""
        channel: str = "hermes"
        status: str = ""
        source: str = "unknown"
        trace_id: str = ""
        channel_response: dict[str, Any] | None = None
        error_message: str | None = None
        retry_count: int = 0
        http_status_code: int | None = None
        occurred_at_utc: datetime | None = None
        sent_at_utc: datetime | None = None
        created_at_utc: datetime | None = None
        updated_at_utc: datetime | None = None

