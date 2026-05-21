"""Redis lock helpers for stage-20C model-review chain worker.

This file belongs to `app/model_review_chain`. It builds bounded lock keys and
wraps the shared Redis task lock used to avoid concurrent worker progress for
the same material pack, chain, or step.

Called by `app/model_review_chain/worker.py`.
External services: Redis only through `app/core/task_lock.py`. MySQL: none.
Hermes: none. DeepSeek/GPT/Claude calls: none. Trading execution: none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.task_lock import RedisTaskLock


@dataclass(frozen=True)
class ModelReviewWorkerLock:
    """One acquired 20C worker lock identity."""

    key: str
    owner: str
    acquired: bool
    reason: str = ""


class ModelReviewChainWorkerLockManager:
    """Small adapter around the shared Redis task lock.

    Parameters: optional `RedisTaskLock` compatible object for tests.
    Return value: lock manager.
    Failure scenarios: Redis errors propagate to the worker, which records a
    structured skipped/blocked result instead of crashing scheduler.
    External effects: writes/deletes only model-review worker lock keys.
    """

    def __init__(self, *, task_lock: Any | None = None) -> None:
        self._task_lock = task_lock or RedisTaskLock()

    def acquire_worker_lock(self, *, key: str, owner: str, ttl_seconds: int) -> ModelReviewWorkerLock:
        """Try to acquire one Redis worker lock."""

        acquired = bool(self._task_lock.acquire_lock(key=key, owner=owner, ttl_seconds=ttl_seconds))
        return ModelReviewWorkerLock(
            key=key,
            owner=owner,
            acquired=acquired,
            reason="" if acquired else "lock_already_held",
        )

    def release_worker_lock(self, lock: ModelReviewWorkerLock) -> bool:
        """Release one previously acquired worker lock by owner."""

        if not lock.acquired:
            return False
        return bool(self._task_lock.release_lock(key=lock.key, owner=lock.owner))


def build_material_worker_lock_key(*, material_pack_id: str) -> str:
    """Build the lock key preventing duplicate automatic chains per material."""

    return f"model_review_chain:material:{material_pack_id.strip()}"


def build_chain_worker_lock_key(*, chain_id: str) -> str:
    """Build the lock key preventing two workers from advancing one chain."""

    return f"model_review_chain:chain:{chain_id.strip()}"


def build_step_worker_lock_key(*, chain_id: str, step_no: int) -> str:
    """Build the lock key preventing concurrent execution of one step."""

    return f"model_review_chain:step:{chain_id.strip()}:{int(step_no)}"


__all__ = [
    "ModelReviewChainWorkerLockManager",
    "ModelReviewWorkerLock",
    "build_chain_worker_lock_key",
    "build_material_worker_lock_key",
    "build_step_worker_lock_key",
]
