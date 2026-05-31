"""27A/27B weak model / factor layer package.

本模块只负责规则型弱模型的配置加载、运行、聚合、审计落库和输出质量观测。
本模块不请求 Binance，不调用 DeepSeek/GPT/Claude，不发送 Hermes，不读取账户或仓位，
不生成订单，不自动交易，不接 scheduler 自动任务。
"""

from __future__ import annotations

__all__: list[str] = []
