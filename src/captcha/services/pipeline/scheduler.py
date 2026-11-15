"""apscheduler wrapper - one AsyncIOScheduler, interval jobs, graceful shutdown.

apscheduler is imported lazily. The one-shot CLI paths (--once sample|drift|
retrain|golden) and the golden harness do not need a scheduler, and the package
must stay importable when the dependency is absent - otherwise every test that
touches captcha.services.pipeline dies on an import it never uses.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from captcha.domain.errors import ConfigError

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = structlog.get_logger(__name__)


class PipelineScheduler:
    def __init__(self) -> None:
        self._scheduler: Any = _make_scheduler()
        self._stop = asyncio.Event()
        self._jobs: list[tuple[str, int]] = []

    def add_job(
        self,
        name: str,
        *,
        interval_sec: int,
        job: Callable[[], Awaitable[None]],
    ) -> None:
        if interval_sec <= 0:
            logger.info("pipeline.scheduler.skip", job=name, reason="interval<=0")
            return
        from apscheduler.triggers.interval import IntervalTrigger

        self._scheduler.add_job(
            job,
            trigger=IntervalTrigger(seconds=interval_sec),
            id=name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=max(60, interval_sec // 4),
        )
        self._jobs.append((name, interval_sec))

    def start(self) -> None:
        self._scheduler.start()
        logger.info("pipeline.scheduler.started", jobs=self._jobs)

    async def wait_shutdown(self) -> None:
        await self._stop.wait()

    def request_stop(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("pipeline.scheduler.stopped")


def _make_scheduler() -> AsyncIOScheduler:
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError as exc:
        raise ConfigError(
            "apscheduler is not installed - daemon mode needs it; --once modes do not",
            context={"hint": "add apscheduler to dependencies"},
        ) from exc
    return AsyncIOScheduler()
