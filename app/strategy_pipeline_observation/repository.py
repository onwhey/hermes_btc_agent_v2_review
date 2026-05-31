"""Repository for 26C strategy pipeline observation index.

本文件属于 `app/strategy_pipeline_observation` 模块。
本文件负责 26C-A 所需的 MySQL 访问：读取已入库 4h K线 slot、读取已有
`strategy_pipeline_event_log`、读取已有 26B/advice/alert 摘要，并在调用方明确
confirm-write 时写入或更新 `strategy_pipeline_observation`。
本文件不负责 canonical 选择规则，不负责复盘分析，不请求 Binance，不发送
Hermes，不读写 Redis，不调用 DeepSeek 或其他大模型，不读取账户或仓位，
不生成订单，不自动交易。

主要调用方：
- `app/strategy_pipeline_observation/service.py::StrategyPipelineObservationService`

外部服务：不访问。
MySQL：按调用方传入 session 读写 observation；查询均为已有审计表。
Redis：不读写。
Hermes：不发送。
模型：不调用。
交易执行：不涉及。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from app.core.time_utils import ensure_utc_aware, now_utc
from app.storage.mysql.models.alert_message import AlertMessage
from app.storage.mysql.models.market_kline_4h import MarketKline4h
from app.storage.mysql.models.strategy_advice import StrategyAdvice, StrategyAdviceLifecycleReview
from app.storage.mysql.models.strategy_evidence_quality import StrategyEvidenceQualityCheckResult
from app.storage.mysql.models.strategy_pipeline import StrategyPipelineEventLog
from app.storage.mysql.models.strategy_pipeline_observation import StrategyPipelineObservation
from app.strategy_pipeline_observation.types import (
    AdviceLinkSummary,
    EvidenceQualitySummary,
    KlineSlotObservationSource,
    PipelineRunCandidate,
    StrategyPipelineObservationBuildRequest,
    StrategyPipelineObservationPayload,
    json_dumps_compact,
)

try:
    from sqlalchemy import or_, select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    or_ = select = None  # type: ignore[assignment]


class StrategyPipelineObservationRepository:
    """Read existing chain rows and persist compact 26C observation rows.

    参数：无。
    返回值：repository instance。
    失败场景：SQLAlchemy 或数据库异常向上抛出；CLI 映射为 exit_code=2。
    外部服务：不访问。
    数据影响：只在 `upsert_observation()` 中写 `strategy_pipeline_observation`；
    不 commit，不写正式 K线，不发送 Hermes，不调用模型，不交易。
    """

    def list_kline_slots(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineObservationBuildRequest,
    ) -> tuple[KlineSlotObservationSource, ...]:
        """Return formal 4h Kline slots that 26C is allowed to observe.

        26C-A 只读取 `market_kline_4h` 已入库 K线。它不计算理论应存在但缺失的
        slot，也不请求 Binance REST；K线漏采和连续性仍由 07/11 质量检查负责。
        """

        _require_sqlalchemy()
        stmt = (
            select(
                MarketKline4h.open_time_utc,
                MarketKline4h.open_time_prc,
                MarketKline4h.close_time_utc,
                MarketKline4h.close_time_prc,
            )
            .where(MarketKline4h.symbol == request.symbol)
            .where(MarketKline4h.interval_value == request.base_interval)
        )
        if request.kline_slot_utc is not None:
            stmt = stmt.where(MarketKline4h.open_time_utc == ensure_utc_aware(request.kline_slot_utc))
        else:
            stmt = stmt.order_by(MarketKline4h.open_time_utc.desc(), MarketKline4h.id.desc()).limit(request.limit)
        rows = db_session.execute(stmt).all()
        return tuple(
            KlineSlotObservationSource(
                open_time_utc=_require_utc(row[0]),
                open_time_prc=ensure_utc_aware(row[1]),
                close_time_utc=ensure_utc_aware(row[2]),
                close_time_prc=ensure_utc_aware(row[3]),
            )
            for row in rows
        )

    def list_pipeline_runs_for_slots(
        self,
        db_session: Any,
        *,
        request: StrategyPipelineObservationBuildRequest,
        slots: Iterable[KlineSlotObservationSource],
    ) -> dict[datetime, tuple[PipelineRunCandidate, ...]]:
        """Return all existing pipeline rows grouped by exact Kline slot."""

        _require_sqlalchemy()
        slot_values = tuple(_require_utc(slot.open_time_utc) for slot in slots)
        if not slot_values:
            return {}
        stmt = (
            select(StrategyPipelineEventLog)
            .where(StrategyPipelineEventLog.symbol == request.symbol)
            .where(StrategyPipelineEventLog.base_interval == request.base_interval)
            .where(StrategyPipelineEventLog.higher_interval == request.higher_interval)
            .where(StrategyPipelineEventLog.kline_slot_utc.in_(slot_values))
            .order_by(
                StrategyPipelineEventLog.kline_slot_utc.desc(),
                StrategyPipelineEventLog.created_at_utc.desc(),
                StrategyPipelineEventLog.id.desc(),
            )
        )
        grouped: dict[datetime, list[PipelineRunCandidate]] = defaultdict(list)
        for row in db_session.execute(stmt).scalars().all():
            slot = ensure_utc_aware(getattr(row, "kline_slot_utc", None))
            if slot is None:
                continue
            grouped[slot].append(_pipeline_candidate_from_row(row))
        return {slot: tuple(records) for slot, records in grouped.items()}

    def load_evidence_quality_by_pipeline_run(
        self,
        db_session: Any,
        *,
        pipeline_runs: Iterable[PipelineRunCandidate],
    ) -> dict[str, EvidenceQualitySummary]:
        """Return latest 26B quality summaries keyed by pipeline_run_id."""

        _require_sqlalchemy()
        pipeline_ids = _non_empty(run.pipeline_run_id for run in pipeline_runs)
        if not pipeline_ids:
            return {}
        stmt = (
            select(StrategyEvidenceQualityCheckResult)
            .where(StrategyEvidenceQualityCheckResult.pipeline_run_id.in_(pipeline_ids))
            .order_by(
                StrategyEvidenceQualityCheckResult.updated_at_utc.desc(),
                StrategyEvidenceQualityCheckResult.id.desc(),
            )
        )
        result: dict[str, EvidenceQualitySummary] = {}
        for row in db_session.execute(stmt).scalars().all():
            pipeline_run_id = _text_or_none(getattr(row, "pipeline_run_id", None))
            if not pipeline_run_id or pipeline_run_id in result:
                continue
            result[pipeline_run_id] = _evidence_quality_summary_from_row(row)
        return result

    def load_advice_links_by_pipeline_run(
        self,
        db_session: Any,
        *,
        pipeline_runs: Iterable[PipelineRunCandidate],
    ) -> dict[str, AdviceLinkSummary]:
        """Resolve stage-21 advice/review/alert ids without creating anything."""

        _require_sqlalchemy()
        records = tuple(pipeline_runs)
        if not records:
            return {}
        advice_ids = _non_empty(run.advice_id for run in records)
        review_ids = _non_empty(run.review_id for run in records)
        mrag_ids = _non_empty(run.review_aggregation_run_id for run in records)

        review_by_id, review_by_mrag = self._load_lifecycle_reviews(
            db_session,
            review_ids=review_ids,
            review_aggregation_run_ids=mrag_ids,
        )
        advice_by_id, advice_by_mrag = self._load_advice_rows(
            db_session,
            advice_ids=advice_ids | _non_empty(getattr(row, "result_advice_id", None) for row in review_by_id.values()),
            review_aggregation_run_ids=mrag_ids,
        )
        alert_by_review, alert_by_related_id = self._load_alert_rows(
            db_session,
            review_ids=review_ids | set(review_by_id.keys()),
            related_ids=advice_ids | set(advice_by_id.keys()),
        )

        result: dict[str, AdviceLinkSummary] = {}
        for record in records:
            review_row = review_by_id.get(record.review_id or "")
            if review_row is None and record.review_aggregation_run_id:
                review_row = review_by_mrag.get(record.review_aggregation_run_id)
            review_id = record.review_id or _text_or_none(getattr(review_row, "review_id", None))

            advice_row = advice_by_id.get(record.advice_id or "")
            result_advice_id = _text_or_none(getattr(review_row, "result_advice_id", None))
            if advice_row is None and result_advice_id:
                advice_row = advice_by_id.get(result_advice_id)
            if advice_row is None and record.review_aggregation_run_id:
                advice_row = advice_by_mrag.get(record.review_aggregation_run_id)
            advice_id = record.advice_id or result_advice_id or _text_or_none(getattr(advice_row, "advice_id", None))

            alert_id = None
            if review_id:
                alert_id = alert_by_review.get(review_id)
            if alert_id is None and advice_id:
                alert_id = alert_by_related_id.get(advice_id)
            result[record.pipeline_run_id] = AdviceLinkSummary(
                advice_id=advice_id,
                review_id=review_id,
                alert_message_id=alert_id,
            )
        return result

    def get_existing_observation(
        self,
        db_session: Any,
        *,
        payload: StrategyPipelineObservationPayload,
    ) -> Any | None:
        """Return one existing observation by the 26C idempotency scope."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyPipelineObservation)
            .where(StrategyPipelineObservation.symbol == payload.symbol)
            .where(StrategyPipelineObservation.base_interval == payload.base_interval)
            .where(StrategyPipelineObservation.higher_interval == payload.higher_interval)
            .where(StrategyPipelineObservation.kline_slot_utc == payload.kline_slot_utc)
        )
        return db_session.execute(stmt).scalars().first()

    def upsert_observation(
        self,
        db_session: Any,
        *,
        payload: StrategyPipelineObservationPayload,
    ) -> tuple[Any, str]:
        """Insert or update one compact observation row by slot scope.

        Idempotency key: `(symbol, base_interval, higher_interval,
        kline_slot_utc)`. Re-running 26C for the same slot updates the same row;
        duplicate pipeline candidates are recorded only in compact excluded
        JSON fields and never create duplicate observation rows.
        """

        _require_sqlalchemy()
        now = now_utc()
        row = self.get_existing_observation(db_session, payload=payload)
        action = "updated"
        if row is None:
            row = StrategyPipelineObservation(
                observation_id=payload.observation_id,
                created_at_utc=now,
            )
            db_session.add(row)
            action = "created"

        row.observation_id = payload.observation_id
        row.symbol = payload.symbol
        row.base_interval = payload.base_interval
        row.higher_interval = payload.higher_interval
        row.kline_slot_utc = payload.kline_slot_utc
        row.kline_open_time_prc = payload.kline_open_time_prc
        row.kline_close_time_utc = payload.kline_close_time_utc
        row.kline_close_time_prc = payload.kline_close_time_prc
        row.canonical_pipeline_run_id = payload.canonical_pipeline_run_id
        row.canonical_trigger_source = payload.canonical_trigger_source
        row.canonical_reason = payload.canonical_reason
        row.duplicate_pipeline_count = int(payload.duplicate_pipeline_count)
        row.excluded_pipeline_run_ids_json = json_dumps_compact(tuple(payload.excluded_pipeline_run_ids))
        row.observation_status = payload.observation_status
        row.eligible_for_review = bool(payload.eligible_for_review)
        row.eligible_for_advice_performance_review = bool(payload.eligible_for_advice_performance_review)
        row.pipeline_status = payload.pipeline_status
        row.pipeline_current_step = payload.pipeline_current_step
        row.pipeline_error_code = payload.pipeline_error_code
        row.pipeline_error_message = payload.pipeline_error_message
        row.strategy_signal_run_id = payload.strategy_signal_run_id
        row.strategy_evidence_aggregation_id = payload.strategy_evidence_aggregation_id
        row.evidence_quality_check_id = payload.evidence_quality_check_id
        row.material_pack_id = payload.material_pack_id
        row.model_analysis_run_id = payload.model_analysis_run_id
        row.review_aggregation_run_id = payload.review_aggregation_run_id
        row.advice_id = payload.advice_id
        row.review_id = payload.review_id
        row.alert_message_id = payload.alert_message_id
        row.evidence_quality_status = payload.evidence_quality_status
        row.evidence_quality_should_block = bool(payload.evidence_quality_should_block)
        row.evidence_quality_failed_roles_json = json_dumps_compact(tuple(payload.evidence_quality_failed_roles))
        row.evidence_quality_failed_strategies_json = json_dumps_compact(
            tuple(payload.evidence_quality_failed_strategies)
        )
        row.model_review_invoked = bool(payload.model_review_invoked)
        row.model_review_reused = bool(payload.model_review_reused)
        row.real_model_called = bool(payload.real_model_called)
        row.real_model_blocked_by_config = bool(payload.real_model_blocked_by_config)
        row.hermes_real_sent = bool(payload.hermes_real_sent)
        row.notification_status = payload.notification_status
        row.updated_at_utc = now
        row.details_json = json_dumps_compact(dict(payload.details))
        if hasattr(db_session, "flush"):
            db_session.flush()
        return row, action

    def _load_lifecycle_reviews(
        self,
        db_session: Any,
        *,
        review_ids: set[str],
        review_aggregation_run_ids: set[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        by_id: dict[str, Any] = {}
        by_mrag: dict[str, Any] = {}
        if review_ids:
            stmt = select(StrategyAdviceLifecycleReview).where(StrategyAdviceLifecycleReview.review_id.in_(review_ids))
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.review_id)] = row
        if review_aggregation_run_ids:
            stmt = (
                select(StrategyAdviceLifecycleReview)
                .where(StrategyAdviceLifecycleReview.source_review_aggregation_run_id.in_(review_aggregation_run_ids))
                .order_by(StrategyAdviceLifecycleReview.created_at_utc.desc(), StrategyAdviceLifecycleReview.id.desc())
            )
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.review_id)] = row
                by_mrag.setdefault(str(row.source_review_aggregation_run_id), row)
        return by_id, by_mrag

    def _load_advice_rows(
        self,
        db_session: Any,
        *,
        advice_ids: set[str],
        review_aggregation_run_ids: set[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        by_id: dict[str, Any] = {}
        by_mrag: dict[str, Any] = {}
        if advice_ids:
            stmt = select(StrategyAdvice).where(StrategyAdvice.advice_id.in_(advice_ids))
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.advice_id)] = row
        if review_aggregation_run_ids:
            stmt = (
                select(StrategyAdvice)
                .where(StrategyAdvice.source_review_aggregation_run_id.in_(review_aggregation_run_ids))
                .order_by(StrategyAdvice.created_at_utc.desc(), StrategyAdvice.id.desc())
            )
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.advice_id)] = row
                by_mrag.setdefault(str(row.source_review_aggregation_run_id), row)
        return by_id, by_mrag

    def _load_alert_rows(
        self,
        db_session: Any,
        *,
        review_ids: set[str],
        related_ids: set[str],
    ) -> tuple[dict[str, int], dict[str, int]]:
        by_review: dict[str, int] = {}
        by_related_id: dict[str, int] = {}
        clauses = []
        if review_ids:
            clauses.append(AlertMessage.related_review_id.in_(review_ids))
        if related_ids:
            clauses.append(AlertMessage.related_id.in_(related_ids))
        if not clauses:
            return by_review, by_related_id
        stmt = (
            select(AlertMessage)
            .where(or_(*clauses))
            .order_by(AlertMessage.created_at_utc.desc(), AlertMessage.id.desc())
        )
        for row in db_session.execute(stmt).scalars().all():
            review_id = _text_or_none(getattr(row, "related_review_id", None))
            related_id = _text_or_none(getattr(row, "related_id", None))
            row_id = _int_or_none(getattr(row, "id", None))
            if row_id is None:
                continue
            if review_id:
                by_review.setdefault(review_id, row_id)
            if related_id:
                by_related_id.setdefault(related_id, row_id)
        return by_review, by_related_id


