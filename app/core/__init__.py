"""核心基础能力包边界。

本包承载配置读取、日志初始化、异常、常量和时间工具等基础能力。
本包不访问外部服务，不连接 MySQL 或 Redis，不发送 Hermes，不调用 DeepSeek，
不实现 K 线采集、策略建议、scheduler 或任何交易执行能力。
"""

from app.core.config import AppSettings, get_settings, load_settings
from app.core.exceptions import AppError, ConfigError, ExternalServiceError, ValidationError
from app.core.logger import configure_logging, get_logger

__all__ = [
    "AppError",
    "AppSettings",
    "ConfigError",
    "ExternalServiceError",
    "ValidationError",
    "configure_logging",
    "get_logger",
    "get_settings",
    "load_settings",
]


