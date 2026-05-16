"""Scheduler slot state and Redis-backed lock storage.

This file belongs to `app/scheduler`. It separates scheduler slot running
locks from completed markers so one Redis key no longer means both "currently
running" and "already handled". It writes only scheduler diagnostic keys in
Redis. It does not call scripts, request Binance, read or write MySQL, send
Hermes, call DeepSeek, repair Klines, generate advice, or perform trading.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from app.core.exceptions import RedisError
from app.core.time_utils import UTC, now_utc
from app.storage.redis.client import get_redis_client

KLINE_4H_INCREMENTAL_JOB_NAME = "kline_4h_incremental"
DAILY_KLINE_INTEGRITY_JOB_NAME = "daily_kline_integrity"
KLINE_1D_INCREMENTAL_JOB_NAME = "kline_1d_incremental"
KLINE_1D_INTEGRITY_JOB_NAME = "kline_1d_integrity_check"

_DELETE_KEY_IF_VALUE_MATCHES = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""


class SchedulerSlotStatus(str, Enum):
    """Status vocabulary for scheduler slot state records."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    STALE = "stale"
    EXPIRED = "expired"


class SchedulerSlotAction(str, Enum):
    """Runner action decided from Redis slot state."""

    ACQUIRED = "acquired"
    ACQUIRED_AFTER_STALE = "acquired_after_stale"
    SKIP = "skip"


@dataclass(frozen=True)
class SchedulerSlotLock:
    """Diagnostic value stored in a running lock.

    Parameters identify one job and UTC slot, the process owner, a non-secret
    random token used only for compare-and-release, and the TTL that should make
    the lock self-cleaning. The value is JSON so operators can inspect Redis
    without guessing what a token means. The lock does not imply a formal Kline
    write; 09 still owns its own Kline write lock and quality checks.
    """

    job: str
    slot: str
    status: SchedulerSlotStatus
    owner: str
    token: str
    created_at_utc: str
    updated_at_utc: str
    ttl_seconds: int

    def to_json(self) -> str:
        """Serialize the running lock to stable JSON for Redis."""

        return _json_dumps(
            {
                "job": self.job,
                "slot": self.slot,
                "status": self.status.value,
                "owner": self.owner,
                "token": self.token,
                "created_at_utc": self.created_at_utc,
                "updated_at_utc": self.updated_at_utc,
                "ttl_seconds": self.ttl_seconds,
            }
        )

    @classmethod
    def from_json(cls, raw_value: str) -> "SchedulerSlotLock":
        """Parse a running lock value and validate the required fields."""

        payload = json.loads(raw_value)
        return cls(
            job=str(payload["job"]),
            slot=str(payload["slot"]),
            status=SchedulerSlotStatus(str(payload["status"])),
            owner=str(payload["owner"]),
            token=str(payload["token"]),
            created_at_utc=str(payload["created_at_utc"]),
            updated_at_utc=str(payload["updated_at_utc"]),
            ttl_seconds=int(payload["ttl_seconds"]),
        )


@dataclass(frozen=True)
class SchedulerSlotDecision:
    """Result of checking completed markers and acquiring a running lock."""

    job: str
    slot: str
    action: SchedulerSlotAction
    status: SchedulerSlotStatus
    running_key: str
    completed_key: str
    status_key: str
    owner: str
    reason: str
    ttl_seconds: int | None = None
    existing_lock: SchedulerSlotLock | None = None
    running_value: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def acquired(self) -> bool:
        """Return whether the caller owns a running lock and may execute."""

        return self.action in {
            SchedulerSlotAction.ACQUIRED,
            SchedulerSlotAction.ACQUIRED_AFTER_STALE,
        }


