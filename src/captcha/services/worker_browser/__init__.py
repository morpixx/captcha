"""captcha-worker-browser — browser-lane solver worker.

Per-task `CamoufoxProvider` sessions from `ctx.browser`; no shared context pool.
See `docs/production-contract.md` §2–§4.
"""

from __future__ import annotations

from .gate import ConcurrencyGate, free_memory_mb
from .heartbeat import LaneHeartbeat, cap_key, lane_key
from .limits import (
    DEFAULT_CONCURRENCY,
    DEFAULT_TIMEOUT_SEC,
    LANE,
    MAX_TIMEOUT_SEC,
    MIN_TIMEOUT_SEC,
    clamp_timeout,
)
from .provider import close_provider, resolve_provider
from .tracking import SessionTracker, TrackingProvider
from .worker import Worker

__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_TIMEOUT_SEC",
    "LANE",
    "MAX_TIMEOUT_SEC",
    "MIN_TIMEOUT_SEC",
    "ConcurrencyGate",
    "LaneHeartbeat",
    "SessionTracker",
    "TrackingProvider",
    "Worker",
    "cap_key",
    "clamp_timeout",
    "close_provider",
    "free_memory_mb",
    "lane_key",
    "resolve_provider",
]
