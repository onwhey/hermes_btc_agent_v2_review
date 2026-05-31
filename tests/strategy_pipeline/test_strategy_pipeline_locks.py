from __future__ import annotations

from app.strategy_pipeline.locks import StrategyPipelineLock, StrategyPipelineLockManager


class KeywordOnlyTaskLock:
    def __init__(self) -> None:
        self.acquire_calls: list[dict[str, object]] = []
        self.release_calls: list[dict[str, object]] = []

    def acquire_lock(self, *, key: str, owner: str, ttl_seconds: int) -> bool:
        self.acquire_calls.append(
            {
                "key": key,
                "owner": owner,
                "ttl_seconds": ttl_seconds,
            }
        )
        return True

    def release_lock(self, *, key: str, owner: str) -> bool:
        self.release_calls.append(
            {
                "key": key,
                "owner": owner,
            }
        )
        return True


def test_strategy_pipeline_lock_manager_uses_keyword_only_task_lock_calls() -> None:
    task_lock = KeywordOnlyTaskLock()
    manager = StrategyPipelineLockManager(task_lock=task_lock)  # type: ignore[arg-type]
    lock = StrategyPipelineLock(
        key="strategy_pipeline:BTCUSDT:4h:1d:2026-05-30T04:00:00Z",
        owner="strategy_pipeline:SP-test",
        ttl_seconds=1800,
    )

    acquired = manager.acquire_strategy_pipeline_lock(lock=lock)
    manager.release_strategy_pipeline_lock(lock=acquired)

    assert acquired.acquired is True
    assert task_lock.acquire_calls == [
        {
            "key": lock.key,
            "owner": lock.owner,
            "ttl_seconds": lock.ttl_seconds,
        }
    ]
    assert task_lock.release_calls == [
        {
            "key": lock.key,
            "owner": lock.owner,
        }
    ]
