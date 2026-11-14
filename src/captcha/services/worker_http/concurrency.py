"""Semaphore-based concurrency limiter + gauge."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ConcurrencyLimiter:
    """Thin wrapper over `asyncio.Semaphore` that tracks current in-flight count.

    Use via `async with limiter.slot(): ...`. `in_flight` and `capacity` feed
    the `captcha_active_tasks` gauge without racing the semaphore internals.
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._sem = asyncio.Semaphore(capacity)
        self._in_flight = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def available(self) -> int:
        # Semaphore._value is the public-ish fastpath; fall back to capacity - in_flight.
        return max(0, self._capacity - self._in_flight)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self._sem.acquire()
        self._in_flight += 1
        try:
            yield
        finally:
            self._in_flight -= 1
            self._sem.release()
