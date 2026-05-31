"""Read-only repository for strategy pipeline observability.

本文件属于 `app/strategy_observability` 模块，负责 26A 策略链路运行观测
所需的只读 MySQL 查询。
本文件不负责状态判定，不负责调用 25 pipeline，不负责调用真实模型，不发送 Hermes，
不读写 Redis，不修改 18/19/20/21/25 核心逻辑，不读取账户或持仓，不涉及交易执行。

主要调用方：
- `app/strategy_observability/service.py::StrategyPipelineObservabilityService`

外部服务：不访问。
MySQL：只读查询 `market_kline_4h`、`strategy_pipeline_event_log` 以及链路表。
Redis：不读写。
Hermes：不发送。
DeepSeek/其他大模型：不调用。
交易执行：不涉及。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from app.core.time_utils import ensure_utc_aware
from app.market_data.kline_constants import KLINE_4H_INTERVAL_VALUE
from app.storage.mysql.models.market_kline_4h import MarketKline4h
from app.storage.mysql.models.model_review_aggregation import ModelReviewAggregationRun
from app.storage.mysql.models.strategy_advice import StrategyAdviceLifecycleReview
from app.storage.mysql.models.strategy_aggregation import AnalysisMaterialPack, StrategyEvidenceAggregationResult
from app.storage.mysql.models.strategy_pipeline import StrategyPipelineEventLog
from app.storage.mysql.models.strategy_signal import StrategySignalRun
from app.strategy_observability.types import (
    KlineSlotRecord,
    StrategyPipelineLinkRecord,
    StrategyPipelineRunRecord,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class StrategyPipelineObservabilityRepository:
    """Repository that only reads the existing strategy pipeline chain tables.

    参数：无。
    返回值：repository instance。
    失败场景：SQLAlchemy 或数据库异常向上抛出，由 CLI/service 转为 exit_code=2。
    外部服务：不访问。
    数据影响：只读 MySQL；不 commit，不写 Redis，不发送 Hermes。
    """

    def list_recent_closed_kline_slots(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        limit: int,
    ) -> tuple[KlineSlotRecord, ...]:
        """Return recent formal closed 4h Kline slots in newest-first order.

        26A 必须按最近 N 根已收盘 4h K线 slot 观测链路，而不是只查最近一条
        pipeline。本方法只读取正式 4h K线表；该表按项目铁律只保存已收盘 K线。
        """

        _require_sqlalchemy()
        if base_interval != KLINE_4H_INTERVAL_VALUE:
            return ()
        stmt = (
            select(MarketKline4h.open_time_utc, MarketKline4h.open_time_ms)
            .where(MarketKline4h.symbol == symbol)
            .where(MarketKline4h.interval_value == base_interval)
            .order_by(MarketKline4h.open_time_utc.desc(), MarketKline4h.id.desc())
            .limit(limit)
        )
        rows = db_session.execute(stmt).all()
        return tuple(
            KlineSlotRecord(open_time_utc=_require_utc(row[0]), open_time_ms=int(row[1]) if row[1] is not None else None)
            for row in rows
        )

    def list_pipeline_runs_for_slots(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        slots: Iterable[KlineSlotRecord],
    ) -> dict[datetime, tuple[StrategyPipelineRunRecord, ...]]:
        """Return pipeline rows grouped by exact base Kline slot.

        This method deliberately returns every pipeline row for a slot so the
        service can identify `duplicate` instead of hiding repeated runs.
        """

        _require_sqlalchemy()
        slot_values = tuple(_require_utc(slot.open_time_utc) for slot in slots)
        if not slot_values:
            return {}
        stmt = (
            select(StrategyPipelineEventLog)
            .where(StrategyPipelineEventLog.symbol == symbol)
            .where(StrategyPipelineEventLog.base_interval == base_interval)
            .where(StrategyPipelineEventLog.higher_interval == higher_interval)
            .where(StrategyPipelineEventLog.kline_slot_utc.in_(slot_values))
            .order_by(
                StrategyPipelineEventLog.kline_slot_utc.desc(),
                StrategyPipelineEventLog.created_at_utc.desc(),
                StrategyPipelineEventLog.id.desc(),
            )
        )
        grouped: dict[datetime, list[StrategyPipelineRunRecord]] = defaultdict(list)
        for row in db_session.execute(stmt).scalars().all():
            slot = ensure_utc_aware(getattr(row, "kline_slot_utc", None))
            if slot is None:
                continue
            record = _pipeline_run_record_from_row(row)
            grouped[slot].append(record)
        return {slot: tuple(records) for slot, records in grouped.items()}

    def load_link_records_for_pipeline_runs(
        self,
        db_session: Any,
        *,
        pipeline_runs: Iterable[StrategyPipelineRunRecord],
    ) -> dict[str, StrategyPipelineLinkRecord]:
        """Resolve SP/SSR/SEA/AMP/MRAG/ADVR ids without mutating any table.

        The pipeline event row is the primary audit source. When an event row
        lacks a downstream id, this method attempts a conservative lookup from
        the previous id, such as SEA by SSR or ADVR by MRAG. It never creates
        missing chain rows and never treats this lookup as a repair.
        """

        _require_sqlalchemy()
        records = tuple(pipeline_runs)
        if not records:
            return {}

        ssr_ids = _non_empty(record.strategy_signal_run_id for record in records)
        sea_ids = _non_empty(record.strategy_evidence_aggregation_id for record in records)
        amp_ids = _non_empty(record.material_pack_id for record in records)
        mrag_ids = _non_empty(record.review_aggregation_run_id for record in records)
        advr_ids = _non_empty(record.review_id for record in records)

        ssr_exists = set(_select_scalar_values(db_session, StrategySignalRun.run_id, ssr_ids))
        sea_by_id, sea_by_ssr = self._load_strategy_evidence_rows(
            db_session,
            aggregation_ids=sea_ids,
            strategy_signal_run_ids=ssr_ids,
        )
        amp_by_id, amp_by_ssr = self._load_material_pack_rows(
            db_session,
            material_pack_ids=amp_ids,
            strategy_signal_run_ids=ssr_ids,
        )
        mrag_by_id, mrag_by_amp = self._load_review_aggregation_rows(
            db_session,
            review_aggregation_run_ids=mrag_ids,
            material_pack_ids=amp_ids | set(amp_by_id.keys()),
        )
        advr_by_id, advr_by_mrag = self._load_advice_review_rows(
            db_session,
            review_ids=advr_ids,
            review_aggregation_run_ids=mrag_ids | set(mrag_by_id.keys()),
        )

        result: dict[str, StrategyPipelineLinkRecord] = {}
        for record in records:
            ssr_id = record.strategy_signal_run_id
            sea_row = sea_by_id.get(record.strategy_evidence_aggregation_id or "")
            if sea_row is None and ssr_id:
                sea_row = sea_by_ssr.get(ssr_id)
            sea_id = record.strategy_evidence_aggregation_id or _text_or_none(getattr(sea_row, "aggregation_id", None))

            amp_row = amp_by_id.get(record.material_pack_id or "")
            if amp_row is None and ssr_id:
                amp_row = amp_by_ssr.get(ssr_id)
            amp_id = record.material_pack_id or _text_or_none(getattr(amp_row, "material_pack_id", None))

            mrag_row = mrag_by_id.get(record.review_aggregation_run_id or "")
            if mrag_row is None and amp_id:
                mrag_row = mrag_by_amp.get(amp_id)
            mrag_id = record.review_aggregation_run_id or _text_or_none(
                getattr(mrag_row, "review_aggregation_run_id", None)
            )

            advr_row = advr_by_id.get(record.review_id or "")
            if advr_row is None and mrag_id:
                advr_row = advr_by_mrag.get(mrag_id)
            advr_id = record.review_id or _text_or_none(getattr(advr_row, "review_id", None))

            result[record.pipeline_run_id] = StrategyPipelineLinkRecord(
                pipeline_run_id=record.pipeline_run_id,
                strategy_signal_run_id=ssr_id,
                strategy_signal_run_exists=bool(ssr_id and ssr_id in ssr_exists),
                strategy_evidence_aggregation_id=sea_id,
                strategy_evidence_aggregation_exists=bool(sea_id and sea_id in sea_by_id),
                material_pack_id=amp_id,
                material_pack_exists=bool(amp_id and amp_id in amp_by_id),
                review_aggregation_run_id=mrag_id,
                review_aggregation_run_exists=bool(mrag_id and mrag_id in mrag_by_id),
                advice_lifecycle_review_id=advr_id,
                advice_lifecycle_review_exists=bool(advr_id and advr_id in advr_by_id),
            )
        return result

    def _load_strategy_evidence_rows(
        self,
        db_session: Any,
        *,
        aggregation_ids: set[str],
        strategy_signal_run_ids: set[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        by_id: dict[str, Any] = {}
        by_ssr: dict[str, Any] = {}
        if aggregation_ids:
            stmt = select(StrategyEvidenceAggregationResult).where(
                StrategyEvidenceAggregationResult.aggregation_id.in_(aggregation_ids)
            )
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.aggregation_id)] = row
        if strategy_signal_run_ids:
            stmt = select(StrategyEvidenceAggregationResult).where(
                StrategyEvidenceAggregationResult.strategy_signal_run_id.in_(strategy_signal_run_ids)
            )
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.aggregation_id)] = row
                by_ssr[str(row.strategy_signal_run_id)] = row
        return by_id, by_ssr

    def _load_material_pack_rows(
        self,
        db_session: Any,
        *,
        material_pack_ids: set[str],
        strategy_signal_run_ids: set[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        by_id: dict[str, Any] = {}
        by_ssr: dict[str, Any] = {}
        if material_pack_ids:
            stmt = select(AnalysisMaterialPack).where(AnalysisMaterialPack.material_pack_id.in_(material_pack_ids))
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.material_pack_id)] = row
        if strategy_signal_run_ids:
            stmt = (
                select(AnalysisMaterialPack)
                .where(AnalysisMaterialPack.strategy_signal_run_id.in_(strategy_signal_run_ids))
                .order_by(AnalysisMaterialPack.created_at_utc.desc(), AnalysisMaterialPack.id.desc())
            )
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.material_pack_id)] = row
                by_ssr.setdefault(str(row.strategy_signal_run_id), row)
        return by_id, by_ssr

    def _load_review_aggregation_rows(
        self,
        db_session: Any,
        *,
        review_aggregation_run_ids: set[str],
        material_pack_ids: set[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        by_id: dict[str, Any] = {}
        by_amp: dict[str, Any] = {}
        if review_aggregation_run_ids:
            stmt = select(ModelReviewAggregationRun).where(
                ModelReviewAggregationRun.review_aggregation_run_id.in_(review_aggregation_run_ids)
            )
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.review_aggregation_run_id)] = row
        if material_pack_ids:
            stmt = (
                select(ModelReviewAggregationRun)
                .where(ModelReviewAggregationRun.material_pack_id.in_(material_pack_ids))
                .order_by(ModelReviewAggregationRun.created_at_utc.desc(), ModelReviewAggregationRun.id.desc())
            )
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.review_aggregation_run_id)] = row
                by_amp.setdefault(str(row.material_pack_id), row)
        return by_id, by_amp

    def _load_advice_review_rows(
        self,
        db_session: Any,
        *,
        review_ids: set[str],
        review_aggregation_run_ids: set[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        by_id: dict[str, Any] = {}
        by_mrag: dict[str, Any] = {}
        if review_ids:
            stmt = select(StrategyAdviceLifecycleReview).where(
                StrategyAdviceLifecycleReview.review_id.in_(review_ids)
            )
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.review_id)] = row
        if review_aggregation_run_ids:
            stmt = select(StrategyAdviceLifecycleReview).where(
                StrategyAdviceLifecycleReview.source_review_aggregation_run_id.in_(review_aggregation_run_ids)
            )
            for row in db_session.execute(stmt).scalars().all():
                by_id[str(row.review_id)] = row
                by_mrag[str(row.source_review_aggregation_run_id)] = row
        return by_id, by_mrag


def create_default_strategy_pipeline_observability_repository() -> StrategyPipelineObservabilityRepository:
    """Create the default 26A read-only repository."""

    return StrategyPipelineObservabilityRepository()


def _pipeline_run_record_from_row(row: Any) -> StrategyPipelineRunRecord:
    return StrategyPipelineRunRecord(
        pipeline_run_id=str(row.pipeline_run_id),
        symbol=str(row.symbol),
        base_interval=str(row.base_interval),
        higher_interval=str(row.higher_interval),
        kline_slot_utc=ensure_utc_aware(row.kline_slot_utc),
        status=str(row.status),
        current_step=_text_or_none(row.current_step),
        strategy_signal_run_id=_text_or_none(row.strategy_signal_run_id),
        strategy_evidence_aggregation_id=_text_or_none(row.strategy_evidence_aggregation_id),
        material_pack_id=_text_or_none(row.material_pack_id),
        review_aggregation_run_id=_text_or_none(row.review_aggregation_run_id),
        advice_id=_text_or_none(row.advice_id),
        review_id=_text_or_none(row.review_id),
        notification_status=_text_or_none(row.notification_status),
        real_model_called=bool(row.real_model_called),
        hermes_real_sent=bool(row.hermes_real_sent),
        error_code=_text_or_none(row.error_code),
        error_message=_text_or_none(row.error_message),
        created_at_utc=ensure_utc_aware(row.created_at_utc),
        id=int(row.id) if row.id is not None else None,
    )


def _select_scalar_values(db_session: Any, column: Any, values: set[str]) -> tuple[str, ...]:
    if not values:
        return ()
    stmt = select(column).where(column.in_(values))
    return tuple(str(value) for value in db_session.execute(stmt).scalars().all())


def _non_empty(values: Iterable[str | None]) -> set[str]:
    return {value for value in (_text_or_none(value) for value in values) if value}


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_utc(value: datetime | None) -> datetime:
    utc_value = ensure_utc_aware(value)
    if utc_value is None:
        raise ValueError("UTC datetime is required for strategy pipeline observability")
    return utc_value


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for strategy pipeline observability repository queries")


__all__ = [
    "StrategyPipelineObservabilityRepository",
    "create_default_strategy_pipeline_observability_repository",
]
