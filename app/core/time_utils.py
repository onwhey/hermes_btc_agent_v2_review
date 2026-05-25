"""UTC / PRC 时间工具模块。

本文件属于 `app/core` 基础能力层，负责统一处理 UTC 与 PRC 时间转换。
本文件不负责 K 线采集、数据库读写、Redis 读写、Hermes 发送、DeepSeek 调用
或任何交易执行能力。主要被后续行情、提醒、日志和测试模块复用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
DEFAULT_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _build_prc_timezone() -> timezone | ZoneInfo:
    """构建 PRC 时区对象。

    参数：无。
    返回值：优先返回 `ZoneInfo("Asia/Shanghai")`，系统缺少时区库时返回固定 offset。
    失败场景：无预期失败场景。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数只为集中时间工具提供时区对象，不负责业务排序或自动交易。
    """

    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(seconds=28800), name="Asia/Shanghai")


PRC_TIME_ZONE = _build_prc_timezone()


def now_utc() -> datetime:
    """返回当前 UTC aware 时间。

    参数：无。
    返回值：`tzinfo=UTC` 的当前 datetime。
    失败场景：系统时间不可用时由 Python 运行时抛出异常。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责业务排序、K 线收盘判断或自动交易。
    """

    return datetime.now(tz=UTC)


def now_prc() -> datetime:
    """返回当前 PRC aware 时间。

    参数：无。
    返回值：`tzinfo=Asia/Shanghai` 的当前 datetime，仅用于展示和排查。
    失败场景：系统时间不可用时由 Python 运行时抛出异常。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责业务排序、K 线连续性判断或自动交易。
    """

    return now_utc().astimezone(PRC_TIME_ZONE)


def utc_naive_to_prc_naive(value: datetime) -> datetime:
    """将 UTC naive datetime 转为 PRC naive datetime。

    参数：`value` 必须是不带 tzinfo 的 UTC datetime。
    返回值：不带 tzinfo 的 PRC datetime，仅用于用户阅读、排查和展示。
    失败场景：传入 aware datetime 时抛出 `ValueError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责业务排序，PRC 结果不得用于 K 线连续性判断。
    """

    if value.tzinfo is not None:
        raise ValueError("utc_naive_to_prc_naive 只接受 UTC naive datetime")
    return value.replace(tzinfo=UTC).astimezone(PRC_TIME_ZONE).replace(tzinfo=None)


def utc_aware_to_prc_aware(value: datetime) -> datetime:
    """将 UTC aware datetime 转为 PRC aware datetime。

    参数：`value` 必须是带 tzinfo 的 datetime，会先转换到 UTC 再转换到 PRC。
    返回值：`tzinfo=Asia/Shanghai` 的 datetime。
    失败场景：传入 naive datetime 时抛出 `ValueError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责业务排序，PRC 结果不得用于 K 线连续性判断。
    """

    if value.tzinfo is None:
        raise ValueError("utc_aware_to_prc_aware 只接受 aware datetime")
    return value.astimezone(UTC).astimezone(PRC_TIME_ZONE)


def ensure_utc_aware(value: datetime | None) -> datetime | None:
    """Return a UTC aware datetime while preserving None safely.

    Parameters: `value` is a UTC datetime read from code or database; MySQL may
    return UTC columns as naive datetime objects.
    Return value: `None`, or a datetime with `tzinfo=UTC`.
    Failure scenarios: non-datetime values fail naturally when callers pass an
    invalid object.
    External services: none. Data impact: no MySQL, Redis, Hermes, DeepSeek, or
    trading execution. This helper only labels naive UTC values as UTC; it does
    not perform PRC display conversion or business-time ordering.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def timestamp_ms_to_utc_datetime(timestamp_ms: int) -> datetime:
    """将毫秒时间戳转换为 UTC aware datetime。

    参数：`timestamp_ms` 是以 UTC 为准的毫秒时间戳。
    返回值：`tzinfo=UTC` 的 datetime。
    失败场景：时间戳类型非法时由 Python 运行时抛出异常。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责请求 Binance 或判断 K 线是否收盘。
    """

    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)


def utc_datetime_to_timestamp_ms(value: datetime) -> int:
    """将 UTC datetime 转换为毫秒时间戳。

    参数：`value` 是 UTC naive 或 aware datetime；naive 值按 UTC 解释。
    返回值：整数毫秒时间戳。
    失败场景：传入非 datetime 时由 Python 运行时抛出异常。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责业务排序、K 线校验或自动交易。
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return int(value.timestamp() * 1000)


def is_utc_datetime(value: datetime) -> bool:
    """判断 datetime 是否为 UTC aware 时间。

    参数：`value` 是待检查 datetime。
    返回值：带 tzinfo 且 UTC offset 为 0 时返回 True，否则 False。
    失败场景：传入非 datetime 时由 Python 运行时抛出异常。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责转换时间或自动修复调用方数据。
    """

    return value.tzinfo is not None and value.utcoffset() == UTC.utcoffset(value)


def format_datetime_with_timezone(
    value: datetime,
    *,
    fmt: str = DEFAULT_DATETIME_FORMAT,
) -> str:
    """格式化 datetime 并显式标注时区。

    参数：`value` 是待展示时间；`fmt` 是 `strftime` 格式。
    返回值：带 `UTC`、`北京时间` 或 offset 标记的字符串。
    失败场景：naive datetime 无法判断时区时抛出 `ValueError`。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责生成业务提醒或策略建议。
    """

    if value.tzinfo is None:
        raise ValueError("格式化带时区展示时必须传入 aware datetime")
    if is_utc_datetime(value):
        suffix = "UTC"
    elif value.utcoffset() == PRC_TIME_ZONE.utcoffset(value):
        suffix = "北京时间"
    else:
        suffix = value.strftime("%z")
    return f"{value.strftime(fmt)} {suffix}"
