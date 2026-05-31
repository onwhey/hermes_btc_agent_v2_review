"""26B strategy evidence quality gate package.

本模块属于 `app/strategy/evidence_quality`，负责 25 pipeline 在 23F/24
证据聚合之后、18 材料包之前的只读证据质量判断与阻断结果记录。
本模块不实现策略算法，不生成材料包，不调用大模型，不读取账户或仓位，
不生成订单，不自动交易。
"""

from app.strategy.evidence_quality.service import (
    StrategyEvidenceQualityGateService,
    create_default_strategy_evidence_quality_gate_service,
)
from app.strategy.evidence_quality.types import (
    STRATEGY_EVIDENCE_QUALITY_ERROR_CODE,
    STRATEGY_EVIDENCE_QUALITY_STEP,
    StrategyEvidenceQualityGateRequest,
    StrategyEvidenceQualityGateResult,
)

__all__ = [
    "STRATEGY_EVIDENCE_QUALITY_ERROR_CODE",
    "STRATEGY_EVIDENCE_QUALITY_STEP",
    "StrategyEvidenceQualityGateRequest",
    "StrategyEvidenceQualityGateResult",
    "StrategyEvidenceQualityGateService",
    "create_default_strategy_evidence_quality_gate_service",
]