def create_default_strategy_pipeline_observation_repository() -> StrategyPipelineObservationRepository:
    """Create the default 26C repository."""

    return StrategyPipelineObservationRepository()


def _pipeline_candidate_from_row(row: Any) -> PipelineRunCandidate:
    return PipelineRunCandidate(
        pipeline_run_id=str(getattr(row, "pipeline_run_id", "") or ""),
        symbol=str(getattr(row, "symbol", "") or ""),
        base_interval=str(getattr(row, "base_interval", "") or ""),
        higher_interval=str(getattr(row, "higher_interval", "") or ""),
        kline_slot_utc=ensure_utc_aware(getattr(row, "kline_slot_utc", None)),
        trigger_source=str(getattr(row, "trigger_source", "") or ""),
        status=str(getattr(row, "status", "") or ""),
        current_step=_text_or_none(getattr(row, "current_step", None)),
        strategy_signal_run_id=_text_or_none(getattr(row, "strategy_signal_run_id", None)),
        strategy_evidence_aggregation_id=_text_or_none(getattr(row, "strategy_evidence_aggregation_id", None)),
        material_pack_id=_text_or_none(getattr(row, "material_pack_id", None)),
        model_analysis_run_id=_text_or_none(getattr(row, "model_analysis_run_id", None)),
        review_aggregation_run_id=_text_or_none(getattr(row, "review_aggregation_run_id", None)),
        advice_id=_text_or_none(getattr(row, "advice_id", None)),
        review_id=_text_or_none(getattr(row, "review_id", None)),
        notification_status=_text_or_none(getattr(row, "notification_status", None)),
        model_review_invoked=bool(getattr(row, "model_review_invoked", False)),
        model_review_reused=bool(getattr(row, "model_review_reused", False)),
        real_model_called=bool(getattr(row, "real_model_called", False)),
        hermes_real_sent=bool(getattr(row, "hermes_real_sent", False)),
        error_code=_text_or_none(getattr(row, "error_code", None)),
        error_message=_text_or_none(getattr(row, "error_message", None)),
        created_at_utc=ensure_utc_aware(getattr(row, "created_at_utc", None)),
        updated_at_utc=ensure_utc_aware(getattr(row, "updated_at_utc", None)),
        id=_int_or_none(getattr(row, "id", None)),
    )


