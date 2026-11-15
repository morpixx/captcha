"""Lane heartbeat: lease key + capability set.

Contract (§2): the worker proves it is alive with
`SETEX captcha:lane:browser:<worker> 90 1` and advertises the types it solves
with `SADD captcha:cap:browser <types>`. The gateway sells a type only while its
lane has a fresh heartbeat, so a crashed worker stops receiving work within one
TTL instead of a whole restart cycle.

The keys and the write itself come from `adapters/queue/capabilities`; this
class only adds the refresh loop and drops the lease on shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import structlog

from captcha.adapters.queue.capabilities import (
    HEARTBEAT_TTL_SECONDS,
    CapabilityRegistry,
    capability_key,
    heartbeat_key,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

LANE_HEARTBEAT_TTL_SEC = HEARTBEAT_TTL_SECONDS
# Three beats per TTL: one lost beat still leaves the worker routable.
LANE_HEARTBEAT_INTERVAL_SEC = 30

# Canonical names, re-exported for the worker's own logs/tests.
lane_key = heartbeat_key
cap_key = capability_key


def _type_values(types: Iterable[Any]) -> list[Any]:
    return [t.value if hasattr(t, "value") else str(t) for t in types]


class LaneHeartbeat:
    """Refresh loop for one worker's lease; also the one-shot `beat()`."""

    def __init__(
        self,
        redis: Redis,
        *,
        worker_id: str,
        lane: str = "browser",
        prefix: str = "captcha",
        types: Iterable[Any] = (),
        ttl_sec: int = LANE_HEARTBEAT_TTL_SEC,
        interval_sec: int | None = None,
    ) -> None:
        self._redis = redis
        self._worker_id = worker_id
        self._lane = lane
        self._prefix = prefix
        self._ttl = ttl_sec
        self._interval = interval_sec or max(5, ttl_sec // 3)
        self._types: list[Any] = _type_values(types)
        self._registry = CapabilityRegistry(redis, prefix=prefix, ttl=ttl_sec)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._log = log.bind(lane=lane, worker=worker_id)

    @property
    def lane_key(self) -> str:
        return lane_key(self._prefix, self._lane, self._worker_id)  # type: ignore[arg-type]

    @property
    def cap_key(self) -> str:
        return cap_key(self._prefix, self._lane)  # type: ignore[arg-type]

    @property
    def types(self) -> tuple[Any, ...]:
        return tuple(self._types)

    def set_types(self, types: Iterable[Any]) -> None:
        """Replace the advertised set; the next beat publishes it."""
        self._types = _type_values(types)

    async def beat(self) -> bool:
        """Write the lease and capability set. Never raises at the caller."""
        try:
            # CapabilityRegistry calls SETEX (not SET EX): a crashed worker's
            # key has to expire on its own.
            await self._registry.heartbeat(self._lane, self._worker_id, self._types)  # type: ignore[arg-type]
        except Exception as exc:  # a lost beat must not kill the worker
            self._log.warning("heartbeat.failed", error=str(exc))
            return False
        return True

    async def start(self) -> None:
        if self._task is not None:
            return
        await self.beat()
        self._task = asyncio.create_task(self._loop(), name=f"heartbeat-{self._worker_id}")
        self._log.info("heartbeat.start", key=self.lane_key, ttl_sec=self._ttl, interval_sec=self._interval)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                await self.beat()
            except asyncio.CancelledError:
                raise

    async def stop(self) -> None:
        """Stop beating and drop the lease so the gateway stops routing to us."""
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        with contextlib.suppress(Exception):
            await self._registry.clear(self._lane, self._worker_id)  # type: ignore[arg-type]
        self._log.info("heartbeat.stop", key=self.lane_key)
