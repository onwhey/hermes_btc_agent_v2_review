"""核心常量模块。

本文件属于 `app/core` 基础能力层，负责保存 02 阶段允许的通用常量。
本文件不负责策略参数、交易参数、外部请求、数据库读写、Redis 读写、
Hermes 发送、DeepSeek 调用或任何交易执行能力。
主要被 `app/core/config.py`、测试和后续基础模块调用。
"""

from __future__ import annotations

APP_ENV_DEV = "dev"
APP_ENV_TEST = "test"
APP_ENV_PROD = "prod"

DEFAULT_APP_NAME = "hermes_btc_agent"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_TIMEZONE = "UTC"
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_INTERVAL = "4h"

DEFAULT_MYSQL_PORT = 3306
DEFAULT_REDIS_PORT = 6379
DEFAULT_BINANCE_BASE_URL = "https://fapi.binance.com"

LOG_DIR_NAME = "logs"
DEFAULT_LOG_FILE_NAME = "app.log"

SENSITIVE_FIELD_NAMES = frozenset(
    {
        "mysql_password",
        "redis_password",
        "hermes_webhook_url",
        "hermes_secret",
    }
)

SENSITIVE_TEXT_MARKERS = (
    "password",
    "secret",
    "token",
    "webhook",
    "authorization",
    "cookie",
)

