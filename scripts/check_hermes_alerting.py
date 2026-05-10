"""兼容 04 plan 中的 Hermes 报警检查入口名称。

触发方式：用户手动执行 `python -m scripts.check_hermes_alerting --dry-run`。
本阶段未提供 scheduler job，也不应被 scheduler 配置引用。
本文件只转发到 `scripts/check_alerting.py::main`，不承载业务逻辑。
默认不真实发送 Hermes；真实发送必须显式传入 `--send-real-alert` 或 `--send-test`。
本文件不请求 Binance，不写 MySQL，不读写 Redis，不调用 DeepSeek，不自动交易。
"""

from __future__ import annotations

from scripts.check_alerting import main


if __name__ == "__main__":
    raise SystemExit(main())

