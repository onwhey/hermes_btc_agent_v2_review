"""MySQL engine 管理模块。

本文件属于 `app/storage/mysql` 基础设施层，负责根据统一配置创建 SQLAlchemy engine。
本文件不负责创建业务表，不执行 Alembic upgrade，不写入业务数据，不读写 Redis，
不发送 Hermes，不请求 Binance，不调用 DeepSeek，不实现任何交易执行能力。
主要被 `app/storage/mysql/session.py`、`app/storage/mysql/health.py` 和后续 repository 调用。
"""

from __future__ import annotations

import importlib
from typing import Any
from urllib.parse import quote_plus, urlencode

from app.core.config import AppSettings, get_settings
from app.core.constants import APP_ENV_TEST
from app.core.exceptions import DatabaseError
from app.core.logger import get_logger, redact_sensitive_text

MYSQL_DRIVER = "mysql+pymysql"
LOCAL_MYSQL_HOSTS = {"127.0.0.1", "localhost", "::1"}

_ENGINE: Any | None = None


def _load_sqlalchemy_create_engine() -> Any:
    try:
        sqlalchemy = importlib.import_module("sqlalchemy")
    except ImportError as exc:
        raise DatabaseError("SQLAlchemy 依赖未安装，无法创建 MySQL engine") from exc
    return sqlalchemy.create_engine


def _validate_mysql_settings_for_connection(settings: AppSettings) -> None:
    missing_fields: list[str] = []
    if not settings.mysql_host:
        missing_fields.append("MYSQL_HOST")
    if not settings.mysql_database:
        missing_fields.append("MYSQL_DATABASE")
    if not settings.mysql_user:
        missing_fields.append("MYSQL_USER")
    if missing_fields:
        joined = ", ".join(missing_fields)
        raise DatabaseError(f"MySQL 配置不完整：缺少 {joined}")

    if settings.app_env == APP_ENV_TEST and settings.mysql_host not in LOCAL_MYSQL_HOSTS:
        raise DatabaseError("APP_ENV=test 时只允许显式检查本机 MySQL")


def build_mysql_connection_url(settings: AppSettings | None = None) -> str:
    """构建内部使用的 MySQL SQLAlchemy URL。

    参数：`settings` 是统一配置对象，未传入时读取缓存配置。
    返回值：包含密码的内部连接 URL；调用方不得写入日志或文档。
    失败场景：必要 MySQL 配置缺失或测试环境目标不是本机时抛出 `DatabaseError`。
    外部服务：不访问外部服务。
    数据影响：不连接 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责健康检查、创建业务表、执行 migration 或自动交易。
    """

    active_settings = settings or get_settings()
    _validate_mysql_settings_for_connection(active_settings)

    username = quote_plus(active_settings.mysql_user)
    password = quote_plus(active_settings.mysql_password)
    credentials = username
    if password:
        credentials = f"{credentials}:{password}"

    query = urlencode({"charset": active_settings.mysql_charset})
    database = quote_plus(active_settings.mysql_database)
    return (
        f"{MYSQL_DRIVER}://{credentials}@"
        f"{active_settings.mysql_host}:{active_settings.mysql_port}/{database}?{query}"
    )


def render_redacted_mysql_connection_info(settings: AppSettings | None = None) -> str:
    """渲染可展示的 MySQL 连接摘要。

    参数：`settings` 是统一配置对象，未传入时读取缓存配置。
    返回值：不包含密码的连接摘要。
    失败场景：无预期失败场景；配置读取失败时由 `get_settings()` 抛出异常。
    外部服务：不访问外部服务。
    数据影响：不连接 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责生成真实连接 URL 或自动交易。
    """

    active_settings = settings or get_settings()
    username = quote_plus(active_settings.mysql_user or "<empty>")
    database = quote_plus(active_settings.mysql_database or "<empty>")
    charset = quote_plus(active_settings.mysql_charset)
    return (
        f"{MYSQL_DRIVER}://{username}:***REDACTED***@"
        f"{active_settings.mysql_host or '<empty>'}:{active_settings.mysql_port}/"
        f"{database}?charset={charset}"
    )


def create_mysql_engine(settings: AppSettings | None = None) -> Any:
    """创建 SQLAlchemy engine。

    参数：`settings` 是统一配置对象，未传入时读取缓存配置。
    返回值：SQLAlchemy engine 对象。
    失败场景：配置不完整、依赖缺失或 engine 构造失败时抛出 `DatabaseError`。
    外部服务：创建 engine 本身不主动建立 MySQL 网络连接。
    数据影响：不创建表，不写业务数据，不读写 Redis，不发送 Hermes。
    本函数不负责 session 生命周期、健康检查 SQL、migration 执行或自动交易。
    """

    active_settings = settings or get_settings()
    url = build_mysql_connection_url(active_settings)
    create_engine = _load_sqlalchemy_create_engine()
    try:
        engine = create_engine(
            url,
            pool_size=active_settings.mysql_pool_size,
            max_overflow=active_settings.mysql_max_overflow,
            pool_recycle=active_settings.mysql_pool_recycle,
            pool_pre_ping=active_settings.mysql_pool_pre_ping,
            future=True,
        )
    except Exception as exc:  # noqa: BLE001 - 需要包装并脱敏底层驱动错误。
        message = redact_sensitive_text(
            str(exc),
            (active_settings.mysql_password, url),
        )
        raise DatabaseError(f"MySQL engine 创建失败：{message}") from exc

    logger = get_logger("mysql.database")
    logger.info(
        "mysql engine created for %s",
        render_redacted_mysql_connection_info(active_settings),
    )
    return engine


def get_engine(*, settings: AppSettings | None = None, reload: bool = False) -> Any:
    """获取缓存的 SQLAlchemy engine。

    参数：`settings` 可显式指定配置；`reload` 为 True 时重建缓存 engine。
    返回值：SQLAlchemy engine 对象。
    失败场景：同 `create_mysql_engine()`。
    外部服务：engine 创建不主动连接 MySQL。
    数据影响：不创建表，不写业务数据，不读写 Redis，不发送 Hermes。
    本函数不负责提交事务、执行健康检查 SQL 或自动交易。
    """

    global _ENGINE
    if _ENGINE is None or reload or settings is not None:
        if reload and _ENGINE is not None and hasattr(_ENGINE, "dispose"):
            _ENGINE.dispose()
        _ENGINE = create_mysql_engine(settings)
    return _ENGINE


def dispose_engine() -> None:
    """释放缓存 engine。

    参数：无。
    返回值：无。
    失败场景：底层 engine dispose 失败时由驱动抛出异常。
    外部服务：可能关闭已有连接池连接，不主动创建新连接。
    数据影响：不写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责业务事务回滚、迁移执行或自动交易。
    """

    global _ENGINE
    if _ENGINE is not None and hasattr(_ENGINE, "dispose"):
        _ENGINE.dispose()
    _ENGINE = None


get_mysql_engine = get_engine
dispose_mysql_engine = dispose_engine

