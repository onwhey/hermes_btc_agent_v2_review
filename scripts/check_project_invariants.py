"""Project invariant sanity checker.

该脚本只做轻量级文本检查，用于发现明显文档损坏、旧文件名引用、10s REST 价格轮询残留等问题。

它不是单元测试的替代品，也不是规则豁免工具。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "AGENTS.md",
    "README.md",
    "docs/rules/project_invariants.md",
    "docs/architecture/data_flow.md",
    "docs/architecture/module_boundaries.md",
    "docs/plans/08_4h_backfill.md",
    "docs/plans/09_4h_incremental_collector.md",
    "docs/plans/10_price_monitor_10s.md",
    "docs/plans/11_daily_kline_integrity_check.md",
]

BROKEN_TEXT_PATTERNS = [
    "下面是 `docs/",
    "])ce",
    "]vent parser",
    "scheduler 每 10s 触发",
    "docs/plans/08_4h_kline_manual_backfill.md",
    "docs/plans/09_4h_kline_incremental_collector.md",
]

PLAN10_FORBIDDEN_POSITIVE_PATTERNS = [
    r"scheduler\s*每\s*10s\s*触发",
    r"/fapi/v1/ticker/price\s*作为数据源",
]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_required_files(errors: list[str]) -> None:
    for rel in REQUIRED_FILES:
        if not (ROOT / rel).is_file():
            errors.append(f"缺少必要文件：{rel}")


def check_broken_text(errors: list[str]) -> None:
    for path in (ROOT / "docs").rglob("*.md"):
        text = read_text(path)
        for pattern in BROKEN_TEXT_PATTERNS:
            if pattern in text:
                errors.append(f"发现疑似损坏文本或旧引用：{path.relative_to(ROOT)} -> {pattern}")


def check_plan10_price_source(errors: list[str]) -> None:
    path = ROOT / "docs/plans/10_price_monitor_10s.md"
    if not path.is_file():
        return
    text = read_text(path)
    required = [
        "Binance U 本位合约 WebSocket",
        "btcusdt@aggTrade",
        "bitcoin_price",
        "不得使用 REST 最新价格接口替代 WebSocket",
    ]
    for item in required:
        if item not in text:
            errors.append(f"10s 价格监控文档缺少关键约束：{item}")
    for pattern in PLAN10_FORBIDDEN_POSITIVE_PATTERNS:
        if re.search(pattern, text):
            errors.append(f"10s 价格监控文档存在疑似 REST 价格轮询正向残留：{pattern}")


def check_markdown_fences(errors: list[str]) -> None:
    for path in (ROOT / "docs").rglob("*.md"):
        text = read_text(path)
        fence_count = text.count("```")
        if fence_count % 2 != 0:
            errors.append(f"Markdown 代码块围栏数量异常：{path.relative_to(ROOT)}")


def main() -> int:
    errors: list[str] = []
    check_required_files(errors)
    check_broken_text(errors)
    check_plan10_price_source(errors)
    check_markdown_fences(errors)

    if errors:
        print("项目规则检查失败：")
        for error in errors:
            print(f"- {error}")
        return 1

    print("项目规则轻量检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
