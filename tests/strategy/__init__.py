"""Tests for the stage-16 strategy signal framework."""

from typing import Any


class NoOpEvidenceAggregationHook:
    """Keep stage-16 persistence tests isolated from stage-23F/24A auto aggregation."""

    def maybe_run_after_strategy_signal_persistence(
        self,
        db_session: Any,
        *,
        request: Any,
        result: Any,
    ) -> Any:
        """Return the stage-16 result without database access, commits, or Hermes sends."""

        return result
