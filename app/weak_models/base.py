"""Base class for 27A weak models.

本文件属于 `app/weak_models` 模块，负责定义规则型弱模型统一接口。
本文件不读取数据库，不请求 Binance，不发送 Hermes，不调用大模型，不读取账户或仓位，
不生成订单，不自动交易。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.weak_models.types import WeakModelEvaluationInput, WeakModelOutput, WeakModelProfile


class BaseWeakModel(ABC):
    """Base interface for one rule-based weak model.

    参数：`profile` 是已校验的本地配置。
    返回值：子类 `evaluate()` 返回 `WeakModelOutput`。
    失败场景：子类计算异常由 runner/service 捕获为单模型 failed。
    外部服务：禁止访问。
    数据影响：不写 MySQL、Redis，不发送 Hermes，不调用模型，不交易。
    """

    def __init__(self, profile: WeakModelProfile) -> None:
        self.profile = profile

    @property
    def model_key(self) -> str:
        """Return configured model key."""

        return self.profile.model_key

    @abstractmethod
    def evaluate(self, input_data: WeakModelEvaluationInput) -> WeakModelOutput:
        """Evaluate local factor evidence from restored snapshot Klines only."""


__all__ = ["BaseWeakModel"]