def _evidence_quality_summary_from_row(row: Any) -> EvidenceQualitySummary:
    failed_checks = _json_loads_list(getattr(row, "failed_checks_json", "[]"))
    return EvidenceQualitySummary(
        quality_check_id=_text_or_none(getattr(row, "quality_check_id", None)),
        status=_text_or_none(getattr(row, "status", None)),
        should_block_pipeline=bool(getattr(row, "should_block_pipeline", False)),
        failed_roles=_unique_non_empty(item.get("strategy_role") for item in failed_checks if isinstance(item, dict)),
        failed_strategies=_unique_non_empty(
            item.get("strategy_name") for item in failed_checks if isinstance(item, dict)
        ),
        alert_message_id=_int_or_none(getattr(row, "alert_message_id", None)),
    )


def _json_loads_list(value: Any) -> tuple[Any, ...]:
    if not value:
        return ()
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError):
        return ()
    if isinstance(loaded, list):
        return tuple(loaded)
    return ()


def _unique_non_empty(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text_or_none(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _non_empty(values: Iterable[str | None]) -> set[str]:
    return {value for value in (_text_or_none(value) for value in values) if value}


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _require_utc(value: datetime | None) -> datetime:
    utc_value = ensure_utc_aware(value)
    if utc_value is None:
        raise ValueError("UTC datetime is required for strategy pipeline observation")
    return utc_value


def _require_sqlalchemy() -> None:
    if select is None or or_ is None:
        raise RuntimeError("SQLAlchemy is required for 26C strategy pipeline observation repository.")


__all__ = [
    "StrategyPipelineObservationRepository",
    "create_default_strategy_pipeline_observation_repository",
]
