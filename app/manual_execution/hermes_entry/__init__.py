"""Stage-22B Hermes/WeChat manual execution entry package.

This package parses user-provided manual execution messages into confirmation
intents and confirms MEI-xxx drafts through the stage-22A service. It does not
call large language models, read exchange accounts, modify Kline tables,
change strategy advice lifecycle state, or perform automatic trading.
"""

from __future__ import annotations

__all__: list[str] = []
