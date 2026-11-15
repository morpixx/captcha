"""Browser-lane constants frozen by docs/production-contract.md.

Kept in one module so the worker, the per-task runner and the CLI cannot drift
apart on the values the contract pins (12 sessions, 180–300 s deadline).
"""

from __future__ import annotations

from captcha.adapters.queue.keys import Lane

LANE: Lane = "browser"
DEFAULT_CONCURRENCY: int = 12
DEFAULT_TIMEOUT_SEC: float = 240.0
MIN_TIMEOUT_SEC: float = 180.0
MAX_TIMEOUT_SEC: float = 300.0

# Floor applied when a task carries a deadline that has almost expired.
MIN_TASK_TIMEOUT: float = 1.0
# How long a cancelled solve gets to unwind before the slot is released anyway.
CANCEL_GRACE_SEC: float = 5.0
# Terminal in this lane: retrying cannot change the outcome. ERR_NEEDS_BROWSER
# is terminal here because the hop budget is one (contract §2).
TERMINAL_CODES: frozenset[str] = frozenset({"ERR_UNSOLVABLE", "ERR_CONFIG", "ERR_URL_REJECTED", "ERR_NEEDS_BROWSER"})


def clamp_timeout(value: float) -> float:
    """Contract §4 hard timeout window: 180–300 s."""
    return max(MIN_TIMEOUT_SEC, min(MAX_TIMEOUT_SEC, value))
