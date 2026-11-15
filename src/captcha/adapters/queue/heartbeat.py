"""Lane heartbeat: a lease key refreshed on a timer, plus the lane's type set.

Contract §2: a worker proves it is alive with

    SETEX captcha:lane:<lane>:<worker> 90 1
    SADD  captcha:cap:<lane> <types>

and the gateway sells a type only while its lane has a fresh heartbeat. The
key layout and the write live in `capabilities.CapabilityRegistry`; this class
adds the 30 s refresh timer and drops the lease on shutdown, so a drained worker
stops being routed to immediately instead of after one TTL.

The lane is a constructor argument: cv, protocol and http share one loop instead
of three near-identical ones. A lost beat never propagates to the worker — every
method logs and returns.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING

import structlog

from captcha.adapters.queue.capabilities import HEARTBEAT_TTL_SECONDS, CapabilityRegistry
from captcha.domain.enums import CaptchaType
from captcha.domain.lanes import Lane

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

#: Three beats per 90 s TTL: one lost beat still leaves the worker routable.
REFRESH_INTERVAL_SEC: int = 30

TypeItem = CaptchaType | str
SleepFn = Callable[[float], Awaitable[None]]


def type_values(types: Iterable[TypeItem]) -> list[TypeItem]:
    return [t.value if isinstance(t, CaptchaType) else str(t) for t in types]


class LaneHeartbeat:
    """Refresh loop for one worker's lease; also the one-shot `beat()`."""

    def __init__(
        self,
        redis: Redis,
        *,
        lane: Lane,
        worker_id: str,
        prefix: str = "captcha",
        types: Iterable[TypeItem] = (),
        ttl_sec: int = HEARTBEAT_TTL_SECONDS,
        interval_sec: float | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self._worker_id = worker_id
        self._lane = lane
        self._ttl = ttl_sec
        self._interval = interval_sec if interval_sec is not None else REFRESH_INTERVAL_SEC
        self._types = type_values(types)
        # Injected in tests: a fake clock keeps the refresh loop deterministic.
        self._sleep = sleep or asyncio.sleep
        self._registry = CapabilityRegistry(redis, prefix=prefix, ttl=ttl_sec)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._log = log.bind(lane=lane, worker=worker_id)

    @property
    def lane(self) -> Lane:
        return self._lane

    @property
    def ttl_sec(self) -> int:
        return self._ttl

    @property
    def interval_sec(self) -> float:
        return self._interval

    @property
    def types(self) -> tuple[TypeItem, ...]:
        return tuple(self._types)

    def set_types(self, types: Iterable[TypeItem]) -> None:
        """Replace the advertised set; the next beat publishes it."""
        self._types = type_values(types)

    async def beat(self) -> bool:
        """Write the lease and capability set. Never raises at the caller."""
        try:
            # heartbeat() uses SETEX (not SET EX): a crashed worker's key has to
            # expire on its own, without a shutdown hook.
            await self._registry.heartbeat(self._lane, self._worker_id, self._types)
        except Exception as exc:  # a lost beat must not kill the worker
            self._log.warning("heartbeat.failed", error=str(exc))
            return False
        return True

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        await self.beat()
        self._task = asyncio.create_task(self._loop(), name=f"heartbeat-{self._lane}-{self._worker_id}")
        self._log.info(
            "heartbeat.start",
            ttl_sec=self._ttl,
            interval_sec=self._interval,
            types=len(self._types),
        )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._sleep(self._interval)
            except asyncio.CancelledError:
                raise
            if self._stop.is_set():
                break
            await self.beat()

    async def stop(self) -> None:
        """Stop beating and drop the lease so the gateway stops routing to us."""
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        with contextlib.suppress(Exception):
            await self._registry.clear(self._lane, self._worker_id)
        self._log.info("heartbeat.stop")
