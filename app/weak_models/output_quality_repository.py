"""Repository for 27B weak model output quality checks.

本文件属于 `app/weak_models` 模块，负责只读加载 27A 已落库的
`weak_model_run`、`weak_model_result`、`weak_model_aggregation`，并在
`--confirm-write` 时写入或更新 `weak_model_quality_check`。
本文件不运行弱模型，不修改原始 27A 输出，不请求 Binance，不读写 Redis，
不发送 Hermes，不调用 DeepSeek/GPT/Claude，不读取账户或仓位，不生成订单，
不自动交易。
主要被 `output_quality_service.py` 调用；所有数据库操作都使用调用方传入的
session。
"""

from __future__ import annotations

from typing import Any

from app.core.time_utils import ensure_utc_aware, now_utc
from app.storage.mysql.models.weak_model import (
    WeakModelAggregation,
    WeakModelQualityCheck,
    WeakModelResult,
    WeakModelRun,
)
from app.weak_models.output_quality_types import (
    WeakModelQualityPersistencePayload,
    WeakModelQualityTarget,
    json_dumps_compact,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are declared in pyproject.
    select = None  # type: ignore[assignment]


class WeakModelOutputQualityRepository:
    """Read persisted 27A weak-model rows and write compact 27B check rows."""

    def get_quality_target_by_run_id(self, db_session: Any, *, weak_model_run_id: str) -> WeakModelQualityTarget | None:
        """Return one 27A run package without mutating any data."""

        _require_sqlalchemy()
        run = self._get_run(db_session, weak_model_run_id=weak_model_run_id)
        if run is None:
            return None
        aggregation = self._get_aggregation_by_run_id(db_session, weak_model_run_id=weak_model_run_id)
        results = self._list_results_by_run_id(db_session, weak_model_run_id=weak_model_run_id)
        return WeakModelQualityTarget(run=run, aggregation=aggregation, results=results)

    def list_recent_quality_targets(
        self,
        db_session: Any,
        *,
        symbol: str,
        base_interval: str,
        higher_interval: str,
        limit: int,
    ) -> tuple[WeakModelQualityTarget, ...]:
        """Return recent persisted 27A runs for one market scope.

        This is read-only observation. It does not rerun weak models and does
        not create missing aggregation rows.
        """

        _require_sqlalchemy()
        stmt = (
            select(WeakModelRun)
            .where(WeakModelRun.symbol == symbol)
            .where(WeakModelRun.base_interval == base_interval)
            .where(WeakModelRun.higher_interval == higher_interval)
            .order_by(WeakModelRun.created_at_utc.desc(), WeakModelRun.id.desc())
            .limit(limit)
        )
        targets: list[WeakModelQualityTarget] = []
        for run in db_session.execute(stmt).scalars().all():
            target = self.get_quality_target_by_run_id(db_session, weak_model_run_id=run.weak_model_run_id)
            if target is not None:
                targets.append(target)
        return tuple(targets)

    def upsert_quality_check(
        self,
        db_session: Any,
        *,
        payload: WeakModelQualityPersistencePayload,
    ) -> tuple[Any, str]:
        """Insert or update one `weak_model_quality_check` row by run id."""

        _require_sqlalchemy()
        now = now_utc()
        row = self._get_quality_check_by_run_id(db_session, weak_model_run_id=payload.weak_model_run_id)
        action = "updated"
        if row is None:
            row = WeakModelQualityCheck(quality_check_id=payload.quality_check_id, created_at_utc=now)
            db_session.add(row)
            action = "created"

        row.quality_check_id = payload.quality_check_id
        row.weak_model_run_id = payload.weak_model_run_id
        row.weak_model_aggregation_id = payload.weak_model_aggregation_id
        row.strategy_signal_run_id = payload.strategy_signal_run_id
        row.snapshot_id = payload.snapshot_id
        row.symbol = payload.symbol
        row.base_interval = payload.base_interval
        row.higher_interval = payload.higher_interval
        row.kline_slot_utc = ensure_utc_aware(payload.kline_slot_utc) if payload.kline_slot_utc else None
        row.status = payload.status
        row.severity = payload.severity
        row.issue_count = int(payload.issue_count)
        row.warning_count = int(payload.warning_count)
        row.critical_count = int(payload.critical_count)
        row.should_block_pipeline = bool(payload.should_block_pipeline)
        row.issues_json = json_dumps_compact(tuple(payload.issues))
        row.checked_models_json = json_dumps_compact(tuple(payload.checked_models))
        row.summary_text = payload.summary_text
        row.trace_id = payload.trace_id
        row.updated_at_utc = now
        row.details_json = json_dumps_compact(dict(payload.details))
        _flush_if_possible(db_session)
        return row, action

    def _get_run(self, db_session: Any, *, weak_model_run_id: str) -> Any | None:
        stmt = select(WeakModelRun).where(WeakModelRun.weak_model_run_id == weak_model_run_id)
        return db_session.execute(stmt).scalars().first()

    def _get_aggregation_by_run_id(self, db_session: Any, *, weak_model_run_id: str) -> Any | None:
        stmt = select(WeakModelAggregation).where(WeakModelAggregation.weak_model_run_id == weak_model_run_id)
        return db_session.execute(stmt).scalars().first()

    def _list_results_by_run_id(self, db_session: Any, *, weak_model_run_id: str) -> tuple[Any, ...]:
        stmt = (
            select(WeakModelResult)
            .where(WeakModelResult.weak_model_run_id == weak_model_run_id)
            .order_by(WeakModelResult.id.asc())
        )
        return tuple(db_session.execute(stmt).scalars().all())

    def _get_quality_check_by_run_id(self, db_session: Any, *, weak_model_run_id: str) -> Any | None:
        stmt = select(WeakModelQualityCheck).where(WeakModelQualityCheck.weak_model_run_id == weak_model_run_id)
        return db_session.execute(stmt).scalars().first()


def create_default_weak_model_output_quality_repository() -> WeakModelOutputQualityRepository:
    """Create the default 27B repository."""

    return WeakModelOutputQualityRepository()


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for 27B weak model output quality repository.")


__all__ = [
    "WeakModelOutputQualityRepository",
    "create_default_weak_model_output_quality_repository",
]
