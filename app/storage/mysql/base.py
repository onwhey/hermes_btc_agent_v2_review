"""MySQL SQLAlchemy declarative base.

本文件属于 `app/storage/mysql` 基础设施层，负责提供 ORM metadata 入口。
本文件不负责创建业务表，不执行迁移，不连接 MySQL，不读写 Redis，
不发送 Hermes，不调用 DeepSeek，不实现 K 线采集或任何交易执行能力。
主要被后续 ORM model、Alembic env 和测试导入。
"""

from __future__ import annotations

try:
    from sqlalchemy.orm import DeclarativeBase
except ImportError:  # pragma: no cover - 真实依赖由 pyproject 管理，本分支测试不强制安装。
    DeclarativeBase = None  # type: ignore[assignment]


if DeclarativeBase is None:

    class Base:  # type: ignore[no-redef]
        """SQLAlchemy 未安装时的导入占位。

        参数：无。
        返回值：仅提供 `metadata = None`，保证默认单元测试不会连接外部服务。
        失败场景：真正创建 ORM model 或 Alembic 迁移前必须安装 SQLAlchemy。
        外部服务：不访问任何外部服务。
        数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
        本类不负责业务表定义、迁移执行或自动交易。
        """

        metadata = None

else:

    class Base(DeclarativeBase):  # type: ignore[no-redef]
        """项目 SQLAlchemy ORM 基类。

        参数：无。
        返回值：供后续 ORM model 继承的 declarative base。
        失败场景：导入 SQLAlchemy 失败时由上方占位避免默认测试失败。
        外部服务：类定义不访问外部服务。
        数据影响：不连接 MySQL，不读写 Redis，不发送 Hermes。
        本类不负责定义业务表、执行 migration、写业务数据或自动交易。
        """

