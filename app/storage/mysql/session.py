"""MySQL session 生命周期管理模块。

本文件属于 `app/storage/mysql` 基础设施层，负责创建 sessionmaker、
提供 session 获取和上下文生命周期管理。
本文件不负责定义业务表，不写业务数据，不执行 migration，不读写 Redis，
不发送 Hermes，不请求 Binance，不调用 DeepSeek，不实现任何交易执行能力。
主要被后续 repository、service 和测试调用。
"""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.config import AppSettings
from app.core.exceptions import DatabaseError

from .database import get_engine

_SESSION_FACTORY: Any | None = None


def _load_sessionmaker() -> Any:
    try:
        sqlalchemy_orm = importlib.import_module("sqlalchemy.orm")
    except ImportError as exc:
        raise DatabaseError("SQLAlchemy 依赖未安装，无法创建 MySQL session factory") from exc
    return sqlalchemy_orm.sessionmaker


def create_session_factory(*, engine: Any | None = None, settings: AppSettings | None = None) -> Any:
    """创建 SQLAlchemy session factory。

    参数：`engine` 是可选 SQLAlchemy engine；`settings` 用于在未传入 engine 时创建 engine。
    返回值：`sessionmaker` 对象。
    失败场景：依赖缺失或 engine 创建失败时抛出 `DatabaseError`。
    外部服务：创建 session factory 不主动连接 MySQL。
    数据影响：不创建表，不写业务数据，不读写 Redis，不发送 Hermes。
    本函数不负责提交事务、业务 Repository 逻辑、migration 执行或自动交易。
    """

    active_engine = engine or get_engine(settings=settings)
    sessionmaker = _load_sessionmaker()
    return sessionmaker(bind=active_engine, autoflush=False, autocommit=False, future=True)


def get_session_factory(
    *,
    settings: AppSettings | None = None,
    reload: bool = False,
) -> Any:
    """获取缓存的 SQLAlchemy session factory。

    参数：`settings` 可显式指定配置；`reload` 为 True 时重建缓存 factory。
    返回值：`sessionmaker` 对象。
    失败场景：同 `create_session_factory()`。
    外部服务：创建 factory 不主动连接 MySQL。
    数据影响：不创建表，不写业务数据，不读写 Redis，不发送 Hermes。
    本函数不负责事务提交策略、业务数据访问或自动交易。
    """

    global _SESSION_FACTORY
    if _SESSION_FACTORY is None or reload or settings is not None:
        _SESSION_FACTORY = create_session_factory(settings=settings)
    return _SESSION_FACTORY


def get_db_session(*, settings: AppSettings | None = None) -> Any:
    """创建一个新的数据库 session。

    参数：`settings` 可显式指定配置。
    返回值：SQLAlchemy Session 实例。
    失败场景：session factory 创建失败时抛出 `DatabaseError`；真正连接通常发生在首次 SQL。
    外部服务：创建 session 对象通常不主动连接 MySQL。
    数据影响：不创建表，不写业务数据，不读写 Redis，不发送 Hermes。
    本函数不负责自动提交、业务查询、Repository 逻辑或自动交易。
    """

    session_factory = get_session_factory(settings=settings)
    return session_factory()


@contextmanager
def session_scope(
    *,
    settings: AppSettings | None = None,
    commit_on_success: bool = False,
) -> Iterator[Any]:
    """提供带回滚和关闭保障的 session 上下文。

    参数：`settings` 可显式指定配置；`commit_on_success` 控制正常退出时是否提交。
    返回值：yield 一个 SQLAlchemy Session 实例。
    失败场景：上下文内异常会触发 rollback，并继续向上抛出原异常。
    外部服务：只有调用方在上下文内执行 SQL 时才会访问 MySQL。
    数据影响：本函数不主动写业务数据，不读写 Redis，不发送 Hermes。
    本函数默认把提交权交给调用方，不负责 Repository 业务逻辑、migration 或自动交易。
    """

    db_session = get_db_session(settings=settings)
    try:
        yield db_session
        if commit_on_success:
            db_session.commit()
    except Exception:
        db_session.rollback()
        raise
    finally:
        db_session.close()

