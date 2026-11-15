"""apscheduler wrapper — one AsyncIOScheduler, one interval job, graceful shutdown."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = structlog.get_logger(__name__)


class CanaryScheduler:
    def __init__(
        self,
        *,
        interval_sec: int,
        job: Callable[[], Awaitable[None]],
        job_id: str = "canary-run",
    ) -> None:
        self._scheduler = AsyncIOScheduler()
        self._interval_sec = interval_sec
        self._job = job
        self._job_id = job_id
        self._stop = asyncio.Event()

    def start(self) -> None:
        # first run after one interval; call `self._job()` once before `start()` if
        # you want a run on boot — main.py does.
        self._scheduler.add_job(
            self._job,
            trigger=IntervalTrigger(seconds=self._interval_sec),
            id=self._job_id,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=max(60, self._interval_sec // 4),
        )
        self._scheduler.start()
        logger.info("canary.scheduler.started", interval_sec=self._interval_sec)

    async def wait_shutdown(self) -> None:
        await self._stop.wait()

    def request_stop(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("canary.scheduler.stopped")
