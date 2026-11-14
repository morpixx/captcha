"""Residential pool manager. In-memory; persist stats externally if needed."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Awaitable, Callable, Iterable

import httpx
import msgspec
import structlog

from captcha.domain.errors import ProxyError
from captcha.adapters.proxy.proxy import Proxy


class PoolError(ProxyError):
    code = "ERR_PROXY_POOL"


BanDetector = Callable[[httpx.Response], bool | Awaitable[bool]]


class _Entry(msgspec.Struct):
    proxy: Proxy
    bad_until: float = 0.0
    bad_count: int = 0
    last_reason: str | None = None
    uses: int = 0


class Pool:
    """Pool of residential proxies.

    Thread-/task-safe for the operations below only when `pick/mark_bad/stats`
    are called from a single event loop (no locking inside); wrap with an
    `asyncio.Lock` if you cross loops.
    """

    def __init__(self, entries: Iterable[Proxy] | None = None, *, cooldown_s: float = 120.0, max_bad: int = 5) -> None:
        self._entries: list[_Entry] = [_Entry(proxy=p) for p in (entries or [])]
        self._cooldown_s = cooldown_s
        self._max_bad = max_bad
        self._sticky: dict[str, Proxy] = {}
        self._log = structlog.get_logger("captcha.proxy.pool")
        self._ban_detector: BanDetector | None = None

    # ------------------------------------------------------------------ load
    @classmethod
    def load_from_file(cls, path: str | Path, *, cooldown_s: float = 120.0) -> "Pool":
        raw = Path(path).read_bytes()
        try:
            data = msgspec.json.decode(raw)
        except msgspec.DecodeError as e:
            raise PoolError(f"pool file is not valid JSON: {e}", context={"path": str(path)}) from e
        if not isinstance(data, list):
            raise PoolError(f"pool file must be a JSON list, got {type(data).__name__}", context={"path": str(path)})
        proxies = [msgspec.convert(row, Proxy) for row in data]
        return cls(proxies, cooldown_s=cooldown_s)

    # --------------------------------------------------------------- hooks
    def set_ban_detector(self, fn: BanDetector) -> None:
        self._ban_detector = fn

    async def is_banned(self, resp: httpx.Response) -> bool:
        if self._ban_detector is None:
            return False
        out = self._ban_detector(resp)
        if hasattr(out, "__await__"):
            out = await out  # type: ignore[assignment]
        return bool(out)

    # --------------------------------------------------------------- pick
    def pick(
        self,
        *,
        country: str | None = None,
        session_id: str | None = None,
        sticky: bool = False,
        rng: random.Random | None = None,
    ) -> Proxy:
        if sticky and session_id and session_id in self._sticky:
            return self._sticky[session_id]

        now = time.time()
        pool = [e for e in self._entries if e.bad_until < now]
        if country:
            pool = [e for e in pool if e.proxy.country and e.proxy.country.upper() == country.upper()]
        if not pool:
            raise PoolError(
                "no healthy proxies match filter",
                context={"country": country, "total": len(self._entries)},
            )

        entry = (rng or random).choice(pool)
        entry.uses += 1
        chosen = entry.proxy
        if session_id:
            chosen = chosen.with_session(session_id)
        if sticky and session_id:
            self._sticky[session_id] = chosen
        return chosen

    # ------------------------------------------------------------- mark bad
    def mark_bad(self, proxy: Proxy, *, reason: str = "unspecified") -> None:
        now = time.time()
        for entry in self._entries:
            if entry.proxy.url.split("@")[-1] == proxy.url.split("@")[-1]:
                entry.bad_count += 1
                entry.last_reason = reason
                entry.bad_until = now + self._cooldown_s * min(entry.bad_count, 8)
                self._log.warning(
                    "proxy.marked_bad",
                    provider=entry.proxy.provider,
                    host=entry.proxy.url.split("@")[-1],
                    reason=reason,
                    bad_count=entry.bad_count,
                    cooldown_s=self._cooldown_s * min(entry.bad_count, 8),
                )
                break
        # Evict sticky binding so next pick re-rolls.
        if proxy.session_id and proxy.session_id in self._sticky:
            del self._sticky[proxy.session_id]

    # ---------------------------------------------------------- rotate
    def rotate_session(self, session_id: str) -> None:
        """Drop sticky binding for `session_id`; next pick gets a fresh IP."""
        self._sticky.pop(session_id, None)

    # ---------------------------------------------------------- stats
    def stats(self) -> dict:
        now = time.time()
        healthy = sum(1 for e in self._entries if e.bad_until < now)
        cooling = len(self._entries) - healthy
        return {
            "total": len(self._entries),
            "healthy": healthy,
            "cooling": cooling,
            "sticky_bindings": len(self._sticky),
        }

    def __len__(self) -> int:
        return len(self._entries)
