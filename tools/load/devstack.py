"""Bring up a load-testable stack on one box: real gateway + fake-solver workers.

What it starts, in one process:

    redis + postgres   assumed running (compose, or two containers — see docs)
    gateway            the real ASGI app under uvicorn, real PG + Redis adapters
    worker-http x N    the real worker loop, its real consumer group and the real
                       state store — with `LoadTestSolver` answering instead of
                       an actual captcha

So everything the driver measures is production code except the solver call
itself. No live captcha, no browser, no proxy, no outbound traffic.

    python -m tools.load.devstack --type text_captcha --redis-url redis://127.0.0.1:6399/1 \
        --postgres-url postgresql+asyncpg://captcha:captcha@127.0.0.1:55432/captcha --flush

`--flush` deletes `<prefix>:*` keys in the target Redis database only. Point it at
a throwaway database; it is off by default.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import time
from typing import Any

import structlog

from captcha.adapters.storage.persistence import TaskPersistence, session_factory_for
from captcha.config import Settings
from captcha.domain.enums import CaptchaType
from captcha.observability.metrics import setup_metrics
from captcha.services.gateway.app import create_app
from captcha.services.worker_http.concurrency import ConcurrencyLimiter
from captcha.services.worker_http.context import build_context
from captcha.services.worker_http.worker import Worker
from captcha.solvers import discover_solvers, global_registry

from . import fake_solver

_LOG = structlog.get_logger("tools.load.devstack")

#: Gateway defaults that make the load run measurable instead of quota-bound.
#: Every one of these is a per-account Redis counter; a soak would hit them.
_ENV_DEFAULTS = {
    "LOG_LEVEL": "WARNING",
    "LOG_JSON": "false",
    "RATE_LIMIT_CREATE_PER_MIN": "0",
    "MAX_ACTIVE_TASKS_PER_ACCOUNT": "0",
    "MAX_DAILY_TASKS": "0",
    "MAX_DAILY_SPEND": "0",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.load.devstack",
        description="Real gateway + fake-solver worker lane for load tests.",
    )
    p.add_argument("--host", default="127.0.0.1", help="gateway bind address")
    p.add_argument("--port", type=int, default=8080, help="gateway port")
    p.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
    p.add_argument(
        "--postgres-url",
        default=os.environ.get("POSTGRES_URL", "postgresql+asyncpg://captcha:captcha@localhost:5432/captcha"),
    )
    p.add_argument("--prefix", default="captcha", help="redis stream prefix")
    p.add_argument("--type", dest="captcha_type", default="text_captcha", help="type the fake solver claims")
    p.add_argument("--workers", type=int, default=4, help="worker loops sharing one consumer group")
    p.add_argument("--concurrency", type=int, default=1024, help="in-flight cap across all worker loops")
    p.add_argument("--batch-size", type=int, default=64, help="XREADGROUP COUNT per poll")
    p.add_argument("--fake-latency-ms", type=float, default=fake_solver.DEFAULT_LATENCY_MS)
    p.add_argument("--fake-jitter-ms", type=float, default=fake_solver.DEFAULT_JITTER_MS)
    p.add_argument("--run-for", type=float, default=0.0, help="stop after N seconds (0: until Ctrl+C)")
    p.add_argument("--flush", action="store_true", help="delete <prefix>:* keys in this redis db first")
    p.add_argument("--no-gateway", action="store_true", help="start only the fake workers")
    return p


def _apply_env_defaults() -> None:
    for key, value in _ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


async def _flush_prefix(redis: Any, prefix: str) -> int:
    keys = [key async for key in redis.scan_iter(match=f"{prefix}:*")]
    if not keys:
        return 0
    return int(await redis.delete(*keys))


async def _wait_started(server: Any, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not getattr(server, "started", False):
        if time.monotonic() > deadline:
            raise RuntimeError("gateway did not start within 30s")
        await asyncio.sleep(0.05)


async def run_stack(args: argparse.Namespace) -> int:
    _apply_env_defaults()
    settings = Settings(
        redis_url=args.redis_url,
        postgres_url=args.postgres_url,
        redis_stream_prefix=args.prefix,
    )
    setup_metrics()
    try:
        captcha_type = CaptchaType(args.captcha_type)
    except ValueError:
        print(f"unknown captcha type: {args.captcha_type}", file=sys.stderr)
        return 2

    discover_solvers()
    fake_solver.configure(latency_ms=args.fake_latency_ms, jitter_ms=args.fake_jitter_ms)
    fake_solver.register(captcha_type)
    print(
        f"fake solver: {captcha_type.value} latency={args.fake_latency_ms}ms+{args.fake_jitter_ms}ms jitter",
        flush=True,
    )

    app = create_app(settings)
    server = None
    server_task: asyncio.Task[Any] | None = None
    if not args.no_gateway:
        import uvicorn

        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            log_level="warning",
            access_log=False,
            lifespan="on",
        )
        server = uvicorn.Server(config)
        server_task = asyncio.create_task(server.serve(), name="gateway")
        await _wait_started(server)
        print(f"gateway: http://{args.host}:{args.port}", flush=True)

    state = app.state.ctx
    if args.flush:
        flushed = await _flush_prefix(state.redis, args.prefix)
        print(f"flushed {flushed} keys under {args.prefix}:*", flush=True)

    bundle = await build_context(settings, redis=state.redis, pg=state.engine, logger=_LOG)
    persistence = TaskPersistence(session_factory_for(state.engine))
    limiter = ConcurrencyLimiter(args.concurrency)

    workers = [
        Worker(
            worker_id=f"load-{i}",
            redis=state.redis,
            registry=global_registry(),
            state=state.state_store,
            bundle=bundle,
            limiter=limiter,
            persistence=persistence,
            settings=settings,
            consumer_count=args.batch_size,
            block_ms=100,
            max_retries=3,
        )
        for i in range(max(1, args.workers))
    ]
    for worker in workers:
        await worker.ensure_group()
    worker_tasks = [asyncio.create_task(worker.run(), name=f"fake-worker-{i}") for i, worker in enumerate(workers)]
    print(f"workers: {len(workers)} loop(s), concurrency={args.concurrency}", flush=True)

    try:
        if args.run_for > 0:
            await asyncio.sleep(args.run_for)
        else:
            print("running until Ctrl+C", flush=True)
            while True:
                await asyncio.sleep(1.0)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("stopping", flush=True)
        for worker in workers:
            worker._running.clear()
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        await asyncio.gather(*(worker.shutdown() for worker in workers), return_exceptions=True)
        await bundle.aclose()
        if server is not None and server_task is not None:
            server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server_task, timeout=15.0)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run_stack(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
