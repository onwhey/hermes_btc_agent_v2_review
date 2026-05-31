"""Repository for 26B strategy evidence quality gate.

本文件属于 `app/strategy/evidence_quality` 模块。
本文件负责 26B 所需的 MySQL 读写：读取 SSR、SEA、public strategy result，
写入或更新 `strategy_evidence_quality_check_result`，以及只读查询已有质量结果。
本文件不负责质量判定，不负责 pipeline 编排，不负责发送 Hermes，不请求 Binance，
不读写 Redis，不调用 DeepSeek 或其他大模型，不读取账户或仓位，不生成订单，
不自动交易。
主要被 `service.py` 和 `scripts/check_strategy_evidence_quality.py` 调用。
外部服务：无。MySQL：按调用方传入 session 读写。Redis：无。Hermes：无。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.time_utils import ensure_utc_aware, now_utc
from app.storage.mysql.models.strategy_aggregation import StrategyEvidenceAggregationResult
from app.storage.mysql.models.strategy_evidence_quality import StrategyEvidenceQualityCheckResult
from app.storage.mysql.models.strategy_signal import StrategySignalResult, StrategySignalRun
from app.strategy.evidence_quality.types import (
    StrategyEvidenceQualityPersistencePayload,
    StrategyEvidenceQualityQueryRequest,
    StrategyEvidenceQualityRowSummary,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class StrategyEvidenceQualityRepository:
    """Read/write repository for compact 26B quality gate audit rows.

    Parameters: none.
    Return value: repository instance.
    Failure scenarios: SQLAlchemy/database exceptions propagate to the service;
    the pipeline converts them to structured failure only at orchestration
    boundaries.
    External services: none.
    Data impact: reads strategy evidence rows and writes only 26B quality rows;
    it does not write formal Kline tables, strategy algorithms, material packs,
    model review rows, advice rows, Redis keys, or Hermes messages.
    """

    def get_strategy_signal_run(self, db_session: Any, *, run_id: str) -> Any | None:
        """Return one `strategy_signal_run` by id without mutating data."""

        _require_sqlalchemy()
        stmt = select(StrategySignalRun).where(StrategySignalRun.run_id == run_id)
        return db_session.execute(stmt).scalars().first()

    def get_strategy_evidence_aggregation(self, db_session: Any, *, aggregation_id: str) -> Any | None:
        """Return one `strategy_evidence_aggregation_result` by business id."""

        _require_sqlalchemy()
        stmt = select(StrategyEvidenceAggregationResult).where(
            StrategyEvidenceAggregationResult.aggregation_id == aggregation_id
        )
        return db_session.execute(stmt).scalars().first()

    def list_strategy_signal_results(self, db_session: Any, *, run_id: str) -> tuple[Any, ...]:
        """Return all public strategy result rows for one SSR.

        The service reads only public metadata and `common_payload_json`; it
        never reads private `strategy_payload_json` or model material fields.
        """

        _require_sqlalchemy()
        stmt = (
            select(StrategySignalResult)
            .where(StrategySignalResult.run_id == run_id)
            .order_by(StrategySignalResult.id.asc())
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def get_existing_quality_check(
        self,
        db_session: Any,
        *,
        pipeline_run_id: str | None,
        evidence_aggregation_id: str | None = None,
        trigger_source: str,
    ) -> Any | None:
        """Return an existing 26B row for idempotent retries.

        Pipeline runs are the primary idempotency boundary. The SEA fallback is
        retained only for non-pipeline callers that do not have a pipeline id.
        """

        _require_sqlalchemy()
        stmt = select(StrategyEvidenceQualityCheckResult).where(
            StrategyEvidenceQualityCheckResult.trigger_source == trigger_source
        )
        if pipeline_run_id:
            stmt = stmt.where(StrategyEvidenceQualityCheckResult.pipeline_run_id == pipeline_run_id)
        elif evidence_aggregation_id:
            stmt = stmt.where(StrategyEvidenceQualityCheckResult.evidence_aggregation_id == evidence_aggregation_id)
        else:
            return None
        return db_session.execute(stmt).scalars().first()

    def upsert_quality_check_result(
        self,
        db_session: Any,
        *,
        payload: StrategyEvidenceQualityPersistencePayload,
    ) -> tuple[Any, str]:
        """Insert or update a compact 26B quality result row.

        Idempotency key: `(pipeline_run_id, trigger_source)` for pipeline
        checks. This keeps different pipeline runs independent even when they
        reuse the same SEA, while allowing a repeated pipeline run to update the
        same quality row.
        """

        _require_sqlalchemy()
        now = now_utc()
        row = self.get_existing_quality_check(
            db_session,
            pipeline_run_id=payload.pipeline_run_id,
            evidence_aggregation_id=payload.evidence_aggregation_id,
            trigger_source=payload.trigger_source,
        )
        action = "updated"
        if row is None:
            row = StrategyEvidenceQualityCheckResult(
                quality_check_id=payload.quality_check_id,
                created_at_utc=now,
            )
            db_session.add(row)
            action = "created"

        row.quality_check_id = payload.quality_check_id
        row.pipeline_run_id = payload.pipeline_run_id
        row.strategy_signal_run_id = payload.strategy_signal_run_id
        row.evidence_aggregation_id = payload.evidence_aggregation_id
        row.symbol = payload.symbol
        row.base_interval = payload.base_interval
        row.higher_interval = payload.higher_interval
        row.kline_slot_utc = ensure_utc_aware(payload.kline_slot_utc)
        row.status = payload.status
        row.severity = payload.severity
        row.should_block_pipeline = bool(payload.should_block_pipeline)
        row.error_code = payload.error_code
        row.error_message = payload.error_message
        row.failed_checks_json = _json_dumps(payload.failed_checks)
        row.warning_checks_json = _json_dumps(payload.warning_checks)
        row.strategy_quality_json = _json_dumps(payload.strategy_quality)
        row.role_quality_json = _json_dumps(payload.role_quality)
        row.config_snapshot_json = _json_dumps(payload.config_snapshot)
        row.alert_required = bool(payload.alert_required)
        row.alert_status = payload.alert_status
        row.alert_message_id = payload.alert_message_id
        row.not_trading_advice = bool(payload.not_trading_advice)
        row.trigger_source = payload.trigger_source
        row.trace_id = payload.trace_id
        row.updated_at_utc = now
        if hasattr(db_session, "flush"):
            db_session.flush()
        return row, action

    def update_quality_alert_status(
        self,
        db_session: Any,
        *,
        quality_check_id: str,
        alert_status: str,
        alert_message_id: int | None = None,
    ) -> Any | None:
        """Update only Hermes alert status fields on a 26B quality row."""

        _require_sqlalchemy()
        stmt = select(StrategyEvidenceQualityCheckResult).where(
            StrategyEvidenceQualityCheckResult.quality_check_id == quality_check_id
        )
        row = db_session.execute(stmt).scalars().first()
        if row is None:
            return None
        row.alert_status = alert_status
        row.alert_message_id = alert_message_id
        row.updated_at_utc = now_utc()
        if hasattr(db_session, "flush"):
            db_session.flush()
        return row

    def list_quality_check_results(
        self,
        db_session: Any,
        *,
        request: StrategyEvidenceQualityQueryRequest,
    ) -> tuple[StrategyEvidenceQualityRowSummary, ...]:
        """Read existing 26B quality rows for the auxiliary CLI only."""

        _require_sqlalchemy()
        stmt = select(StrategyEvidenceQualityCheckResult)
        if request.evidence_aggregation_id:
            stmt = stmt.where(
                StrategyEvidenceQualityCheckResult.evidence_aggregation_id == request.evidence_aggregation_id
            )
        else:
            stmt = (
                stmt.where(StrategyEvidenceQualityCheckResult.symbol == request.symbol)
                .where(StrategyEvidenceQualityCheckResult.base_interval == request.base_interval)
                .where(StrategyEvidenceQualityCheckResult.higher_interval == request.higher_interval)
            )
        stmt = stmt.order_by(
            StrategyEvidenceQualityCheckResult.created_at_utc.desc(),
            StrategyEvidenceQualityCheckResult.id.desc(),
        ).limit(request.limit)
        return tuple(_row_summary_from_model(row) for row in db_session.execute(stmt).scalars().all())


def create_default_strategy_evidence_quality_repository() -> StrategyEvidenceQualityRepository:
    """Create the default 26B repository."""

    return StrategyEvidenceQualityRepository()


def _row_summary_from_model(row: Any) -> StrategyEvidenceQualityRowSummary:
    return StrategyEvidenceQualityRowSummary(
        quality_check_id=str(getattr(row, "quality_check_id", "") or ""),
        pipeline_run_id=_text_or_none(getattr(row, "pipeline_run_id", None)),
        strategy_signal_run_id=str(getattr(row, "strategy_signal_run_id", "") or ""),
        evidence_aggregation_id=str(getattr(row, "evidence_aggregation_id", "") or ""),
        symbol=str(getattr(row, "symbol", "") or ""),
        base_interval=str(getattr(row, "base_interval", "") or ""),
        higher_interval=str(getattr(row, "higher_interval", "") or ""),
        kline_slot_utc=ensure_utc_aware(getattr(row, "kline_slot_utc", None)),
        status=str(getattr(row, "status", "") or ""),
        severity=str(getattr(row, "severity", "") or ""),
        should_block_pipeline=bool(getattr(row, "should_block_pipeline", False)),
        error_code=_text_or_none(getattr(row, "error_code", None)),
        error_message=_text_or_none(getattr(row, "error_message", None)),
        alert_status=_text_or_none(getattr(row, "alert_status", None)),
        alert_message_id=_int_or_none(getattr(row, "alert_message_id", None)),
        trace_id=str(getattr(row, "trace_id", "") or ""),
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for 26B strategy evidence quality repository.")


__all__ = [
    "StrategyEvidenceQualityRepository",
    "create_default_strategy_evidence_quality_repository",
]
