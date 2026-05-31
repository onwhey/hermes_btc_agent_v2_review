"""Service for 26C strategy pipeline observation index.

本文件属于 `app/strategy_pipeline_observation` 模块，负责 26C-A 的
canonical pipeline 选择、observation 状态判定和写库编排。

调用链：

```text
用户 CLI
    ↓
scripts/build_strategy_pipeline_observations.py::main
    ↓
app/strategy_pipeline_observation/service.py::build_strategy_pipeline_observations
    ↓
app/strategy_pipeline_observation/repository.py::list_kline_slots
    ↓
app/strategy_pipeline_observation/repository.py::list_pipeline_runs_for_slots
    ↓
app/strategy_pipeline_observation/repository.py::load_evidence_quality_by_pipeline_run
    ↓
app/strategy_pipeline_observation/repository.py::load_advice_links_by_pipeline_run
    ↓
app/strategy_pipeline_observation/repository.py::upsert_observation
```

本文件不负责数据库 SQL 细节，不做复盘分析，不请求 Binance，不重新运行
16/23F/26B/18/20/21，不发送 Hermes，不读写 Redis，不调用 DeepSeek 或其他
大模型，不读取账户或仓位，不生成订单，不自动交易。

外部服务：不访问。
MySQL：通过 repository 读取已有结果；confirm-write 时只写 observation 索引。
Redis：不读写。
Hermes：不发送。
模型：不调用。
交易执行：不涉及。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Mapping

from app.core.config import AppSettings, get_settings
from app.core.time_utils import UTC, ensure_utc_aware
from app.market_data.kline_constants import TRIGGER_SOURCE_CLI, TRIGGER_SOURCE_SCHEDULER
from app.strategy_pipeline.types import PIPELINE_STEP_STAGE20, PIPELINE_STEP_STAGE26B
from app.strategy_pipeline_observation.repository import (
    StrategyPipelineObservationRepository,
    create_default_strategy_pipeline_observation_repository,
)
from app.strategy_pipeline_observation.types import (
    CANONICAL_REASON_NO_PIPELINE,
    CANONICAL_REASON_ONLY_CLI_RUNS,
    CANONICAL_REASON_SCHEDULER_SELECTED,
    AdviceLinkSummary,
    EvidenceQualitySummary,
    ExcludedPipelineSummary,
    KlineSlotObservationSource,
    OBSERVATION_STATUS_ADVICE_GENERATED,
    OBSERVATION_STATUS_EXPECTED_BLOCKED_BY_MODEL_CONFIG,
    OBSERVATION_STATUS_MISSING_PIPELINE,
    OBSERVATION_STATUS_MODEL_REVIEW_COMPLETED,
    OBSERVATION_STATUS_NOTIFICATION_PREPARED,
    OBSERVATION_STATUS_NOTIFICATION_SENT,
    OBSERVATION_STATUS_ONLY_CLI_RUNS,
    OBSERVATION_STATUS_PIPELINE_FAILED,
    OBSERVATION_STATUS_QUALITY_BLOCKED,
    OBSERVATION_STATUS_UNKNOWN,
    ObservationConfigSnapshot,
    PipelineRunCandidate,
    StrategyPipelineObservationBuildReport,
    StrategyPipelineObservationBuildRequest,
    StrategyPipelineObservationPayload,
    StrategyPipelineObservationResult,
    build_strategy_pipeline_observation_id,
)

EXPECTED_MODEL_BLOCK_ERROR_CODES = {
    "no_model_review_result",
    "real_model_disabled",
    "model_review_expired_but_real_model_disabled",
    "model_review_real_model_disabled",
    "model_review_scheduler_worker_disabled",
    "model_review_auto_run_disabled",
    "cli_real_model_cost_not_confirmed",
}

PIPELINE_FAILED_STATUSES = {"failed"}
PIPELINE_BLOCKED_STATUSES = {"blocked"}
QUALITY_FAILED_STATUSES = {"failed", "blocked"}
STATUS_PRIORITY = {
    OBSERVATION_STATUS_NOTIFICATION_SENT: 90,
    OBSERVATION_STATUS_NOTIFICATION_PREPARED: 80,
    OBSERVATION_STATUS_ADVICE_GENERATED: 70,
    OBSERVATION_STATUS_MODEL_REVIEW_COMPLETED: 60,
    OBSERVATION_STATUS_EXPECTED_BLOCKED_BY_MODEL_CONFIG: 50,
    OBSERVATION_STATUS_QUALITY_BLOCKED: 40,
    OBSERVATION_STATUS_PIPELINE_FAILED: 30,
    OBSERVATION_STATUS_UNKNOWN: 10,
}


class StrategyPipelineObservationService:
    """Build 26C observations from existing database rows only.

    参数：
    - `settings`：只读取非敏感开关，用于判断模型关闭是否为 expected blocked。
    - `repository`：MySQL repository，可在测试中注入 fake。

    返回值：service instance。
    失败场景：repository/database 异常向上抛出，由 CLI 映射为 exit_code=2。
    外部服务：不访问。
    数据影响：dry-run 不写库；confirm-write 时只写 observation 索引，不写
    pipeline、策略、模型、advice、正式 K线、Redis 或 Hermes。
    """

    def __init__(
        self,
        *,
        settings: AppSettings | Any | None = None,
        repository: StrategyPipelineObservationRepository | Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repository = repository or create_default_strategy_pipeline_observation_repository()

    def build_strategy_pipeline_observations(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineObservationBuildRequest,
    ) -> StrategyPipelineObservationBuildReport:
        """Build observations for requested slots without triggering pipeline stages."""

        config = build_observation_config_snapshot(self._settings)
        slots = self._repository.list_kline_slots(db_session, request=request)
        pipelines_by_slot = self._repository.list_pipeline_runs_for_slots(
            db_session,
            request=request,
            slots=slots,
        )
        all_pipeline_runs = tuple(run for runs in pipelines_by_slot.values() for run in runs)
        quality_by_pipeline = self._repository.load_evidence_quality_by_pipeline_run(
            db_session,
            pipeline_runs=all_pipeline_runs,
        )
        advice_by_pipeline = self._repository.load_advice_links_by_pipeline_run(
            db_session,
            pipeline_runs=all_pipeline_runs,
        )

        results: list[StrategyPipelineObservationResult] = []
        for slot in slots:
            payload, excluded_summary = self._build_payload_for_slot(
                request=request,
                config=config,
                slot=slot,
                pipeline_runs=pipelines_by_slot.get(slot.open_time_utc, ()),
                quality_by_pipeline=quality_by_pipeline,
                advice_by_pipeline=advice_by_pipeline,
            )
            database_action = "dry_run" if request.dry_run or not request.confirm_write else "pending"
            database_written = False
            if request.confirm_write and not request.dry_run:
                _, database_action = self._repository.upsert_observation(db_session, payload=payload)
                database_written = True
            results.append(
                StrategyPipelineObservationResult(
                    payload=payload,
                    database_action=database_action,
                    database_written=database_written,
                    excluded_reason_summary=excluded_summary,
                )
            )

        if request.confirm_write and not request.dry_run:
            _commit_if_possible(db_session)

        return StrategyPipelineObservationBuildReport(
            request=request,
            results=tuple(results),
            exit_code=0,
            dry_run=bool(request.dry_run),
            confirm_write=bool(request.confirm_write),
        )

    def _build_payload_for_slot(
        self,
        *,
        request: StrategyPipelineObservationBuildRequest,
        config: ObservationConfigSnapshot,
        slot: KlineSlotObservationSource,
        pipeline_runs: tuple[PipelineRunCandidate, ...],
        quality_by_pipeline: Mapping[str, EvidenceQualitySummary],
        advice_by_pipeline: Mapping[str, AdviceLinkSummary],
    ) -> tuple[StrategyPipelineObservationPayload, Mapping[str, int]]:
        """Classify one slot and return a compact persistence payload."""

        canonical, excluded = _select_canonical_pipeline(
            pipeline_runs=pipeline_runs,
            quality_by_pipeline=quality_by_pipeline,
            advice_by_pipeline=advice_by_pipeline,
            config=config,
        )
        excluded_summary = Counter(item.reason for item in excluded)
        duplicate_count = len(pipeline_runs) if len(pipeline_runs) > 1 else 0
        if canonical is None:
            canonical_reason = CANONICAL_REASON_ONLY_CLI_RUNS if pipeline_runs else CANONICAL_REASON_NO_PIPELINE
            observation_status = OBSERVATION_STATUS_ONLY_CLI_RUNS if pipeline_runs else OBSERVATION_STATUS_MISSING_PIPELINE
            return (
                _empty_payload_for_slot(
                    request=request,
                    slot=slot,
                    canonical_reason=canonical_reason,
                    observation_status=observation_status,
                    duplicate_pipeline_count=duplicate_count,
                    excluded=excluded,
                ),
                dict(excluded_summary),
            )

        quality = quality_by_pipeline.get(canonical.pipeline_run_id, EvidenceQualitySummary())
        advice = advice_by_pipeline.get(canonical.pipeline_run_id, AdviceLinkSummary())
        observation_status, eligible_for_advice, real_model_blocked = _classify_canonical_pipeline(
            pipeline=canonical,
            quality=quality,
            advice=advice,
            config=config,
        )
        advice_id = canonical.advice_id or advice.advice_id
        review_id = canonical.review_id or advice.review_id
        alert_message_id = quality.alert_message_id or advice.alert_message_id
        details = {
            "trace_id": request.trace_id,
            "source_pipeline_count": len(pipeline_runs),
            "scheduler_pipeline_count": sum(1 for run in pipeline_runs if run.trigger_source == TRIGGER_SOURCE_SCHEDULER),
            "cli_pipeline_count": sum(1 for run in pipeline_runs if run.trigger_source == TRIGGER_SOURCE_CLI),
            "excluded_reason_summary": dict(excluded_summary),
            "refresh_existing": bool(request.refresh_existing),
            "read_only_source": True,
            "model_called_by_26c": False,
            "hermes_sent_by_26c": False,
        }
        payload = StrategyPipelineObservationPayload(
            observation_id=build_strategy_pipeline_observation_id(
                symbol=request.symbol,
                base_interval=request.base_interval,
                higher_interval=request.higher_interval,
                kline_slot_utc=slot.open_time_utc,
            ),
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=slot.open_time_utc,
            kline_open_time_prc=slot.open_time_prc,
            kline_close_time_utc=slot.close_time_utc,
            kline_close_time_prc=slot.close_time_prc,
            canonical_pipeline_run_id=canonical.pipeline_run_id,
            canonical_trigger_source=canonical.trigger_source,
            canonical_reason=CANONICAL_REASON_SCHEDULER_SELECTED,
            duplicate_pipeline_count=duplicate_count,
            excluded_pipeline_run_ids=tuple(item.as_dict() for item in excluded),
            observation_status=observation_status,
            eligible_for_review=True,
            eligible_for_advice_performance_review=eligible_for_advice,
            pipeline_status=canonical.status,
            pipeline_current_step=canonical.current_step,
            pipeline_error_code=canonical.error_code,
            pipeline_error_message=canonical.error_message,
            strategy_signal_run_id=canonical.strategy_signal_run_id,
            strategy_evidence_aggregation_id=canonical.strategy_evidence_aggregation_id,
            evidence_quality_check_id=quality.quality_check_id,
            material_pack_id=canonical.material_pack_id,
            model_analysis_run_id=canonical.model_analysis_run_id,
            review_aggregation_run_id=canonical.review_aggregation_run_id,
            advice_id=advice_id,
            review_id=review_id,
            alert_message_id=alert_message_id,
            evidence_quality_status=quality.status,
            evidence_quality_should_block=quality.should_block_pipeline,
            evidence_quality_failed_roles=quality.failed_roles,
            evidence_quality_failed_strategies=quality.failed_strategies,
            model_review_invoked=canonical.model_review_invoked,
            model_review_reused=canonical.model_review_reused,
            real_model_called=canonical.real_model_called,
            real_model_blocked_by_config=real_model_blocked,
            hermes_real_sent=canonical.hermes_real_sent,
            notification_status=canonical.notification_status,
            details=details,
        )
        return payload, dict(excluded_summary)


def build_observation_config_snapshot(settings: AppSettings | Any) -> ObservationConfigSnapshot:
    """Build a non-sensitive config snapshot used only for status classification."""

    return ObservationConfigSnapshot(
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


def build_strategy_pipeline_observations(
    db_session: Any,
    *,
    request: StrategyPipelineObservationBuildRequest,
    service: StrategyPipelineObservationService | None = None,
) -> StrategyPipelineObservationBuildReport:
    """Convenience wrapper used by CLI and tests."""

    active_service = service or create_default_strategy_pipeline_observation_service()
    return active_service.build_strategy_pipeline_observations(db_session, request=request)


def create_default_strategy_pipeline_observation_service(
    *,
    settings: AppSettings | Any | None = None,
) -> StrategyPipelineObservationService:
    """Create the default 26C observation service."""

    return StrategyPipelineObservationService(settings=settings)


def _select_canonical_pipeline(
    *,
    pipeline_runs: tuple[PipelineRunCandidate, ...],
    quality_by_pipeline: Mapping[str, EvidenceQualitySummary],
    advice_by_pipeline: Mapping[str, AdviceLinkSummary],
    config: ObservationConfigSnapshot,
) -> tuple[PipelineRunCandidate | None, tuple[ExcludedPipelineSummary, ...]]:
    """Choose the canonical scheduler pipeline and compactly summarize exclusions."""

    scheduler_runs = tuple(run for run in pipeline_runs if run.trigger_source == TRIGGER_SOURCE_SCHEDULER)
    if not scheduler_runs:
        return None, tuple(
            ExcludedPipelineSummary(
                pipeline_run_id=run.pipeline_run_id,
                trigger_source=run.trigger_source,
                reason="cli_excluded_from_formal_sample",
            )
            for run in pipeline_runs
        )

    canonical = max(
        scheduler_runs,
        key=lambda run: _canonical_sort_key(
            run=run,
            quality=quality_by_pipeline.get(run.pipeline_run_id, EvidenceQualitySummary()),
            advice=advice_by_pipeline.get(run.pipeline_run_id, AdviceLinkSummary()),
            config=config,
        ),
    )
    excluded: list[ExcludedPipelineSummary] = []
    for run in pipeline_runs:
        if run.pipeline_run_id == canonical.pipeline_run_id:
            continue
        reason = (
            "cli_excluded_from_formal_sample"
            if run.trigger_source != TRIGGER_SOURCE_SCHEDULER
            else "superseded_by_canonical_scheduler_pipeline"
        )
        excluded.append(
            ExcludedPipelineSummary(
                pipeline_run_id=run.pipeline_run_id,
                trigger_source=run.trigger_source,
                reason=reason,
            )
        )
    return canonical, tuple(excluded)


def _canonical_sort_key(
    *,
    run: PipelineRunCandidate,
    quality: EvidenceQualitySummary,
    advice: AdviceLinkSummary,
    config: ObservationConfigSnapshot,
) -> tuple[int, datetime, int]:
    status, _, _ = _classify_canonical_pipeline(pipeline=run, quality=quality, advice=advice, config=config)
    return (
        int(STATUS_PRIORITY.get(status, 0)),
        run.created_at_utc or datetime.min.replace(tzinfo=UTC),
        int(run.id or 0),
    )


def _classify_canonical_pipeline(
    *,
    pipeline: PipelineRunCandidate,
    quality: EvidenceQualitySummary,
    advice: AdviceLinkSummary,
    config: ObservationConfigSnapshot,
) -> tuple[str, bool, bool]:
    """Return observation status, advice-performance eligibility, and model block flag."""

    if _quality_should_block(pipeline=pipeline, quality=quality):
        return OBSERVATION_STATUS_QUALITY_BLOCKED, False, False
    if _expected_model_config_block(pipeline=pipeline, config=config):
        return OBSERVATION_STATUS_EXPECTED_BLOCKED_BY_MODEL_CONFIG, False, True

    pipeline_status = (pipeline.status or "").strip().lower()
    if pipeline_status in PIPELINE_FAILED_STATUSES:
        return OBSERVATION_STATUS_PIPELINE_FAILED, False, False
    if pipeline_status in PIPELINE_BLOCKED_STATUSES:
        return OBSERVATION_STATUS_PIPELINE_FAILED, False, False

    advice_exists = bool(pipeline.advice_id or pipeline.review_id or advice.advice_id or advice.review_id)
    if advice_exists and pipeline.hermes_real_sent:
        return OBSERVATION_STATUS_NOTIFICATION_SENT, True, False
    if advice_exists and pipeline.notification_status:
        return OBSERVATION_STATUS_NOTIFICATION_PREPARED, True, False
    if advice_exists:
        return OBSERVATION_STATUS_ADVICE_GENERATED, True, False
    if pipeline.review_aggregation_run_id or pipeline.model_analysis_run_id or pipeline.model_review_invoked:
        return OBSERVATION_STATUS_MODEL_REVIEW_COMPLETED, False, False
    return OBSERVATION_STATUS_UNKNOWN, False, False


def _quality_should_block(*, pipeline: PipelineRunCandidate, quality: EvidenceQualitySummary) -> bool:
    quality_status = (quality.status or "").strip().lower()
    if quality.should_block_pipeline or quality_status in QUALITY_FAILED_STATUSES:
        return True
    error_code = (pipeline.error_code or "").strip()
    return (pipeline.current_step or "").strip() == PIPELINE_STEP_STAGE26B and error_code == "strategy_evidence_quality_failed"


def _expected_model_config_block(*, pipeline: PipelineRunCandidate, config: ObservationConfigSnapshot) -> bool:
    error_code = (pipeline.error_code or "").strip()
    stopped_at_model_step = (pipeline.current_step or "").strip() == PIPELINE_STEP_STAGE20
    return stopped_at_model_step and not config.real_model_allowed_for_pipeline and error_code in EXPECTED_MODEL_BLOCK_ERROR_CODES


def _empty_payload_for_slot(
    *,
    request: StrategyPipelineObservationBuildRequest,
    slot: KlineSlotObservationSource,
    canonical_reason: str,
    observation_status: str,
    duplicate_pipeline_count: int,
    excluded: tuple[ExcludedPipelineSummary, ...],
) -> StrategyPipelineObservationPayload:
    details = {
        "trace_id": request.trace_id,
        "source_pipeline_count": len(excluded),
        "scheduler_pipeline_count": 0,
        "cli_pipeline_count": len(excluded),
        "excluded_reason_summary": dict(Counter(item.reason for item in excluded)),
        "refresh_existing": bool(request.refresh_existing),
        "read_only_source": True,
        "model_called_by_26c": False,
        "hermes_sent_by_26c": False,
    }
    return StrategyPipelineObservationPayload(
        observation_id=build_strategy_pipeline_observation_id(
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=slot.open_time_utc,
        ),
        symbol=request.symbol,
        base_interval=request.base_interval,
        higher_interval=request.higher_interval,
        kline_slot_utc=slot.open_time_utc,
        kline_open_time_prc=slot.open_time_prc,
        kline_close_time_utc=slot.close_time_utc,
        kline_close_time_prc=slot.close_time_prc,
        canonical_pipeline_run_id=None,
        canonical_trigger_source=None,
        canonical_reason=canonical_reason,
        duplicate_pipeline_count=duplicate_pipeline_count,
        excluded_pipeline_run_ids=tuple(item.as_dict() for item in excluded),
        observation_status=observation_status,
        eligible_for_review=False,
        eligible_for_advice_performance_review=False,
        pipeline_status=None,
        pipeline_current_step=None,
        pipeline_error_code=None,
        pipeline_error_message=None,
        strategy_signal_run_id=None,
        strategy_evidence_aggregation_id=None,
        evidence_quality_check_id=None,
        material_pack_id=None,
        model_analysis_run_id=None,
        review_aggregation_run_id=None,
        advice_id=None,
        review_id=None,
        alert_message_id=None,
        evidence_quality_status=None,
        evidence_quality_should_block=False,
        evidence_quality_failed_roles=(),
        evidence_quality_failed_strategies=(),
        model_review_invoked=False,
        model_review_reused=False,
        real_model_called=False,
        real_model_blocked_by_config=False,
        hermes_real_sent=False,
        notification_status=None,
        details=details,
    )


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


__all__ = [
    "StrategyPipelineObservationService",
    "build_observation_config_snapshot",
    "build_strategy_pipeline_observations",
    "create_default_strategy_pipeline_observation_service",
]
