"""Alembic 环境配置入口。

本文件属于 `migrations` 数据库结构迁移层，负责让 Alembic 读取项目统一配置
和 SQLAlchemy metadata。
本文件不定义业务表，不创建 migration，不自动执行 upgrade，不写业务数据，
不读写 Redis，不发送 Hermes，不请求 Binance，不调用 DeepSeek，不实现交易执行。
主要由用户手动运行 Alembic 命令时调用。
"""

from __future__ import annotations

from logging.config import fileConfig
from typing import Any

try:
    from alembic import context
except ImportError:  # pragma: no cover - Alembic 由 pyproject 管理，本地测试不强制安装。
    context = None  # type: ignore[assignment]

from app.core.config import get_settings
from app.storage.mysql.base import Base
from app.storage.mysql.database import build_mysql_connection_url
from app.storage.mysql.models import alert_message as _alert_message_model  # noqa: F401
from app.storage.mysql.models import collector_event_log as _collector_event_log_model  # noqa: F401
from app.storage.mysql.models import data_quality_check as _data_quality_check_model  # noqa: F401
from app.storage.mysql.models import market_context_snapshot as _market_context_snapshot_model  # noqa: F401
from app.storage.mysql.models import market_kline_1d as _market_kline_1d_model  # noqa: F401
from app.storage.mysql.models import market_kline_4h as _market_kline_4h_model  # noqa: F401
from app.storage.mysql.models import model_analysis as _model_analysis_model  # noqa: F401
from app.storage.mysql.models import model_review_aggregation as _model_review_aggregation_model  # noqa: F401
from app.storage.mysql.models import model_review_chain as _model_review_chain_model  # noqa: F401
from app.storage.mysql.models import strategy_aggregation as _strategy_aggregation_model  # noqa: F401
from app.storage.mysql.models import strategy_signal as _strategy_signal_model  # noqa: F401
from app.storage.mysql.models import strategy_signal_scheduler_event as _strategy_signal_scheduler_event_model  # noqa: F401

target_metadata = Base.metadata


def _require_alembic_context() -> Any:
    if context is None:
        raise RuntimeError("Alembic 依赖未安装，无法运行迁移环境")
    return context


def _is_alembic_runtime() -> bool:
    if context is None:
        return False
    try:
        context.config
    except Exception:  # pragma: no cover - 仅用于避免普通 import 触发 Alembic 运行态。
        return False
    return True


def _configure_database_url() -> str:
    settings = get_settings()
    url = build_mysql_connection_url(settings)
    _require_alembic_context().config.set_main_option("sqlalchemy.url", url)
    return url


def run_migrations_offline() -> None:
    """运行 Alembic offline migration 配置。

    参数：无。
    返回值：无。
    失败场景：配置缺失、Alembic 不在运行态或 metadata 不可用时抛出异常。
    外部服务：offline 模式不连接 MySQL。
    数据影响：不写业务数据，不读写 Redis，不发送 Hermes。
    本函数不负责创建业务 migration、执行自动升级或自动交易。
    """

    active_context = _require_alembic_context()
    url = _configure_database_url()
    active_context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with active_context.begin_transaction():
        active_context.run_migrations()


def run_migrations_online() -> None:
    """运行 Alembic online migration 配置。

    参数：无。
    返回值：无。
    失败场景：配置缺失、连接失败或迁移执行失败时由 Alembic / SQLAlchemy 抛出异常。
    外部服务：仅用户手动运行 Alembic 命令时才可能连接 MySQL。
    数据影响：由用户明确执行的 Alembic 命令决定；本阶段没有业务 migration。
    本函数不负责创建业务表、自动执行 upgrade、写 Redis、发送 Hermes 或自动交易。
    """

    active_context = _require_alembic_context()
    config = active_context.config
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)

    _configure_database_url()
    connectable = active_context.config.attributes.get("connection")
    if connectable is None:
        from sqlalchemy import engine_from_config, pool

        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

    with connectable.connect() as connection:
        active_context.configure(connection=connection, target_metadata=target_metadata)
        with active_context.begin_transaction():
            active_context.run_migrations()


if _is_alembic_runtime():
    if _require_alembic_context().is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
