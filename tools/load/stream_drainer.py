"""Drain the lane streams and answer instantly, from outside the stack.

The sibling tools answer tasks *inside* a process: `fake_solver` is a Solver the
local `devstack` workers pick up, and `run.py` is the driver. This one is the
external variant — it needs nothing but a reachable Redis, so it can finish
tasks behind a deployed compose while an outside generator (k6, locust) pushes
`createTask`:

  read envelope -> set processing -> set ready -> ack

By default the Postgres mirror is skipped because `getTaskResult` reads the
Redis hash first; pass `--db` when the load test is meant to stress storage as
well. No captcha vendor, model, browser or network egress is involved.

    python tools/load/stream_drainer.py --redis redis://127.0.0.1:6379/0

Run it from the repository root with `PYTHONPATH=src` (or `uv run`).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import time

import msgspec
import redis.asyncio as aioredis

from captcha.adapters.queue import Consumer, TaskStateStore
from captcha.adapters.queue.keys import LANES
from captcha.adapters.storage import TaskRepo, init_engine, shutdown_engine
from captcha.config import Settings
from captcha.domain.catalog import SolutionKind, solution_kind
from captcha.domain.enums import SolveMode
from captcha.domain.task import SolverResult, TaskEnvelope

log = logging.getLogger("stream_drainer")


def result_for(envelope: TaskEnvelope) -> SolverResult:
    """A plausible result in the shape each type projects onto.

    The default load type is text_captcha; the mapping keeps the fake usable for
    mixed-type smoke runs without inventing a vendor.
    """
    kind = solution_kind(envelope.task.type)
    if kind is SolutionKind.TEXT:
        return SolverResult(text="loadtest", solve_mode=SolveMode.HTTP)
    if kind is SolutionKind.ROTATE:
        return SolverResult(angle=0.0, solve_mode=SolveMode.HTTP)
    if kind is SolutionKind.SLIDER:
        return SolverResult(offset=42.0, solve_mode=SolveMode.HTTP)
    if kind is SolutionKind.GRID:
        return SolverResult(indices=[0, 1], solve_mode=SolveMode.HTTP)
    if kind is SolutionKind.BBOX:
        return SolverResult(bbox=(1.0, 2.0, 3.0, 4.0), solve_mode=SolveMode.HTTP)
    if kind is SolutionKind.COORDS:
        return SolverResult(coords=[(10.0, 20.0)], solve_mode=SolveMode.HTTP)
    if kind is SolutionKind.GEETEST_V3:
        return SolverResult(
            token="loadtest",
            extra={"challenge": "loadtest", "validate": "loadtest"},
            solve_mode=SolveMode.HTTP,
        )
    return SolverResult(token="loadtest", solve_mode=SolveMode.HTTP)


class StreamDrainer:
    def __init__(
        self,
        *,
        redis: aioredis.Redis,
        prefix: str,
        lane: str,
        name: str,
        store_pg: bool,
        sessionmaker: object | None,
    ) -> None:
        self.name = name
        self.consumer = Consumer(redis, lane=lane, consumer_name=name, prefix=prefix, block_ms=1000, count=256)
        self.store = TaskStateStore(redis, prefix=prefix)
        self.store_pg = store_pg
        self.sessionmaker = sessionmaker
        self.handled = 0
        self.stop = asyncio.Event()

    async def run(self) -> None:
        await self.consumer.ensure_group()
        log.info("stream_drainer.started lane=%s consumer=%s", self.consumer.lane, self.name)
        while not self.stop.is_set():
            try:
                batch = await self.consumer.read()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("stream_drainer.read_failed")
                await asyncio.sleep(0.5)
                continue
            for message_id, envelope in batch:
                await self.handle(message_id, envelope)

    async def handle(self, message_id: str, envelope: TaskEnvelope) -> None:
        now = time.time()
        task_id = envelope.task.id
        try:
            result = result_for(envelope)
            await self.store.set_processing(task_id, self.name, started_at=now)
            await self.store.set_ready(task_id, result, finished_at=now)
            if self.store_pg and self.sessionmaker is not None:
                await self.persist(task_id, result)
        except Exception:
            log.exception("stream_drainer.handle_failed task=%s", task_id)
            return
        await self.consumer.ack(message_id)
        self.handled += 1
        if self.handled % 5000 == 0:
            log.info("stream_drainer.progress handled=%d lane=%s", self.handled, self.consumer.lane)

    async def persist(self, task_id: object, result: SolverResult) -> None:
        async with self.sessionmaker() as session:  # type: ignore[misc]
            repo = TaskRepo(session)
            await repo.update_status(task_id, "ready", worker_id=self.name)
            await repo.update_result(task_id, result=msgspec.to_builtins(result))
            await session.commit()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Drain the lane streams and answer tasks with fake results")
    parser.add_argument("--redis", default="redis://127.0.0.1:6379/0", help="Redis URL")
    parser.add_argument("--prefix", default="captcha", help="Redis stream prefix")
    parser.add_argument("--lanes", default=",".join(LANES), help="Comma-separated lanes to consume")
    parser.add_argument("--db", action="store_true", help="Also mirror results into Postgres")
    parser.add_argument("--postgres", default=None, help="Postgres URL (defaults to Settings)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = Settings() if not args.postgres else Settings(POSTGRES_URL=args.postgres)
    redis = aioredis.from_url(args.redis, decode_responses=False)

    sessionmaker = None
    if args.db:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        engine = init_engine(settings.postgres_url, pool_size=10)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    lanes = [lane.strip() for lane in args.lanes.split(",") if lane.strip()]
    workers = [
        StreamDrainer(
            redis=redis,
            prefix=args.prefix,
            lane=lane,
            name=f"drainer-{lane}-{index}",
            store_pg=args.db,
            sessionmaker=sessionmaker,
        )
        for index, lane in enumerate(lanes)
    ]

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, lambda: [worker.stop.set() for worker in workers])

    try:
        await asyncio.gather(*(worker.run() for worker in workers))
    finally:
        await redis.aclose()
        if args.db:
            await shutdown_engine()
        total = sum(worker.handled for worker in workers)
        log.info("stream_drainer.stopped handled=%d", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
