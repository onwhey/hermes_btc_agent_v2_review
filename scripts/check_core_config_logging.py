"""02 核心配置、日志、时间工具检查入口。

触发方式：用户手动执行 `python -m scripts.check_core_config_logging`。
是否允许用户手动执行：允许。
是否允许 scheduler 调用：本阶段不需要也不允许作为 scheduler 任务。
必须参数：无。
调用的 app service：无，只调用 `app.core` 基础模块。
不负责：不实现业务流程、不采集行情、不检查 K 线连续性、不修复数据。
数据库影响：不连接 MySQL，不创建表，不写入数据。
Redis 影响：不连接 Redis，不读取或写入 key。
Hermes 影响：不发送 Hermes，不生成提醒记录。
正式 K 线影响：不读取、不写入、不修改正式 K 线表。
自动修复：不允许。
自动交易：不允许。
"""

from __future__ import annotations

from datetime import datetime

from app.core.config import load_settings
from app.core.exceptions import AppError, ConfigError, ExternalServiceError, ValidationError
from app.core.logger import configure_logging
from app.core.time_utils import now_prc, now_utc, utc_naive_to_prc_naive


def collect_core_config_logging_errors() -> list[str]:
    """检查 02 阶段核心模块是否可用。

    参数：无。
    返回值：错误信息列表，空列表表示检查通过。
    失败场景：配置加载失败、logger 初始化失败、时间工具不可调用或异常类不可实例化。
    外部服务：不访问任何外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes，不修改数据质量记录。
    本方法不负责业务校验、K 线校验、数据库迁移或自动交易。
    """

    errors: list[str] = []

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001 - 检查脚本需要汇总错误返回码。
        errors.append(f"配置模块加载失败：{type(exc).__name__}")
        return errors

    try:
        logger = configure_logging(settings, enable_file=False)
        logger.info("core config logging check initialized")
    except Exception as exc:  # noqa: BLE001 - 检查脚本需要汇总错误返回码。
        errors.append(f"logger 初始化失败：{type(exc).__name__}")

    try:
        utc_now = now_utc()
        prc_now = now_prc()
        converted = utc_naive_to_prc_naive(datetime(2026, 1, 1, 0, 0, 0))
        if utc_now.tzinfo is None:
            errors.append("now_utc 返回了 naive datetime")
        if prc_now.tzinfo is None:
            errors.append("now_prc 返回了 naive datetime")
        if converted.tzinfo is not None:
            errors.append("utc_naive_to_prc_naive 应返回 naive datetime")
    except Exception as exc:  # noqa: BLE001 - 检查脚本需要汇总错误返回码。
        errors.append(f"时间工具检查失败：{type(exc).__name__}")

    for error_class in (AppError, ConfigError, ValidationError, ExternalServiceError):
        try:
            error_class("check")
        except Exception as exc:  # noqa: BLE001 - 检查脚本需要汇总错误返回码。
            errors.append(f"异常类不可实例化：{error_class.__name__} -> {type(exc).__name__}")

    return errors


def print_core_config_logging_report(errors: list[str]) -> None:
    """输出核心模块检查结果。

    参数：`errors` 是 `collect_core_config_logging_errors` 返回的错误列表。
    返回值：无。
    失败场景：标准输出不可用时由 Python 运行时抛出异常。
    外部服务：不访问任何外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本方法不负责创建缺失文件或自动修复配置。
    """

    if errors:
        print("核心配置日志检查失败：")
        for error in errors:
            print(f"- {error}")
        return

    print("核心配置日志检查通过。")


def main() -> int:
    """脚本入口函数。

    参数：无命令行参数。
    返回值：检查通过返回 0，检查失败返回 1。
    失败场景：配置、日志、时间工具或异常类检查失败。
    外部服务：不访问任何外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes，不修改正式数据。
    本入口不负责业务采集、scheduler 调度、数据修复或自动交易。
    """

    errors = collect_core_config_logging_errors()
    print_core_config_logging_report(errors)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

