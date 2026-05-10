"""01 项目骨架检查入口。

触发方式：用户手动执行 `python -m scripts.check_project_skeleton`。
是否允许用户手动执行：允许。
是否允许 scheduler 调用：本阶段不需要也不允许作为 scheduler 任务。
必须参数：无。
调用的 app service：无，只检查 `app` 包能否导入。
不负责：不实现业务流程、不采集行情、不检查 K 线连续性、不修复数据。
数据库影响：不连接 MySQL，不创建表，不写入数据。
Redis 影响：不连接 Redis，不读取或写入 key。
Hermes 影响：不发送 Hermes，不生成提醒记录。
正式 K 线影响：不读取、不写入、不修改正式 K 线表。
自动修复：不允许。
自动交易：不允许。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MINIMUM_PYTHON = (3, 10)

REQUIRED_DIRECTORIES = [
    "app",
    "app/alerting",
    "app/core",
    "app/exchange",
    "app/exchange/binance",
    "app/storage",
    "app/storage/mysql",
    "app/storage/redis",
    "app/market_data",
    "app/scheduler",
    "app/monitoring",
    "configs",
    "migrations",
    "scripts",
    "tests",
    "logs",
    "docs/implementation",
]

REQUIRED_FILES = [
    "pyproject.toml",
    ".env.example",
    ".gitignore",
    "README.md",
    "AGENTS.md",
    "alembic.ini",
    "scripts/check_project_skeleton.py",
    "tests/test_project_skeleton.py",
]


def collect_project_skeleton_errors() -> list[str]:
    """检查项目骨架是否满足 01 阶段最小要求。

    参数：无。
    返回值：错误信息列表，空列表表示检查通过。
    失败场景：Python 版本过低、关键目录或文件缺失、`app` 包无法导入。
    外部服务：不访问任何外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes，不修改数据质量记录。
    本方法不负责业务校验、K 线校验、数据库迁移或自动交易。
    """

    errors: list[str] = []

    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(str(part) for part in MINIMUM_PYTHON)
        current = f"{sys.version_info.major}.{sys.version_info.minor}"
        errors.append(f"Python 版本过低：当前 {current}，要求 >= {required}")

    for directory in REQUIRED_DIRECTORIES:
        if not (ROOT / directory).is_dir():
            errors.append(f"缺少目录：{directory}")

    for file_path in REQUIRED_FILES:
        if not (ROOT / file_path).is_file():
            errors.append(f"缺少文件：{file_path}")

    try:
        importlib.import_module("app")
    except ImportError as exc:
        errors.append(f"app 包无法导入：{exc}")

    return errors


def print_project_skeleton_report(errors: list[str]) -> None:
    """输出骨架检查结果。

    参数：`errors` 是 `collect_project_skeleton_errors` 返回的错误列表。
    返回值：无。
    失败场景：标准输出不可用时由 Python 运行时抛出异常。
    外部服务：不访问任何外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes。
    本方法不负责创建缺失目录或自动修复项目结构。
    """

    if errors:
        print("项目骨架检查失败：")
        for error in errors:
            print(f"- {error}")
        return

    print("项目骨架检查通过。")


def main() -> int:
    """脚本入口函数。

    参数：无命令行参数。
    返回值：检查通过返回 0，检查失败返回 1。
    失败场景：关键目录缺失、关键文件缺失、Python 版本过低或 `app` 包无法导入。
    外部服务：不访问任何外部服务。
    数据影响：不读写 MySQL，不读写 Redis，不发送 Hermes，不修改正式数据。
    本入口不负责业务采集、scheduler 调度、数据修复或自动交易。
    """

    errors = collect_project_skeleton_errors()
    print_project_skeleton_report(errors)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

