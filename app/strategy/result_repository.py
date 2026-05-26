"""Repository for stage-16 strategy signal persistence.

This file belongs to `app/strategy`. It writes only `strategy_signal_run` and
`strategy_signal_result` rows after the service has completed a strategy signal
run. It does not restore snapshots, query latest Klines, request Binance,
modify formal Kline tables, write Redis, send Hermes, call DeepSeek or any
large language model, read account/position state, generate final advice, or
perform trading.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from app.core.time_utils import now_utc
from app.storage.mysql.models.strategy_signal import StrategySignalResult, StrategySignalRun
from app.strategy.types import StrategyRunPersistencePayload, StrategySignalPersistencePayload


class StrategySignalResultRepository:
    """Persist strategy signal run/result rows without committing the session.

    Parameters: none.
    Return value: repository instance.
    Failure scenarios: database insert or uniqueness errors propagate to the
    service, which rolls back and returns a structured failed result.
    External service access: none.
    Data impact: writes only strategy signal tables and never writes formal
    Kline tables.
    """

    def create_strategy_signal_run(
        self,
        db_session: Any,
        payload: StrategyRunPersistencePayload,
    ) -> StrategySignalRun:
        """Insert one strategy signal run row and return the ORM object."""

        created_at_utc = now_utc()
        row = StrategySignalRun(
            run_id=payload.run_id,
            snapshot_id=payload.snapshot_id,
            symbol=payload.symbol,
            base_interval_value=payload.base_interval_value,
            higher_interval_value=payload.higher_interval_value,
            status=payload.status.value,
            trigger_source=payload.trigger_source,
            strategy_count=payload.strategy_count,
            success_count=payload.success_count,
            failed_count=payload.failed_count,
            invalid_count=payload.invalid_count,
            not_implemented_count=payload.not_implemented_count,
            blocked_reason=payload.blocked_reason,
            error_message=payload.error_message,
            trace_id=payload.trace_id,
            started_at_utc=payload.started_at_utc,
            finished_at_utc=payload.finished_at_utc,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def create_strategy_signal_results(
        self,
        db_session: Any,
        payloads: Iterable[StrategySignalPersistencePayload],
    ) -> tuple[StrategySignalResult, ...]:
        """Insert independent strategy result rows without Kline arrays."""

        rows: list[StrategySignalResult] = []
        created_at_utc = now_utc()
        for payload in payloads:
            signal = payload.signal
            row = StrategySignalResult(
                run_id=payload.run_id,
                snapshot_id=payload.snapshot_id,
                symbol=payload.symbol,
                base_interval_value=payload.base_interval_value,
                higher_interval_value=payload.higher_interval_value,
                strategy_name=signal.strategy_name,
                strategy_version=signal.strategy_version,
                strategy_status=signal.strategy_status.value,
                direction_bias=signal.direction_bias.value,
                risk_level=signal.risk_level.value,
                signal_strength=_normalize_signal_strength(signal.signal_strength),
                reason_codes_json=_json_dumps(tuple(signal.reason_codes)),
                reason_text=signal.reason_text,
                metrics_json=_json_dumps(signal.metrics),
                debug_json=_json_dumps(signal.debug_info),
                error_message=signal.error_message,
                contract_version=signal.contract_version,
                strategy_role=signal.strategy_role,
                common_payload_json=_json_dumps(signal.common_payload_json or {}),
                strategy_model_material_json=_json_dumps(signal.strategy_model_material_json or {}),
                strategy_payload_json=_json_dumps(signal.strategy_payload_json or {}),
                common_payload_hash=signal.common_payload_hash,
                validation_status=signal.validation_status,
                validation_errors_json=_json_dumps(tuple(signal.validation_errors_json or ())),
                trace_id=payload.trace_id,
                created_at_utc=created_at_utc,
                updated_at_utc=created_at_utc,
            )
            db_session.add(row)
            rows.append(row)
        _flush_if_possible(db_session)
        return tuple(rows)

    def create_strategy_signal_run_with_results(
        self,
        db_session: Any,
        *,
        run_payload: StrategyRunPersistencePayload,
        signal_payloads: Iterable[StrategySignalPersistencePayload],
    ) -> tuple[StrategySignalRun, tuple[StrategySignalResult, ...]]:
        """Insert one run row and its result rows in the caller transaction."""

        run_row = self.create_strategy_signal_run(db_session, run_payload)
        result_rows = self.create_strategy_signal_results(db_session, signal_payloads)
        return run_row, result_rows


def create_default_strategy_signal_result_repository() -> StrategySignalResultRepository:
    """Create the default strategy signal result repository."""

    return StrategySignalResultRepository()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _normalize_signal_strength(value: float) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("signal_strength must be numeric") from exc
    if decimal_value < Decimal("0"):
        decimal_value = Decimal("0")
    if decimal_value > Decimal("1"):
        decimal_value = Decimal("1")
    return decimal_value.quantize(Decimal("0.0001"))


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = [
    "StrategySignalResultRepository",
    "create_default_strategy_signal_result_repository",
]
