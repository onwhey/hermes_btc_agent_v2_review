"""MySQL 健康检查模块。

本文件属于 `app/storage/mysql` 基础设施层，负责在被显式调用时执行 MySQL
连接检查和 `SELECT 1` 级别检查。
本文件不负责创建业务表，不写业务数据，不执行 Alembic upgrade，不读写 Redis，
不发送 Hermes，不请求 Binance，不调用 DeepSeek，不实现任何交易执行能力。
主要被 `scripts/check_infra.py` 和测试调用。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from urllib.parse import quote_plus

from app.core.config import AppSettings, get_settings
from app.core.exceptions import DatabaseError
from app.core.logger import get_logger, redact_sensitive_text

from .database import create_mysql_engine, render_redacted_mysql_connection_info


@dataclass(frozen=True)
class MySqlHealthCheckResult:
    """MySQL 健康检查结果。

    参数：`ok` 表示是否通过；`message` 是脱敏后的可读摘要。
    返回值：不可变结果对象。
    失败场景：无预期失败场景；底层异常由 `check_mysql_health()` 捕获并脱敏。
    外部服务：本类不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本类不负责业务表、告警记录、迁移执行或自动交易。
    """

    ok: bool
    message: str


def _load_sqlalchemy_text() -> object:
    try:
        sqlalchemy = importlib.import_module("sqlalchemy")
    except ImportError as exc:
        raise DatabaseError("SQLAlchemy 依赖未安装，无法执行 MySQL 健康检查") from exc
    return sqlalchemy.text


def check_mysql_health(settings: AppSettings | None = None) -> MySqlHealthCheckResult:
    """执行 MySQL 基础健康检查。

    参数：`settings` 是统一配置对象，未传入时读取缓存配置。
    返回值：`MySqlHealthCheckResult`，失败时 `ok=False` 且包含脱敏原因。
    失败场景：配置不完整、依赖缺失、连接失败或 `SELECT 1` 失败。
    外部服务：被调用时会尝试连接 MySQL 并执行 `SELECT 1`。
    数据影响：只读连接检查，不创建表，不写业务数据，不读写 Redis，不发送 Hermes。
    本函数不负责重试、业务报警、migration、Repository 逻辑或自动交易。
    """

    active_settings = settings or get_settings()
    logger = get_logger("mysql.health")
    target = render_redacted_mysql_connection_info(active_settings)
    logger.info("mysql health check started for %s", target)

    engine = None
    try:
        engine = create_mysql_engine(active_settings)
        sql_text = _load_sqlalchemy_text()
        with engine.connect() as connection:
            connection.execute(sql_text("SELECT 1"))
        logger.info("mysql health check passed for %s", target)
        return MySqlHealthCheckResult(ok=True, message="MySQL health check passed")
    except Exception as exc:  # noqa: BLE001 - health check 需要汇总明确失败原因。
        message = redact_sensitive_text(
            str(exc),
            (
                active_settings.mysql_password,
                quote_plus(active_settings.mysql_password),
                target,
            ),
        )
        logger.error("mysql health check failed: %s", message)
        return MySqlHealthCheckResult(ok=False, message=message)
    finally:
        if engine is not None and hasattr(engine, "dispose"):
            engine.dispose()
