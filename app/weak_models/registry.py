"""Weak model registry for 27A local factor profiles.

本文件属于 `app/weak_models` 模块，负责把 `WeakModelProfile` 映射为
`BaseWeakModel` 实例。
本文件不运行模型，不读取数据库，不请求 Binance，不发送 Hermes，不调用大模型，
不读取账户或仓位，不生成订单，不自动交易。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from app.weak_models.base import BaseWeakModel
from app.weak_models.config import load_weak_model_profiles
from app.weak_models.models import (
    MarketRegimeContextModel,
    SupportDistanceConfirmationModel,
    TrendStrengthDirectionalModel,
    VolatilityRiskGateModel,
)
from app.weak_models.types import WeakModelProfile


class WeakModelRegistryError(ValueError):
    """Raised when the weak model registry cannot build model instances."""


class WeakModelRegistry:
    """Load configured weak model instances in registry order."""

    def __init__(
        self,
        *,
        config_dir: Path | None = None,
        model_classes: Mapping[str, type[BaseWeakModel]] | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._model_classes = dict(model_classes or _default_model_classes())

    def load_profiles(self) -> tuple[WeakModelProfile, ...]:
        """Return all configured weak model profiles."""

        return load_weak_model_profiles(self._config_dir)

    def load_enabled_models(self) -> tuple[BaseWeakModel, ...]:
        """Return enabled weak model instances; disabled/deprecated are skipped."""

        models: list[BaseWeakModel] = []
        for profile in self.load_profiles():
            if not profile.enabled or profile.maturity_stage in {"disabled", "deprecated"}:
                continue
            model_class = self._model_classes.get(profile.model_key)
            if model_class is None:
                raise WeakModelRegistryError(f"unsupported weak model configured: {profile.model_key}")
            models.append(model_class(profile))
        return tuple(models)


def create_default_weak_model_registry(*, config_dir: Path | None = None) -> WeakModelRegistry:
    """Create the default 27A weak model registry."""

    return WeakModelRegistry(config_dir=config_dir)


def _default_model_classes() -> dict[str, type[BaseWeakModel]]:
    return {
        "trend_strength_directional": TrendStrengthDirectionalModel,
        "volatility_risk_gate": VolatilityRiskGateModel,
        "support_distance_confirmation": SupportDistanceConfirmationModel,
        "market_regime_context": MarketRegimeContextModel,
    }


__all__ = [
    "WeakModelRegistry",
    "WeakModelRegistryError",
    "create_default_weak_model_registry",
]