class RedisSchedulerSlotStore:
    """Redis store for scheduler running locks and terminal markers.

    Parameters: `redis_client` may be injected by tests; otherwise the shared
    Redis client factory is used lazily.
    Failure scenarios: Redis command failures raise `RedisError`; the runner
    must then avoid executing the job because slot state is unknown.
    External effects: writes only scheduler keys under `scheduler:*`.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis_client = redis_client

    @property
    def redis_client(self) -> Any:
        if self._redis_client is None:
            self._redis_client = get_redis_client()
        return self._redis_client

    def acquire_slot_for_run(
        self,
        *,
        job: str,
        slot: str,
        owner: str,
        running_ttl_seconds: int,
        status_marker_ttl_seconds: int,
        current_time_utc: datetime | None = None,
    ) -> SchedulerSlotDecision:
        """Acquire the running lock for one scheduler job slot.

        The lifecycle is intentionally explicit:
        1. completed marker exists -> skip as completed.
        2. failed/blocked/skipped marker exists -> skip without retrying the
           same slot every poll cycle.
        3. running lock can be acquired -> execute.
        4. running lock exists -> inspect JSON and TTL; normal running locks are
           skipped, while stale/expired locks are marked and retried once.
        """

        if running_ttl_seconds <= 0:
            raise RedisError("scheduler running lock TTL must be greater than 0")
        if status_marker_ttl_seconds <= 0:
            raise RedisError("scheduler status marker TTL must be greater than 0")

        active_now = _ensure_utc_aware(current_time_utc or now_utc())
        keys = _slot_keys(job=job, slot=slot)
        completed_marker = self._read_json_key(keys["completed_key"])
        if completed_marker is not None:
            return self._skip_decision(
                job=job,
                slot=slot,
                keys=keys,
                status=SchedulerSlotStatus.COMPLETED,
                owner=owner,
                reason="completed_marker_exists",
                ttl_seconds=self._read_ttl(keys["completed_key"]),
                details={"marker": completed_marker},
            )

        terminal_marker = self._read_terminal_status_marker(keys["status_key"])
        if terminal_marker is not None:
            marker_status = SchedulerSlotStatus(str(terminal_marker.get("status")))
            return self._skip_decision(
                job=job,
                slot=slot,
                keys=keys,
                status=marker_status,
                owner=owner,
                reason=f"{marker_status.value}_marker_exists",
                ttl_seconds=self._read_ttl(keys["status_key"]),
                details={"marker": terminal_marker},
            )

        lock = _build_running_lock(
            job=job,
            slot=slot,
            owner=owner,
            ttl_seconds=running_ttl_seconds,
            active_now=active_now,
        )
        running_value = lock.to_json()
        if self._set_key(keys["running_key"], running_value, nx=True, ex=running_ttl_seconds):
            return SchedulerSlotDecision(
                job=job,
                slot=slot,
                action=SchedulerSlotAction.ACQUIRED,
                status=SchedulerSlotStatus.RUNNING,
                running_key=keys["running_key"],
                completed_key=keys["completed_key"],
                status_key=keys["status_key"],
                owner=owner,
                reason="running_lock_acquired",
                ttl_seconds=running_ttl_seconds,
                running_value=running_value,
                details={"lock": json.loads(running_value)},
            )

        return self._inspect_existing_running_lock(
            job=job,
            slot=slot,
            owner=owner,
            keys=keys,
            new_running_value=running_value,
            running_ttl_seconds=running_ttl_seconds,
            status_marker_ttl_seconds=status_marker_ttl_seconds,
            active_now=active_now,
        )

    def mark_slot_completed(
        self,
        *,
        job: str,
        slot: str,
        owner: str,
        completed_ttl_seconds: int,
        source: str = "scheduler",
        result_status: str | None = None,
        details: Mapping[str, Any] | None = None,
        current_time_utc: datetime | None = None,
    ) -> None:
        """Write the completed marker after a slot is safely handled.

        The completed marker is separate from the running lock and may live much
        longer. It prevents catch-up scanning from repeating a slot that already
        reached a successful terminal scheduler outcome.
        """

        if completed_ttl_seconds <= 0:
            raise RedisError("scheduler completed marker TTL must be greater than 0")
        active_now = _ensure_utc_aware(current_time_utc or now_utc())
        payload = {
            "job": job,
            "slot": slot,
            "status": SchedulerSlotStatus.COMPLETED.value,
            "owner": owner,
            "completed_at_utc": _iso_utc_z(active_now),
            "source": source,
            "ttl_seconds": completed_ttl_seconds,
        }
        if result_status:
            payload["result_status"] = result_status
        if details:
            payload["details"] = dict(details)
        self._set_key(
            build_scheduler_completed_key(job=job, slot=slot),
            _json_dumps(payload),
            ex=completed_ttl_seconds,
        )

    def mark_slot_status(
        self,
        *,
        job: str,
        slot: str,
        status: SchedulerSlotStatus,
        owner: str,
        reason: str,
        ttl_seconds: int,
        details: Mapping[str, Any] | None = None,
        current_time_utc: datetime | None = None,
    ) -> None:
        """Write a non-completed diagnostic marker for a slot outcome."""

        if ttl_seconds <= 0:
            raise RedisError("scheduler status marker TTL must be greater than 0")
        if status == SchedulerSlotStatus.COMPLETED:
            raise RedisError("completed status must use mark_slot_completed")
        active_now = _ensure_utc_aware(current_time_utc or now_utc())
        payload = {
            "job": job,
            "slot": slot,
            "status": status.value,
            "owner": owner,
            "reason": reason,
            "created_at_utc": _iso_utc_z(active_now),
            "updated_at_utc": _iso_utc_z(active_now),
            "source": "scheduler",
            "ttl_seconds": ttl_seconds,
        }
        if details:
            payload["details"] = dict(details)
        self._set_key(build_scheduler_status_key(job=job, slot=slot), _json_dumps(payload), ex=ttl_seconds)

    def release_running_lock(self, *, running_key: str, running_value: str) -> bool:
        """Release a running lock only if the exact JSON value still matches."""

        try:
            return bool(int(self.redis_client.eval(_DELETE_KEY_IF_VALUE_MATCHES, 1, running_key, running_value) or 0))
        except Exception as exc:  # noqa: BLE001 - normalize Redis driver failures.
            raise RedisError(f"scheduler running lock release failed key={running_key}") from exc

    def _inspect_existing_running_lock(
        self,
        *,
        job: str,
        slot: str,
        owner: str,
        keys: Mapping[str, str],
        new_running_value: str,
        running_ttl_seconds: int,
        status_marker_ttl_seconds: int,
        active_now: datetime,
    ) -> SchedulerSlotDecision:
        raw_value = self._get_key(keys["running_key"])
        ttl_seconds = self._read_ttl(keys["running_key"])
        if raw_value is None or ttl_seconds == -2:
            if self._set_key(keys["running_key"], new_running_value, nx=True, ex=running_ttl_seconds):
                return SchedulerSlotDecision(
                    job=job,
                    slot=slot,
                    action=SchedulerSlotAction.ACQUIRED,
                    status=SchedulerSlotStatus.RUNNING,
                    running_key=keys["running_key"],
                    completed_key=keys["completed_key"],
                    status_key=keys["status_key"],
                    owner=owner,
                    reason="running_lock_acquired_after_missing_lock_race",
                    ttl_seconds=running_ttl_seconds,
                    running_value=new_running_value,
                    details={"lock": json.loads(new_running_value)},
                )
            raw_value = self._get_key(keys["running_key"])
            ttl_seconds = self._read_ttl(keys["running_key"])

        status, reason, parsed_lock = _classify_running_lock(
            raw_value=raw_value,
            ttl_seconds=ttl_seconds,
            running_ttl_seconds=running_ttl_seconds,
            active_now=active_now,
        )
        if status == SchedulerSlotStatus.RUNNING:
            return self._skip_decision(
                job=job,
                slot=slot,
                keys=keys,
                status=SchedulerSlotStatus.RUNNING,
                owner=owner,
                reason=reason,
                ttl_seconds=ttl_seconds,
                existing_lock=parsed_lock,
                details={"lock": _lock_details(raw_value, parsed_lock)},
            )

        self.mark_slot_status(
            job=job,
            slot=slot,
            status=status,
            owner=owner,
            reason=reason,
            ttl_seconds=status_marker_ttl_seconds,
            details={
                "running_key": keys["running_key"],
                "running_ttl_seconds": ttl_seconds,
                "previous_lock": _lock_details(raw_value, parsed_lock),
                "action": "retry_after_stale_or_expired_running_lock",
            },
            current_time_utc=active_now,
        )
        deleted = self._delete_key_if_value_matches(keys["running_key"], raw_value or "")
        if deleted and self._set_key(keys["running_key"], new_running_value, nx=True, ex=running_ttl_seconds):
            return SchedulerSlotDecision(
                job=job,
                slot=slot,
                action=SchedulerSlotAction.ACQUIRED_AFTER_STALE,
                status=SchedulerSlotStatus.RUNNING,
                running_key=keys["running_key"],
                completed_key=keys["completed_key"],
                status_key=keys["status_key"],
                owner=owner,
                reason=f"retry_after_{status.value}_running_lock",
                ttl_seconds=running_ttl_seconds,
                existing_lock=parsed_lock,
                running_value=new_running_value,
                details={
                    "previous_status": status.value,
                    "previous_reason": reason,
                    "previous_ttl_seconds": ttl_seconds,
                    "lock": json.loads(new_running_value),
                },
            )
        return self._skip_decision(
            job=job,
            slot=slot,
            keys=keys,
            status=status,
            owner=owner,
            reason=f"{reason}_retry_not_acquired",
            ttl_seconds=ttl_seconds,
            existing_lock=parsed_lock,
            details={"lock": _lock_details(raw_value, parsed_lock), "action": "skip"},
        )

    def _read_terminal_status_marker(self, key: str) -> dict[str, Any] | None:
        payload = self._read_json_key(key)
        if payload is None:
            return None
        try:
            status = SchedulerSlotStatus(str(payload.get("status")))
        except ValueError:
            return None
        if status in {
            SchedulerSlotStatus.FAILED,
            SchedulerSlotStatus.SKIPPED,
            SchedulerSlotStatus.BLOCKED,
        }:
            return payload
        return None

    def _skip_decision(
        self,
        *,
        job: str,
        slot: str,
        keys: Mapping[str, str],
        status: SchedulerSlotStatus,
        owner: str,
        reason: str,
        ttl_seconds: int | None,
        existing_lock: SchedulerSlotLock | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> SchedulerSlotDecision:
        return SchedulerSlotDecision(
            job=job,
            slot=slot,
            action=SchedulerSlotAction.SKIP,
            status=status,
            running_key=keys["running_key"],
            completed_key=keys["completed_key"],
            status_key=keys["status_key"],
            owner=owner,
            reason=reason,
            ttl_seconds=ttl_seconds,
            existing_lock=existing_lock,
            details=details or {},
        )

    def _read_json_key(self, key: str) -> dict[str, Any] | None:
        raw_value = self._get_key(key)
        if raw_value is None:
            return None
        try:
            payload = json.loads(raw_value)
        except (TypeError, ValueError):
            return {"status": "unknown", "raw_value": raw_value, "invalid_json": True}
        if isinstance(payload, dict):
            return dict(payload)
        return {"status": "unknown", "raw_value": raw_value, "invalid_json": True}

    def _get_key(self, key: str) -> str | None:
        try:
            raw_value = self.redis_client.get(key)
        except Exception as exc:  # noqa: BLE001 - normalize Redis driver failures.
            raise RedisError(f"scheduler slot read failed key={key}") from exc
        if raw_value is None:
            return None
        if isinstance(raw_value, bytes):
            return raw_value.decode("utf-8")
        return str(raw_value)

    def _read_ttl(self, key: str) -> int | None:
        try:
            value = self.redis_client.ttl(key)
        except Exception as exc:  # noqa: BLE001 - normalize Redis driver failures.
            raise RedisError(f"scheduler slot TTL read failed key={key}") from exc
        return int(value) if value is not None else None

    def _set_key(self, key: str, value: str, *, ex: int, nx: bool = False) -> bool:
        try:
            return bool(self.redis_client.set(key, value, nx=nx, ex=ex))
        except Exception as exc:  # noqa: BLE001 - normalize Redis driver failures.
            raise RedisError(f"scheduler slot write failed key={key}") from exc

    def _delete_key_if_value_matches(self, key: str, expected_value: str) -> bool:
        try:
            return bool(int(self.redis_client.eval(_DELETE_KEY_IF_VALUE_MATCHES, 1, key, expected_value) or 0))
        except Exception as exc:  # noqa: BLE001 - normalize Redis driver failures.
            raise RedisError(f"scheduler stale running lock cleanup failed key={key}") from exc


def build_scheduler_running_key(*, job: str, slot: str) -> str:
    """Build the Redis key for the short running lock."""

    return f"scheduler:running:{job}:{slot}"


def build_scheduler_completed_key(*, job: str, slot: str) -> str:
    """Build the Redis key for the longer completed marker."""

    return f"scheduler:completed:{job}:{slot}"


def build_scheduler_status_key(*, job: str, slot: str) -> str:
    """Build the Redis key for failed, skipped, blocked, stale, or expired markers."""

    return f"scheduler:status:{job}:{slot}"


def build_kline_4h_incremental_slot_id(slot_time_utc: datetime) -> str:
    """Build the UTC slot id for one 09 scheduled 4h collection window."""

    return _ensure_utc_aware(slot_time_utc).strftime("%Y-%m-%dT%H:%MZ")


def build_kline_1d_incremental_slot_id(slot_time_utc: datetime) -> str:
    """Build the UTC slot id for one scheduled 1d collection window."""

    return _ensure_utc_aware(slot_time_utc).strftime("%Y-%m-%dT%H:%MZ")


def build_kline_1d_integrity_slot_id(slot_date_utc: Any) -> str:
    """Build the UTC slot id for one 1d daily integrity review date."""

    return slot_date_utc.isoformat()


def build_daily_kline_integrity_slot_id(slot_date_utc: Any) -> str:
    """Build the UTC slot id for one 11 daily integrity review date."""

    return slot_date_utc.isoformat()


def build_scheduler_owner(*, trace_id: str) -> str:
    """Build a diagnostic owner string for scheduler Redis values."""

    return f"{socket.gethostname()}:{os.getpid()}:{trace_id[:8]}"


def _slot_keys(*, job: str, slot: str) -> dict[str, str]:
    return {
        "running_key": build_scheduler_running_key(job=job, slot=slot),
        "completed_key": build_scheduler_completed_key(job=job, slot=slot),
        "status_key": build_scheduler_status_key(job=job, slot=slot),
    }


def _build_running_lock(
    *,
    job: str,
    slot: str,
    owner: str,
    ttl_seconds: int,
    active_now: datetime,
) -> SchedulerSlotLock:
    now_text = _iso_utc_z(active_now)
    return SchedulerSlotLock(
        job=job,
        slot=slot,
        status=SchedulerSlotStatus.RUNNING,
        owner=owner,
        token=uuid4().hex,
        created_at_utc=now_text,
        updated_at_utc=now_text,
        ttl_seconds=ttl_seconds,
    )


def _classify_running_lock(
    *,
    raw_value: str | None,
    ttl_seconds: int | None,
    running_ttl_seconds: int,
    active_now: datetime,
) -> tuple[SchedulerSlotStatus, str, SchedulerSlotLock | None]:
    if raw_value is None:
        return SchedulerSlotStatus.EXPIRED, "running_lock_missing", None
    try:
        parsed_lock = SchedulerSlotLock.from_json(raw_value)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return SchedulerSlotStatus.STALE, "running_lock_value_invalid", None

    if ttl_seconds is None:
        return SchedulerSlotStatus.STALE, "running_lock_ttl_unavailable", parsed_lock
    if ttl_seconds == -2:
        return SchedulerSlotStatus.EXPIRED, "running_lock_key_expired", parsed_lock
    if ttl_seconds == -1:
        return SchedulerSlotStatus.EXPIRED, "running_lock_without_ttl", parsed_lock
    if ttl_seconds <= 0:
        return SchedulerSlotStatus.EXPIRED, "running_lock_ttl_expired", parsed_lock
    if ttl_seconds > running_ttl_seconds:
        return SchedulerSlotStatus.STALE, "running_lock_ttl_exceeds_config", parsed_lock

    created_at_utc = _parse_utc_text(parsed_lock.created_at_utc)
    if created_at_utc is None:
        return SchedulerSlotStatus.STALE, "running_lock_created_at_invalid", parsed_lock
    age_seconds = (active_now - created_at_utc).total_seconds()
    if age_seconds > running_ttl_seconds:
        return SchedulerSlotStatus.STALE, "running_lock_age_exceeds_limit", parsed_lock

    return SchedulerSlotStatus.RUNNING, "running_lock_active", parsed_lock


def _lock_details(raw_value: str | None, parsed_lock: SchedulerSlotLock | None) -> dict[str, Any]:
    if parsed_lock is None:
        return {"raw_value": raw_value or "", "parsed": False}
    return {
        "job": parsed_lock.job,
        "slot": parsed_lock.slot,
        "status": parsed_lock.status.value,
        "owner": parsed_lock.owner,
        "created_at_utc": parsed_lock.created_at_utc,
        "updated_at_utc": parsed_lock.updated_at_utc,
        "ttl_seconds": parsed_lock.ttl_seconds,
        "parsed": True,
    }


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso_utc_z(value: datetime) -> str:
    return _ensure_utc_aware(value).isoformat().replace("+00:00", "Z")


def _parse_utc_text(value: str) -> datetime | None:
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _ensure_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise RedisError("scheduler slot time must be timezone-aware UTC")
    return value.astimezone(UTC)


__all__ = [
    "DAILY_KLINE_INTEGRITY_JOB_NAME",
    "KLINE_1D_INCREMENTAL_JOB_NAME",
    "KLINE_1D_INTEGRITY_JOB_NAME",
    "KLINE_4H_INCREMENTAL_JOB_NAME",
    "RedisSchedulerSlotStore",
    "SchedulerSlotAction",
    "SchedulerSlotDecision",
    "SchedulerSlotLock",
    "SchedulerSlotStatus",
    "build_daily_kline_integrity_slot_id",
    "build_kline_1d_incremental_slot_id",
    "build_kline_1d_integrity_slot_id",
    "build_kline_4h_incremental_slot_id",
    "build_scheduler_completed_key",
    "build_scheduler_owner",
    "build_scheduler_running_key",
    "build_scheduler_status_key",
]
