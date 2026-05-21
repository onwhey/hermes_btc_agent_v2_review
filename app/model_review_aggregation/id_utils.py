"""ID helpers for stage-20A model review aggregation.

This file belongs to `app/model_review_aggregation`. It generates business IDs
for aggregation rows. It does not read/write databases, call large models,
send Hermes, write Redis, modify Kline tables, or perform trading.
"""

from __future__ import annotations

import uuid


def build_model_review_aggregation_run_id(material_pack_id: str, *, trace_id: str) -> str:
    """Return one compact stage-20A aggregation run id.

    Parameters: `material_pack_id` is the stage-18 material pack being
    aggregated; `trace_id` links the CLI run to logs and persisted rows.
    Return value: a business id prefixed with `MRAG`.
    Failure scenarios: none expected for normal string inputs.
    External effects: none.
    """

    stable = uuid.uuid5(uuid.NAMESPACE_URL, f"{material_pack_id}:stage20a").hex[:12]
    return f"MRAG-{stable}-{trace_id[:8] or uuid.uuid4().hex[:8]}"


__all__ = ["build_model_review_aggregation_run_id"]
