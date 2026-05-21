"""Stage-20B model review chain orchestration package.

This package contains only the chain/step state machine, compact persistence,
and manual CLI-facing DTOs for mock relay execution. It does not connect
scheduler jobs, call real model providers, generate trading advice, modify
formal Kline data, read/write Redis, or send Hermes.
"""

