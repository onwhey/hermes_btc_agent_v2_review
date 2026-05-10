"""Redis 健康检查模块。

本文件属于 `app/storage/redis` 基础设施层，负责在被显式调用时执行 Redis
连接检查和 ping 级别检查。
本文件不负责写入业务 key，不缓存价格，不实现提醒冷却，不连接 MySQL，
不发送 Hermes，不请求 Binance，不调用 DeepSeek，不实现任何交易执行能力。
主要被 `scripts/check_infra.py` 和测试调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus

from app.core.config import AppSettings, get_settings
from app.core.logger import get_logger, redact_sensitive_text

from .client import create_redis_client, render_redacted_redis_connection_info


@dataclass(frozen=True)
class RedisHealthCheckResult:
    """Redis 健康检查结果。

    参数：`ok` 表示是否通过；`message` 是脱敏后的可读摘要。
    返回值：不可变结果对象。
    失败场景：无预期失败场景；底层异常由 `check_redis_health()` 捕获并脱敏。
    外部服务：本类不访问外部服务。
    数据影响：不读写 MySQL，不写 Redis，不发送 Hermes。
    本类不负责业务 key、价格监控、冷却逻辑或自动交易。
    """

    ok: bool
    message: str


def check_redis_health(settings: AppSettings | None = None) -> RedisHealthCheckResult:
    """执行 Redis 基础健康检查。

    参数：`settings` 是统一配置对象，未传入时读取缓存配置。
    返回值：`RedisHealthCheckResult`，失败时 `ok=False` 且包含脱敏原因。
    失败场景：配置不完整、依赖缺失、连接失败或 ping 失败。
    外部服务：被调用时会尝试连接 Redis 并执行 ping。
    数据影响：不写 Redis 业务 key，不读写 MySQL，不发送 Hermes。
    本函数不负责重试、业务报警、价格监控、冷却状态或自动交易。
    """

    active_settings = settings or get_settings()
    logger = get_logger("redis.health")
    target = render_redacted_redis_connection_info(active_settings)
    logger.info("redis health check started for %s", target)

    client = None
    try:
        client = create_redis_client(active_settings)
        ping_result = client.ping()
        if ping_result is not True:
            return RedisHealthCheckResult(ok=False, message="Redis ping 返回非 True 结果")
        logger.info("redis health check passed for %s", target)
        return RedisHealthCheckResult(ok=True, message="Redis health check passed")
    except Exception as exc:  # noqa: BLE001 - health check 需要汇总明确失败原因。
        message = redact_sensitive_text(
            str(exc),
            (active_settings.redis_password, quote_plus(active_settings.redis_password), target),
        )
        logger.error("redis health check failed: %s", message)
        return RedisHealthCheckResult(ok=False, message=message)
    finally:
        if client is not None and hasattr(client, "close"):
            client.close()
