"""Service layer for read-only strategy pipeline observability.

本文件属于 `app/strategy_observability` 模块，负责 26A 策略链路运行观测的
slot 级状态判定和汇总。

调用链：

```text
用户 CLI
    ↓
scripts/check_strategy_pipeline_status.py::main
    ↓
app/strategy_observability/service.py::check_strategy_pipeline_status
    ↓
app/strategy_observability/repository.py::list_recent_closed_kline_slots
    ↓
app/strategy_observability/repository.py::list_pipeline_runs_for_slots
    ↓
app/strategy_observability/repository.py::load_link_records_for_pipeline_runs
```

本文件不负责数据库 SQL 细节，不负责调用 25 pipeline，不负责修改 18/19/20/21
核心逻辑，不调用真实模型，不发送 Hermes，不读写 Redis，不读取账户或持仓，
不生成订单，不涉及自动交易。

外部服务：不访问。
MySQL：通过 repository 只读查询。
Redis：不读写。
Hermes：不发送。
DeepSeek/其他大模型：不调用。
交易执行：不涉及。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.core.config import AppSettings, get_settings
from app.strategy_observability.repository import (
    StrategyPipelineObservabilityRepository,
    create_default_strategy_pipeline_observability_repository,
)
from app.strategy_observability.types import (
    EXIT_SUCCESS,
    EXIT_UNHEALTHY,
    KlineSlotRecord,
    ObservabilityConfigSnapshot,
    SlotObservationStatus,
    StrategyPipelineLinkRecord,
    StrategyPipelineRunRecord,
    StrategyPipelineSlotObservation,
    StrategyPipelineStatusReport,
    StrategyPipelineStatusRequest,
)
from app.strategy_pipeline.types import PIPELINE_STEP_STAGE20

EXPECTED_MODEL_BLOCK_ERROR_CODES = {
    "no_model_review_result",
    "real_model_disabled",
    "model_review_expired_but_real_model_disabled",
    "model_review_real_model_disabled",
    "model_review_scheduler_worker_disabled",
    "model_review_auto_run_disabled",
    "cli_real_model_cost_not_confirmed",
}
SUCCESS_PIPELINE_STATUSES = {"success"}
FAILED_PIPELINE_STATUSES = {"failed"}
BLOCKED_PIPELINE_STATUSES = {"blocked"}


class StrategyPipelineObservabilityService:
    """Build a read-only strategy pipeline observability report.

    参数：
    - `settings`：只读取非敏感开关用于合理阻断判断。
    - `repository`：只读 MySQL repository，可在测试中注入 fake。

    返回值：`StrategyPipelineStatusReport`。
    失败场景：repository/database 异常向 CLI 抛出并映射为 exit_code=2。
    外部服务：不访问。
    数据影响：不写 MySQL、不写 Redis、不发送 Hermes、不调用模型。
    """

    def __init__(
        self,
        *,
        settings: AppSettings | Any | None = None,
        repository: StrategyPipelineObservabilityRepository | Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repository = repository or create_default_strategy_pipeline_observability_repository()

    def check_strategy_pipeline_status(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineStatusRequest,
    ) -> StrategyPipelineStatusReport:
        """Check recent closed 4h slots without triggering downstream stages."""

        config = build_observability_config_snapshot(self._settings)
        slots = self._repository.list_recent_closed_kline_slots(
            db_session,
            symbol=request.symbol,
            base_interval=request.base_interval,
            limit=request.limit,
        )
        pipelines_by_slot = self._repository.list_pipeline_runs_for_slots(
            db_session,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            slots=slots,
        )
        all_pipeline_runs = tuple(run for runs in pipelines_by_slot.values() for run in runs)
        links_by_pipeline_id = self._repository.load_link_records_for_pipeline_runs(
            db_session,
            pipeline_runs=all_pipeline_runs,
        )
        observations = tuple(
            self._observe_one_slot(
                slot=slot,
                pipeline_runs=pipelines_by_slot.get(slot.open_time_utc, ()),
                links_by_pipeline_id=links_by_pipeline_id,
                config=config,
            )
            for slot in slots
        )
        counts = Counter(observation.status.value for observation in observations)
        exit_code = _exit_code_for_observations(observations)
        return StrategyPipelineStatusReport(
            request=request,
            config=config,
            observations=observations,
            exit_code=exit_code,
            summary_counts=dict(counts),
        )

    def _observe_one_slot(
        self,
        *,
        slot: KlineSlotRecord,
        pipeline_runs: tuple[StrategyPipelineRunRecord, ...],
        links_by_pipeline_id: dict[str, StrategyPipelineLinkRecord],
        config: ObservabilityConfigSnapshot,
    ) -> StrategyPipelineSlotObservation:
        """Classify one Kline slot using only persisted audit rows."""

        if not pipeline_runs:
            return StrategyPipelineSlotObservation(
                slot_utc=slot.open_time_utc,
                kline_open_time_ms=slot.open_time_ms,
                status=SlotObservationStatus.MISSING,
                reason="该 4h K线已存在，但未找到对应 25 pipeline。",
                blocked_reasonable=None,
            )

        representative = pipeline_runs[0]
        links = links_by_pipeline_id.get(
            representative.pipeline_run_id,
            _links_from_pipeline_record(representative),
        )
        if len(pipeline_runs) > 1:
            return _build_observation_from_pipeline(
                slot=slot,
                pipeline_runs=pipeline_runs,
                pipeline=representative,
                links=links,
                status=SlotObservationStatus.DUPLICATE,
                reason="同一 slot 存在多个 pipeline_run，请检查幂等或手动重复触发；若为人工 retry，需要人工确认。",
                blocked_reasonable=None,
            )

        pipeline_status = representative.status.strip().lower()
        if pipeline_status in SUCCESS_PIPELINE_STATUSES:
            if _success_links_complete(links):
                return _build_observation_from_pipeline(
                    slot=slot,
                    pipeline_runs=pipeline_runs,
                    pipeline=representative,
                    links=links,
                    status=SlotObservationStatus.HEALTHY,
                    reason="pipeline 已完成，关键链路 ID 已形成。",
                    blocked_reasonable=None,
                )
            return _build_observation_from_pipeline(
                slot=slot,
                pipeline_runs=pipeline_runs,
                pipeline=representative,
                links=links,
                status=SlotObservationStatus.UNKNOWN,
                reason="pipeline 标记为 success，但关键链路 ID 不完整，需要人工核对持久化记录。",
                blocked_reasonable=None,
            )

        if pipeline_status in FAILED_PIPELINE_STATUSES:
            return _build_observation_from_pipeline(
                slot=slot,
                pipeline_runs=pipeline_runs,
                pipeline=representative,
                links=links,
                status=SlotObservationStatus.FAILED,
                reason="pipeline 状态为 failed。",
                blocked_reasonable=None,
            )

        if pipeline_status in BLOCKED_PIPELINE_STATUSES:
            expected, reason = _is_expected_model_block(pipeline=representative, config=config)
            return _build_observation_from_pipeline(
                slot=slot,
                pipeline_runs=pipeline_runs,
                pipeline=representative,
                links=links,
                status=SlotObservationStatus.EXPECTED_BLOCKED if expected else SlotObservationStatus.FAILED,
                reason=reason,
                blocked_reasonable=expected,
            )

        return _build_observation_from_pipeline(
            slot=slot,
            pipeline_runs=pipeline_runs,
            pipeline=representative,
            links=links,
            status=SlotObservationStatus.UNKNOWN,
            reason=f"pipeline 状态为 {representative.status}，26A 第一版无法判断为健康或合理阻断。",
            blocked_reasonable=None,
        )


def build_observability_config_snapshot(settings: AppSettings | Any) -> ObservabilityConfigSnapshot:
    """Build a non-sensitive config snapshot from the shared settings object."""

    return ObservabilityConfigSnapshot(
        strategy_pipeline_enabled=bool(getattr(settings, "strategy_pipeline_enabled", False)),
        strategy_pipeline_scheduler_enabled=bool(getattr(settings, "strategy_pipeline_scheduler_enabled", False)),
        strategy_evidence_aggregation_enabled=bool(getattr(settings, "strategy_evidence_aggregation_enabled", False)),
        strategy_pipeline_real_model_enabled=bool(getattr(settings, "strategy_pipeline_real_model_enabled", False)),
        strategy_pipeline_confirm_real_model_cost=bool(
            getattr(settings, "strategy_pipeline_confirm_real_model_cost", False)
        ),
        model_review_real_model_enabled=bool(getattr(settings, "model_review_real_model_enabled", False)),
        strategy_pipeline_notification_send_enabled=bool(
            getattr(settings, "strategy_pipeline_notification_send_enabled", False)
        ),
        strategy_advice_notification_send_enabled=bool(
            getattr(settings, "strategy_advice_notification_send_enabled", False)
        ),
    )


def check_strategy_pipeline_status(
    *,
    db_session: Any,
    request: StrategyPipelineStatusRequest,
    service: StrategyPipelineObservabilityService | None = None,
) -> StrategyPipelineStatusReport:
    """Convenience function used by the 26A CLI."""

    active_service = service or create_default_strategy_pipeline_observability_service()
    return active_service.check_strategy_pipeline_status(db_session, request=request)


def create_default_strategy_pipeline_observability_service() -> StrategyPipelineObservabilityService:
    """Create the default 26A observability service."""

    return StrategyPipelineObservabilityService()


def _is_expected_model_block(
    *,
    pipeline: StrategyPipelineRunRecord,
    config: ObservabilityConfigSnapshot,
) -> tuple[bool, str]:
    """Return whether a blocked pipeline is reasonable under safe-mode gates.

    安全模式下真实模型关闭时，20C/19/20A 阶段可能因为没有模型审查结果而阻断。
    这种阻断不应被误报为系统失败；但如果真实模型开关已全部开启，同样的
    `no_model_review_result` 必须暴露为异常失败。
    """

    error_code = (pipeline.error_code or "").strip()
    stopped_at_model_step = (pipeline.current_step or "").strip() == PIPELINE_STEP_STAGE20
    model_safety_gate_closed = not config.real_model_allowed_for_pipeline
    if stopped_at_model_step and model_safety_gate_closed and error_code in EXPECTED_MODEL_BLOCK_ERROR_CODES:
        return True, "安全模式下真实模型关闭，pipeline 合理停在 20C/19/20A。"
    if stopped_at_model_step and error_code in EXPECTED_MODEL_BLOCK_ERROR_CODES:
        return False, "真实模型开关已开启或成本确认已开启，但仍缺少模型审查结果，需要排查。"
    return False, "pipeline 被 blocked，但不属于 26A 已知的安全模式合理阻断。"


def _build_observation_from_pipeline(
    *,
    slot: KlineSlotRecord,
    pipeline_runs: tuple[StrategyPipelineRunRecord, ...],
    pipeline: StrategyPipelineRunRecord,
    links: StrategyPipelineLinkRecord,
    status: SlotObservationStatus,
    reason: str,
    blocked_reasonable: bool | None,
) -> StrategyPipelineSlotObservation:
    return StrategyPipelineSlotObservation(
        slot_utc=slot.open_time_utc,
        kline_open_time_ms=slot.open_time_ms,
        status=status,
        reason=reason,
        pipeline_run_ids=tuple(run.pipeline_run_id for run in pipeline_runs),
        pipeline_status=pipeline.status,
        current_step=pipeline.current_step,
        links=links,
        real_model_called=pipeline.real_model_called,
        hermes_real_sent=pipeline.hermes_real_sent,
        error_code=pipeline.error_code,
        error_message=pipeline.error_message,
        blocked_reasonable=blocked_reasonable,
    )


def _links_from_pipeline_record(pipeline: StrategyPipelineRunRecord) -> StrategyPipelineLinkRecord:
    return StrategyPipelineLinkRecord(
        pipeline_run_id=pipeline.pipeline_run_id,
        strategy_signal_run_id=pipeline.strategy_signal_run_id,
        strategy_evidence_aggregation_id=pipeline.strategy_evidence_aggregation_id,
        material_pack_id=pipeline.material_pack_id,
        review_aggregation_run_id=pipeline.review_aggregation_run_id,
        advice_lifecycle_review_id=pipeline.review_id,
    )


def _success_links_complete(links: StrategyPipelineLinkRecord) -> bool:
    """Return whether a successful pipeline has all required persisted ids."""

    return all(
        (
            links.pipeline_run_id,
            links.strategy_signal_run_id and links.strategy_signal_run_exists,
            links.strategy_evidence_aggregation_id and links.strategy_evidence_aggregation_exists,
            links.material_pack_id and links.material_pack_exists,
            links.review_aggregation_run_id and links.review_aggregation_run_exists,
            links.advice_lifecycle_review_id and links.advice_lifecycle_review_exists,
        )
    )


def _exit_code_for_observations(observations: tuple[StrategyPipelineSlotObservation, ...]) -> int:
    if not observations:
        return EXIT_UNHEALTHY
    allowed = {SlotObservationStatus.HEALTHY, SlotObservationStatus.EXPECTED_BLOCKED}
    return EXIT_SUCCESS if all(observation.status in allowed for observation in observations) else EXIT_UNHEALTHY


__all__ = [
    "StrategyPipelineObservabilityService",
    "build_observability_config_snapshot",
    "check_strategy_pipeline_status",
    "create_default_strategy_pipeline_observability_service",
]
