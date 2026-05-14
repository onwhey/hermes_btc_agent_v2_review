"""Backward-compatible scheduler slot names.

This file belongs to `app/scheduler`. Phase-12 originally exposed execution
slot helpers here. The active implementation now lives in
`app/scheduler/slot_state.py`, where running locks, completed markers, and
terminal status markers are separated. The compatibility class below preserves
the old import name only; it intentionally does not provide the old
`reserve_execution_slot` method because the old method cannot express separated
running/completed/status state. Callers must use
`RedisSchedulerSlotStore.acquire_slot_for_run()`. This file does not request
Binance, write MySQL, send Hermes, call DeepSeek, repair Klines, or perform
trading.
"""

from __future__ import annotations

from datetime import date, datetime

from app.scheduler.slot_state import (
    DAILY_KLINE_INTEGRITY_JOB_NAME,
    KLINE_4H_INCREMENTAL_JOB_NAME,
    RedisSchedulerSlotStore,
    build_daily_kline_integrity_slot_id,
    build_kline_4h_incremental_slot_id,
    build_scheduler_completed_key,
)


def build_kline_4h_incremental_slot_key(slot_time_utc: datetime) -> str:
    """Build the completed-marker key for a 09 scheduler slot."""

    slot = build_kline_4h_incremental_slot_id(slot_time_utc)
    return build_scheduler_completed_key(job=KLINE_4H_INCREMENTAL_JOB_NAME, slot=slot)


def build_daily_kline_integrity_slot_key(slot_date_utc: date) -> str:
    """Build the completed-marker key for an 11 scheduler slot."""

    slot = build_daily_kline_integrity_slot_id(slot_date_utc)
    return build_scheduler_completed_key(job=DAILY_KLINE_INTEGRITY_JOB_NAME, slot=slot)


class SchedulerExecutionSlotStore(RedisSchedulerSlotStore):
    """Compatibility import name for the new Redis scheduler slot store.

    This class keeps old imports from failing while forcing callers onto
    `acquire_slot_for_run()`. It deliberately has no old reservation method,
    because the previous single-key API would collapse running locks and
    completed markers back into an ambiguous state.
    """

__all__ = [
    "DAILY_KLINE_INTEGRITY_JOB_NAME",
    "KLINE_4H_INCREMENTAL_JOB_NAME",
    "SchedulerExecutionSlotStore",
    "build_daily_kline_integrity_slot_key",
    "build_kline_4h_incremental_slot_key",
]
