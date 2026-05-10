"""03 MySQL / Redis 基础设施检查入口。

触发方式：用户手动执行 `python -m scripts.check_infra`。
是否允许用户手动执行：允许。
scheduler 边界：本阶段未提供 scheduler job，也不应被 scheduler 配置引用。
必须参数：无；可选 `--skip-mysql`、`--skip-redis` 用于只做配置级检查。
调用的 app service：`app.storage.mysql.health.check_mysql_health` 和
`app.storage.redis.health.check_redis_health`。
不负责：不采集行情，不检查 K 线连续性，不修复数据，不执行迁移。
数据库影响：默认由用户手动触发 MySQL `SELECT 1` 检查，不创建表，不写业务数据。
Redis 影响：默认由用户手动触发 Redis ping，不写业务 key。
Hermes 影响：不发送 Hermes，不生成提醒记录。
正式 K 线影响：不读取、不写入、不修改正式 K 线表。
自动修复：不允许。
自动交易：不允许。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from app.core.config import AppSettings, load_settings
from app.core.logger import configure_logging, get_logger
from app.core.time_utils import now_prc, now_utc
from app.storage.mysql.health import check_mysql_health
from app.storage.redis.health import check_redis_health


def collect_infra_errors(
    *,
    settings: AppSettings | None = None,
    check_mysql: bool = True,
    check_redis: bool = True,
) -> list[str]:
    """检查基础设施配置和显式健康检查结果。

    参数：`settings` 可注入配置；`check_mysql` / `check_redis` 控制是否连接检查。
    返回值：错误信息列表，空列表表示检查通过。
    失败场景：配置加载失败、日志初始化失败、MySQL 或 Redis 健康检查失败。
    外部服务：只有 `check_mysql` 或 `check_redis` 为 True 时才会访问对应基础设施。
    数据影响：MySQL 仅执行 `SELECT 1`；Redis 仅 ping；不写业务数据，不发送 Hermes。
    本函数不负责业务采集、scheduler 调度、数据修复、migration 或自动交易。
    """

    errors: list[str] = []

    try:
        active_settings = settings or load_settings()
    except Exception as exc:  # noqa: BLE001 - 检查脚本需要汇总错误返回码。
        return [f"配置模块加载失败：{type(exc).__name__}"]

    try:
        configure_logging(active_settings, enable_file=False)
        logger = get_logger("infra.check")
        logger.info("infra check utc=%s prc=%s", now_utc().isoformat(), now_prc().isoformat())
    except Exception as exc:  # noqa: BLE001 - 检查脚本需要汇总错误返回码。
        errors.append(f"logger 初始化失败：{type(exc).__name__}")
        logger = None

    if check_mysql:
        mysql_result = check_mysql_health(active_settings)
        if not mysql_result.ok:
            errors.append(f"MySQL 检查失败：{mysql_result.message}")
    elif logger is not None:
        logger.info("mysql health check skipped by CLI flag")

    if check_redis:
        redis_result = check_redis_health(active_settings)
        if not redis_result.ok:
            errors.append(f"Redis 检查失败：{redis_result.message}")
    elif logger is not None:
        logger.info("redis health check skipped by CLI flag")

    return errors


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析基础设施检查参数。

    参数：`argv` 是可选命令行参数序列。
    返回值：`argparse.Namespace`。
    失败场景：非法参数由 argparse 抛出并返回非 0 状态码。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责连接检查、业务流程、scheduler 或自动交易。
    """

    parser = argparse.ArgumentParser(description="检查 MySQL / Redis 基础设施")
    parser.add_argument("--skip-mysql", action="store_true", help="跳过 MySQL 连接检查")
    parser.add_argument("--skip-redis", action="store_true", help="跳过 Redis 连接检查")
    return parser.parse_args(argv)


def print_infra_report(errors: list[str]) -> None:
    """输出基础设施检查结果。

    参数：`errors` 是 `collect_infra_errors()` 返回的错误列表。
    返回值：无。
    失败场景：标准输出不可用时由 Python 运行时抛出异常。
    外部服务：不访问外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本函数不负责自动修复、业务入库、告警发送或自动交易。
    """

    if errors:
        print("基础设施检查失败：")
        for error in errors:
            print(f"- {error}")
        return

    print("基础设施检查通过。")


def main(argv: Sequence[str] | None = None) -> int:
    """脚本入口函数。

    参数：`argv` 是可选命令行参数序列。
    返回值：检查通过返回 0，检查失败返回 1。
    失败场景：配置、日志、MySQL 或 Redis 检查失败。
    外部服务：默认由用户手动触发 MySQL `SELECT 1` 和 Redis ping。
    数据影响：不创建表，不写业务数据，不写 Redis key，不发送 Hermes，不修改正式数据。
    本入口不负责业务采集、scheduler 调度、数据修复、migration 或自动交易。
    """

    args = parse_args(argv)
    errors = collect_infra_errors(
        check_mysql=not args.skip_mysql,
        check_redis=not args.skip_redis,
    )
    print_infra_report(errors)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
