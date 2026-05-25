"""Repository for stage-22B manual execution confirmation intents.

This file belongs to `app/manual_execution/hermes_entry`. It reads and writes
only the 22B intent table through a caller-owned session. It never commits,
sends Hermes, reads Redis, requests Binance, calls large language models,
modifies Kline tables, or performs automatic trading.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.manual_execution.hermes_entry.intent_schema import ManualExecutionIntentPersistencePayload
from app.storage.mysql.models.manual_execution_intent import StrategyAdviceManualExecutionIntent

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - dependencies are managed by pyproject.
    select = None  # type: ignore[assignment]


class ManualExecutionIntentRepository:
    """Data access helper for stage-22B confirmation intents."""

    def get_intent_by_id(self, db_session: Any, *, intent_id: str) -> Any | None:
        """Return one confirmation intent row by MEI business id."""

        _require_sqlalchemy()
        stmt = (
            select(StrategyAdviceManualExecutionIntent)
            .where(StrategyAdviceManualExecutionIntent.intent_id == intent_id)
            .limit(1)
        )
        return db_session.execute(stmt).scalar_one_or_none()

    def create_intent(self, db_session: Any, *, payload: ManualExecutionIntentPersistencePayload) -> Any:
        """Insert one confirmation intent row without committing."""

        row = StrategyAdviceManualExecutionIntent(
            intent_id=payload.intent_id,
            status=payload.status,
            source_channel=payload.source_channel,
            source_message_id=payload.source_message_id,
            source_user_id=payload.source_user_id,
            raw_text=payload.raw_text,
            normalized_text=payload.normalized_text,
            parsed_action=payload.parsed_action,
            parsed_symbol=payload.parsed_symbol,
            parsed_side=payload.parsed_side,
            parsed_manual_position_id=payload.parsed_manual_position_id,
            parsed_advice_id=payload.parsed_advice_id,
            parsed_price=payload.parsed_price,
            parsed_notional_usdt=payload.parsed_notional_usdt,
            parsed_margin_usdt=payload.parsed_margin_usdt,
            parsed_reason=payload.parsed_reason,
            parsed_note=payload.parsed_note,
            parsed_payload_json=payload.parsed_payload_json,
            validation_status=payload.validation_status,
            validation_error_code=payload.validation_error_code,
            validation_error_message=payload.validation_error_message,
            missing_fields_json=payload.missing_fields_json,
            dry_run_snapshot_json=payload.dry_run_snapshot_json,
            expires_at_utc=payload.expires_at_utc,
            trace_id=payload.trace_id,
            created_at_utc=payload.created_at_utc,
            updated_at_utc=payload.updated_at_utc,
            is_manual=True,
            auto_trading_allowed=False,
        )
        db_session.add(row)
        _flush_if_possible(db_session)
        return row

    def mark_status(
        self,
        db_session: Any,
        row: Any,
        *,
        status: str,
        now_utc_value: datetime,
        validation_error_code: str | None = None,
        validation_error_message: str | None = None,
        executed_manual_position_id: str | None = None,
        executed_execution_id: str | None = None,
    ) -> Any:
        """Update one intent status without committing."""

        row.status = status
        row.updated_at_utc = now_utc_value
        if validation_error_code is not None:
            row.validation_error_code = validation_error_code
        if validation_error_message is not None:
            row.validation_error_message = validation_error_message
        if executed_manual_position_id is not None:
            row.executed_manual_position_id = executed_manual_position_id
        if executed_execution_id is not None:
            row.executed_execution_id = executed_execution_id
        timestamp_fields = {
            "confirmed": "confirmed_at_utc",
            "executed": "executed_at_utc",
            "cancelled": "cancelled_at_utc",
            "expired": "failed_at_utc",
            "execution_failed": "failed_at_utc",
            "failed": "failed_at_utc",
        }
        field_name = timestamp_fields.get(status)
        if field_name:
            setattr(row, field_name, now_utc_value)
        _flush_if_possible(db_session)
        return row


def create_default_manual_execution_intent_repository() -> ManualExecutionIntentRepository:
    """Create the default stage-22B intent repository."""

    return ManualExecutionIntentRepository()


def _require_sqlalchemy() -> None:
    if select is None:
        raise RuntimeError("SQLAlchemy is required for manual execution intent repository queries")


def _flush_if_possible(db_session: Any) -> None:
    flush = getattr(db_session, "flush", None)
    if callable(flush):
        flush()


__all__ = [
    "ManualExecutionIntentRepository",
    "create_default_manual_execution_intent_repository",
]
