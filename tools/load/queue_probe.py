"""Queue depth sampled straight from Redis: XLEN plus consumer-group lag.

The gateway's Prometheus gauge is per-process and only moves when a worker
ticks; the streams are the ground truth, so the driver reads them directly.
`lag` is the count of entries not yet delivered to any consumer — that is the
depth a backlog actually shows up in. `pending` is delivered-but-unacked, i.e.
work in flight inside the worker.

Sampling is best-effort: a Redis hiccup or a missing group degrades the depth
section of the report, it never fails the run.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

DEFAULT_SAMPLE_SEC = 1.0


def lane_for_type(type_name: str) -> str:
    """Lane a captcha type is routed to, per `captcha.domain.catalog`."""
    try:
        from captcha.domain.catalog import lane_for
        from captcha.domain.enums import CaptchaType

        return str(lane_for(CaptchaType(type_name)))
    except (ImportError, ValueError):
        return "http"


def stream_for(prefix: str, lane: str) -> str:
    return f"{prefix}:stream:{lane}"


def group_for(lane: str) -> str:
    return f"group:lane-{lane}"


@dataclass(slots=True)
class _Series:
    total: int = 0
    count: int = 0
    peak: int = 0

    def add(self, value: int) -> None:
        self.total += value
        self.count += 1
        if value > self.peak:
            self.peak = value

    def as_dict(self) -> dict[str, Any]:
        return {
            "samples": self.count,
            "avg": round(self.total / self.count, 2) if self.count else 0.0,
            "max": self.peak,
        }


@dataclass(slots=True)
class QueueProbe:
    """Background sampler for one lane's stream. `start()` before the run."""

    redis_url: str = ""
    lane: str = "http"
    prefix: str = "captcha"
    sample_sec: float = DEFAULT_SAMPLE_SEC
    _client: Any = None
    _task: asyncio.Task[None] | None = None
    _lag: _Series = field(default_factory=_Series)
    _pending: _Series = field(default_factory=_Series)
    _xlen: _Series = field(default_factory=_Series)
    _errors: int = 0

    @property
    def stream(self) -> str:
        return stream_for(self.prefix, self.lane)

    async def start(self) -> None:
        if not self.redis_url:
            return
        try:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self.redis_url, decode_responses=True)
            await self._client.ping()
        except Exception:
            self._client = None
            self._errors += 1
            return
        self._task = asyncio.create_task(self._loop(), name="queue-probe")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._sample()
            except Exception:
                self._errors += 1
            await asyncio.sleep(self.sample_sec)

    async def _sample(self) -> None:
        xlen = int(await self._client.xlen(self.stream))
        lag = 0
        pending = 0
        try:
            groups = await self._client.xinfo_groups(self.stream)
        except Exception:
            groups = []
        for group in groups or []:
            if group.get("name") != group_for(self.lane):
                continue
            lag = int(group.get("lag") or 0)
            pending = int(group.get("pending") or 0)
            break
        self._xlen.add(xlen)
        self._lag.add(lag)
        self._pending.add(pending)

    def summary(self) -> dict[str, Any]:
        if self._client is None and self._lag.count == 0:
            return {"enabled": False, "reason": "no redis" if self.redis_url else "not configured"}
        return {
            "enabled": True,
            "lane": self.lane,
            "stream": self.stream,
            "group": group_for(self.lane),
            "lag": self._lag.as_dict(),
            "pending": self._pending.as_dict(),
            "xlen": self._xlen.as_dict(),
            "sample_errors": self._errors,
        }
