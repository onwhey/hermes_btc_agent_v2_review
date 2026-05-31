"""26C strategy pipeline observation index package.

本模块只负责把已有 4h K线 slot 与已有 25 pipeline 结果整理为轻量观察索引。
本模块不做复盘分析，不请求 Binance，不调用 DeepSeek 或其他大模型，不发送
Hermes，不读取账户或仓位，不生成订单，不自动交易。
"""

from __future__ import annotations

__all__: list[str] = []
