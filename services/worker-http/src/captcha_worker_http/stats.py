"""Per-type rolling success-rate tracker.

Each record is a member in a redis sorted set keyed by captcha type. `score`
is the event timestamp in milliseconds; `member` is `<ts_ms>:<nonce>:ok` or
`:err`. ZRANGEBYSCORE over the trailing `window_sec` gives the window; we
count outcomes with a Lua pipeline so the trim+count is one round-trip per
query.

This exists so the router can flip to `solve_browser` when the HTTP path for
a given type is bleeding (sudden sensor-schema drift, captcha redesign,
upstream model regression). The threshold+min-N floor keeps a new/dead type
from false-positive-flipping on a single failure.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from captcha_common.enums import CaptchaType

if TYPE_CHECKING:
    from redis.asyncio import Redis


_DEFAULT_WINDOW_SEC = 3600
_DEFAULT_TTL_SEC = 3 * 3600  # keep a bit beyond the window so warmup after a gap works


class SuccessRateTracker:
    """Rolling success-rate per CaptchaType, backed by redis ZSETs.

    Membership scheme:
        key = <prefix>:stats:<type>
        score = time.time() * 1000 (int ms)
        member = "<score>:<pid>:<nonce>:<ok|err>"

    The score+nonce suffix keeps members unique; the `:ok`/`:err` tail is the
    outcome we match on when we count.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "captcha",
        window_sec: int = _DEFAULT_WINDOW_SEC,
        ttl_sec: int = _DEFAULT_TTL_SEC,
    ) -> None:
        self._r = redis
        self._prefix = prefix
        self._window_sec = window_sec
        self._ttl_sec = ttl_sec
        self._pid = os.getpid()
        self._seq = 0

    def _key(self, ct: CaptchaType | str) -> str:
        t = ct.value if isinstance(ct, CaptchaType) else ct
        return f"{self._prefix}:stats:{t}"

    def _member(self, ts_ms: int, ok: bool) -> str:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        tag = "ok" if ok else "err"
        return f"{ts_ms}:{self._pid}:{self._seq}:{tag}"

    async def record(self, ct: CaptchaType | str, ok: bool) -> None:
        """Append one outcome and trim old entries in a single pipeline."""
        ts_ms = int(time.time() * 1000)
        key = self._key(ct)
        cutoff_ms = ts_ms - self._window_sec * 1000
        member = self._member(ts_ms, ok)
        async with self._r.pipeline(transaction=False) as pipe:
            pipe.zadd(key, {member: ts_ms})
            pipe.zremrangebyscore(key, "-inf", cutoff_ms)
            pipe.expire(key, self._ttl_sec)
            await pipe.execute()

    async def rate(self, ct: CaptchaType | str) -> tuple[float, int]:
        """Return (success_rate, sample_count) for the trailing window.

        rate is 1.0 on an empty window (nothing bad has happened), so that a
        cold type doesn't get its HTTP path yanked on day zero.
        """
        key = self._key(ct)
        ts_ms = int(time.time() * 1000)
        cutoff_ms = ts_ms - self._window_sec * 1000
        # Pull the window; filter in Python since ZRANGEBYLEX on fixed suffix
        # requires a different encoding and this call is infrequent.
        members: list[bytes | str] = await self._r.zrangebyscore(
            key, cutoff_ms, "+inf"
        )
        if not members:
            return 1.0, 0
        total = 0
        ok_count = 0
        for m in members:
            s = m.decode("utf-8") if isinstance(m, (bytes, bytearray)) else m
            # tail is the outcome tag
            tail = s.rsplit(":", 1)[-1]
            if tail == "ok":
                ok_count += 1
                total += 1
            elif tail == "err":
                total += 1
        if total == 0:
            return 1.0, 0
        return ok_count / total, total

    async def should_fallback_to_browser(
        self,
        ct: CaptchaType | str,
        *,
        threshold: float = 0.7,
        min_n: int = 20,
    ) -> bool:
        """True iff we've seen at least `min_n` attempts and success < threshold."""
        rate, n = await self.rate(ct)
        if n < min_n:
            return False
        return rate < threshold
