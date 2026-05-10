"""Redis client 管理模块。

本文件属于 `app/storage/redis` 基础设施层，负责根据统一配置创建 Redis client。
本文件不负责写入业务 key，不缓存价格，不实现提醒冷却，不连接 MySQL，
不发送 Hermes，不请求 Binance，不调用 DeepSeek，不实现任何交易执行能力。
主要被 `app/storage/redis/health.py`、后续短期状态模块和测试调用。
"""

from __future__ import annotations

import importlib
from typing import Any

from app.core.config import AppSettings, get_settings
from app.core.constants import APP_ENV_TEST
from app.core.exceptions import RedisError
from app.core.logger import get_logger, redact_sensitive_text

LOCAL_REDIS_HOSTS = {"127.0.0.1", "localhost", "::1"}

_CLIENT: Any | None = None


def _load_redis_client_class() -> Any:
    try:
        redis_module = importlib.import_module("redis")
    except ImportError as exc:
        raise RedisError("redis 依赖未安装，无法创建 Redis client") from exc
    return redis_module.Redis


def _validate_redis_settings_for_connection(settings: AppSettings) -> None:
    if not settings.redis_host:
        raise RedisError("Redis 配置不完整：缺少 REDIS_HOST")
    if settings.redis_db < 0:
        raise RedisError("REDIS_DB 必须大于或等于 0")
    if settings.app_env == APP_ENV_TEST and settings.redis_host not in LOCAL_REDIS_HOSTS:
        raise RedisError("APP_ENV=test 时只允许显式检查本机 Redis")


def render_redacted_redis_connection_info(settings: AppSettings | None = None) -> str:
    """渲染可展示的 Redis 连接摘要。

    参数：`settings` 是统一配置对象，未传入时读取缓存配置。
    返回值：不包含密码的连接摘要。
    失败场景：无预期失败场景；配置读取失败时由 `get_settings()` 抛出异常。
    外部服务：不访问外部服务。
    数据影响：不连接 Redis，不读写 MySQL，不发送 Hermes。
    本函数不负责生成业务 key、价格监控、冷却逻辑或自动交易。
    """

    active_settings = settings or get_settings()
    password_part = ":***REDACTED***@" if active_settings.redis_password else ""
    return (
        f"redis://{password_part}{active_settings.redis_host or '<empty>'}:"
        f"{active_settings.redis_port}/{active_settings.redis_db}"
    )


def create_redis_client(settings: AppSettings | None = None) -> Any:
    """创建 Redis client。

    参数：`settings` 是统一配置对象，未传入时读取缓存配置。
    返回值：Redis client 对象。
    失败场景：配置不完整、依赖缺失或 client 构造失败时抛出 `RedisError`。
    外部服务：构造 client 不主动执行 ping，也不写 Redis。
    数据影响：不读写 MySQL，不写 Redis 业务 key，不发送 Hermes。
    本函数不负责健康检查、价格监控、冷却状态、业务缓存或自动交易。
    """

    active_settings = settings or get_settings()
    _validate_redis_settings_for_connection(active_settings)
    redis_client_class = _load_redis_client_class()

    try:
        client = redis_client_class(
            host=active_settings.redis_host,
            port=active_settings.redis_port,
            db=active_settings.redis_db,
            password=active_settings.redis_password or None,
            socket_timeout=active_settings.redis_socket_timeout,
            decode_responses=active_settings.redis_decode_responses,
        )
    except Exception as exc:  # noqa: BLE001 - 需要包装并脱敏底层驱动错误。
        message = redact_sensitive_text(str(exc), (active_settings.redis_password,))
        raise RedisError(f"Redis client 创建失败：{message}") from exc

    logger = get_logger("redis.client")
    logger.info(
        "redis client created for %s",
        render_redacted_redis_connection_info(active_settings),
    )
    return client


def get_client(*, settings: AppSettings | None = None, reload: bool = False) -> Any:
    """获取缓存的 Redis client。

    参数：`settings` 可显式指定配置；`reload` 为 True 时重建缓存 client。
    返回值：Redis client 对象。
    失败场景：同 `create_redis_client()`。
    外部服务：构造 client 不主动执行 ping，也不写 Redis。
    数据影响：不读写 MySQL，不写 Redis 业务 key，不发送 Hermes。
    本函数不负责业务 key、价格监控、冷却逻辑或自动交易。
    """

    global _CLIENT
    if _CLIENT is None or reload or settings is not None:
        close_client()
        _CLIENT = create_redis_client(settings)
    return _CLIENT


def close_client() -> None:
    """关闭缓存的 Redis client。

    参数：无。
    返回值：无。
    失败场景：底层 client close 失败时由驱动抛出异常。
    外部服务：可能关闭已有 Redis 连接，不主动创建新连接。
    数据影响：不读写 MySQL，不写 Redis 业务 key，不发送 Hermes。
    本函数不负责清理业务状态、删除 key、价格监控或自动交易。
    """

    global _CLIENT
    if _CLIENT is not None and hasattr(_CLIENT, "close"):
        _CLIENT.close()
    _CLIENT = None


get_redis_client = get_client
close_redis_client = close_client
