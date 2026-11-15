"""HTTP-lane heartbeat: the lease key the gateway gates sales on.

Contract §2: a live worker is one short-TTL Redis key,

    SETEX captcha:lane:http:<worker> 90 1
    SADD  captcha:cap:http <types>

refreshed every 30 s. CapabilityRegistry.heartbeat() owns the write; this
class owns the timer and drops the lease on shutdown so a drained worker stops
being routed to immediately instead of after one TTL.

A lost beat never propagates to the worker loop: every method swallows Redis
errors and only logs them.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from typing import TYPE_CHECKING

import structlog

from captcha.adapters.queue.capabilities import HEARTBEAT_TTL_SECONDS, CapabilityRegistry
from captcha.domain.enums import CaptchaType
from captcha.domain.lanes import Lane

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

LANE: Lane = "http"
#: Three beats per 90 s TTL: one lost beat still leaves the worker routable.
BEAT_INTERVAL_SEC: int = 30


TypeItem = CaptchaType | str


def _type_values(types: Iterable[TypeItem]) -> list[TypeItem]:
    return [t.value if isinstance(t, CaptchaType) else str(t) for t in types]


class HttpLaneHeartbeat:
    """Refresh loop for one HTTP worker's lease, plus the one-shot beat()."""

    def __init__(
        self,
        redis: Redis,
        *,
        worker_id: str,
        prefix: str = "captcha",
        types: Iterable[TypeItem] = (),
        ttl_sec: int = HEARTBEAT_TTL_SECONDS,
        interval_sec: float | None = None,
    ) -> None:
        self._worker_id = worker_id
        self._ttl = ttl_sec
        self._interval = interval_sec or BEAT_INTERVAL_SEC
        self._types = _type_values(types)
        self._registry = CapabilityRegistry(redis, prefix=prefix, ttl=ttl_sec)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._log = log.bind(lane=LANE, worker=worker_id)

    @property
    def types(self) -> tuple[TypeItem, ...]:
        return tuple(self._types)

    @property
    def interval_sec(self) -> float:
        return self._interval

    def set_types(self, types: Iterable[TypeItem]) -> None:
        """Replace the advertised set; the next beat publishes it."""
        self._types = _type_values(types)

    async def beat(self) -> bool:
        """Write the lease + capability set. Never raises at the caller."""
        try:
            await self._registry.heartbeat(LANE, self._worker_id, self._types)
        except Exception as exc:  # a lost beat must not kill the worker
            self._log.warning("heartbeat.failed", error=str(exc))
            return False
        return True

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        await self.beat()
        self._task = asyncio.create_task(self._loop(), name=f"heartbeat-{LANE}-{self._worker_id}")
        self._log.info("heartbeat.start", ttl_sec=self._ttl, interval_sec=self._interval, types=len(self._types))

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
            await self._registry.clear(LANE, self._worker_id)
        self._log.info("heartbeat.stop")
