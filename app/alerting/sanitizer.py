"""报警脱敏工具。

本文件属于 `app/alerting` 报警模块，负责在日志、Hermes 响应和
`alert_message.channel_response` 保存前统一脱敏敏感文本。
本文件不负责发送 Hermes，不连接 MySQL，不读写 Redis，不请求 Binance，
不调用 DeepSeek，不涉及任何交易执行。
主要被 Hermes client、报警 service、alert_message repository 和测试调用。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.core.constants import SENSITIVE_TEXT_MARKERS
from app.core.logger import redact_sensitive_text

REDACTED_TEXT = "***REDACTED***"
ADDITIONAL_SENSITIVE_KEY_MARKERS = ("signature", "set-cookie")


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower()
    return any(marker in normalized for marker in SENSITIVE_TEXT_MARKERS) or any(
        marker in normalized for marker in ADDITIONAL_SENSITIVE_KEY_MARKERS
    )


def sanitize_text(value: object, extra_sensitive_values: Sequence[str] = ()) -> str:
    """脱敏文本值。

    参数：`value` 是任意待展示值；`extra_sensitive_values` 是必须精确隐藏的敏感值。
    返回值：脱敏后的字符串。
    失败场景：无预期失败场景。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责判断报警是否应该发送，也不生成交易建议。
    """

    return redact_sensitive_text(str(value), sensitive_values=extra_sensitive_values)


def sanitize_mapping(
    value: Any,
    extra_sensitive_values: Sequence[str] = (),
) -> Any:
    """递归脱敏 mapping、序列和字符串。

    参数：`value` 是待保存或待返回对象；`extra_sensitive_values` 是额外敏感值。
    返回值：结构保持一致但敏感值被替换后的对象。
    失败场景：无预期失败场景。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责持久化，也不修改业务数据。
    """

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key)
            if _is_sensitive_key(key):
                sanitized[normalized_key] = REDACTED_TEXT
            else:
                sanitized[normalized_key] = sanitize_mapping(item, extra_sensitive_values)
        return sanitized

    if isinstance(value, str):
        return sanitize_text(value, extra_sensitive_values)

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [sanitize_mapping(item, extra_sensitive_values) for item in value]

    return value
