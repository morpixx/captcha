"""Per-account rate limits and quotas, enforced on Redis counters.

Four independent limits, all read from Settings and each disabled when 0:

```
RATE_LIMIT_CREATE_PER_MIN      fixed one-minute window, per account
MAX_ACTIVE_TASKS_PER_ACCOUNT   ZSET of task ids created within the task TTL
MAX_DAILY_TASKS                UTC-day task counter
MAX_DAILY_SPEND                UTC-day spend counter, decimal cost
```

`check()` reserves the rate-limit slot and decides; `record()` writes the task
into the active/daily counters once it is actually queued. The reservation is
not transactional: two requests racing at the exact boundary can overshoot by
one. That is the trade every fixed-window limiter makes, and it is cheaper than
holding a Redis transaction across the create path. The active set is pruned by
task id age instead of worker callbacks, so it errs on the conservative side —
a task that finishes early still counts until its TTL expires.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

_log = structlog.get_logger(__name__)

_RATE_WINDOW_SEC = 60
_DAY_SEC = 86_400
#: Keep in step with `_TASK_TTL_SEC` in routes/tasks.py and routes/legacy.py.
_ACTIVE_WINDOW_SEC = 300.0


def _as_text(raw: Any) -> str:
    """Redis is built with decode_responses=False, so counters come back as bytes."""
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "replace")
    return str(raw)


@dataclass(frozen=True)
class QuotaLimits:
    """Limits for one process. Zero means "no limit", not "reject everything"."""

    create_per_min: int = 0
    active_tasks: int = 0
    daily_tasks: int = 0
    daily_spend: Decimal = Decimal("0")

    @classmethod
    def from_settings(cls, settings: Any) -> QuotaLimits:
        if settings is None:
            return cls()

        def _count(name: str) -> int:
            try:
                return max(0, int(getattr(settings, name, 0) or 0))
            except (TypeError, ValueError):
                return 0

        try:
            spend = Decimal(str(getattr(settings, "max_daily_spend", 0) or 0))
        except (InvalidOperation, TypeError, ValueError):
            spend = Decimal("0")
        return cls(
            create_per_min=_count("rate_limit_create_per_min"),
            active_tasks=_count("max_active_tasks_per_account"),
            daily_tasks=_count("max_daily_tasks"),
            daily_spend=max(Decimal("0"), spend),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.create_per_min or self.active_tasks or self.daily_tasks or self.daily_spend > 0)


class QuotaGuard:
    """Counter bookkeeping for one createTask call.

    `redis` may be None (tests, or a gateway booted without Redis for a smoke
    run); a guard without a client or without limits always allows the task.
    """

    def __init__(
        self,
        redis: Any,
        limits: QuotaLimits,
        *,
        prefix: str = "captcha",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._r = redis
        self.limits = limits
        self._prefix = prefix
        self._clock = clock

    def _keys(self, account_id: Any) -> tuple[str, str, str, str]:
        day = datetime.fromtimestamp(self._clock(), UTC).strftime("%Y%m%d")
        base = f"{self._prefix}:quota"
        acc = str(account_id)
        return (
            f"{base}:rate:create:{acc}",
            f"{base}:active:{acc}",
            f"{base}:day:{acc}:{day}",
            f"{base}:spend:{acc}:{day}",
        )

    async def check(self, account_id: Any, cost: Decimal) -> str | None:
        """Reserve the creation slot. Returns None when allowed, else a reason."""
        if not self.limits.enabled or self._r is None:
            return None

        rate_key, active_key, day_key, spend_key = self._keys(account_id)

        if self.limits.create_per_min:
            created = int(await self._r.incr(rate_key))
            if created == 1:
                await self._r.expire(rate_key, _RATE_WINDOW_SEC)
            if created > self.limits.create_per_min:
                return "rate_per_min"

        if self.limits.active_tasks:
            await self._r.zremrangebyscore(active_key, "-inf", self._clock() - _ACTIVE_WINDOW_SEC)
            active = int(await self._r.zcard(active_key))
            if active >= self.limits.active_tasks:
                return "active_tasks"

        if self.limits.daily_tasks:
            used = int(_as_text(await self._r.get(day_key)) or 0)
            if used >= self.limits.daily_tasks:
                return "daily_tasks"

        if self.limits.daily_spend:
            spent = Decimal(_as_text(await self._r.get(spend_key)) or "0")
            if spent + cost > self.limits.daily_spend:
                return "daily_spend"

        return None

    async def record(self, account_id: Any, task_id: Any, cost: Decimal) -> None:
        """Book a task that actually made it onto a stream."""
        if not self.limits.enabled or self._r is None:
            return

        _rate_key, active_key, day_key, spend_key = self._keys(account_id)

        if self.limits.active_tasks:
            await self._r.zadd(active_key, {str(task_id): self._clock()})
            await self._r.expire(active_key, int(_ACTIVE_WINDOW_SEC * 2))

        if self.limits.daily_tasks:
            count = int(await self._r.incr(day_key))
            if count == 1:
                await self._r.expire(day_key, 2 * _DAY_SEC)

        if self.limits.daily_spend:
            await self._r.incrbyfloat(spend_key, float(cost))
            await self._r.expire(spend_key, 2 * _DAY_SEC)


__all__ = ["QuotaGuard", "QuotaLimits"]
