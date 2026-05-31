"""Strategy pipeline observability package.

本包属于 `app/strategy_observability`，只负责策略链路运行状态的只读观测。
本包不负责修改 25 pipeline 调度，不负责修改策略算法，不调用大模型，不发送
Hermes，不读写 Redis，不读取账户或持仓，不涉及交易执行。

主要被 `scripts/check_strategy_pipeline_status.py` 和单元测试调用。
"""

__all__ = []
