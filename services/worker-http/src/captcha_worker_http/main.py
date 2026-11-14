"""CLI entry point for the HTTP-lane worker.

Boot sequence:
    1. install uvloop (winloop on Windows)
    2. parse argv
    3. structlog + prometheus init
    4. discover_solvers() — loads every solver published via the
       `captcha.solvers` entry-point group. Log what we got.
    5. open redis + (optional) pg
    6. build the shared SolverContext bundle
    7. spawn N Worker tasks sharing one ConcurrencyLimiter
    8. install SIGTERM/SIGINT handlers that flip _running off
    9. asyncio.gather on them; await graceful shutdown
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from typing import Any

import structlog

from captcha_common.config import get_settings
from captcha_observability import setup_logging, setup_metrics
from captcha_queue import TaskStateStore
from captcha_solver_base import discover_solvers, global_registry

from .concurrency import ConcurrencyLimiter
from .context import build_context
from .stats import SuccessRateTracker
from .worker import Worker


def _install_loop_policy() -> str:
    """uvloop on *nix, winloop on Windows. Returns the policy name we installed."""
    if sys.platform == "win32":
        try:
            import winloop  # type: ignore[import-not-found]
            winloop.install()
            return "winloop"
        except ImportError:
            return "asyncio"
    try:
        import uvloop  # type: ignore[import-not-found]
        uvloop.install()
        return "uvloop"
    except ImportError:
        return "asyncio"


def _parse_argv(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="captcha-worker-http", description="HTTP-lane captcha worker")
    p.add_argument(
        "--consumers",
        type=int,
        default=int(os.environ.get("WORKER_HTTP_CONSUMERS", "8")),
        help="parallel XREADGROUP consumers sharing the concurrency cap",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="global in-flight cap (defaults to WORKER_HTTP_CONCURRENCY)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=16,
        help="XREADGROUP COUNT per poll",
    )
    p.add_argument(
        "--block-ms",
        type=int,
        default=5_000,
        help="XREADGROUP BLOCK",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="retries before an envelope is dead-lettered",
    )
    p.add_argument(
        "--worker-id-prefix",
        default=os.environ.get("HOSTNAME", "worker-http"),
        help="prefix for XREADGROUP consumer names",
    )
    return p.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup_logging(level=settings.log_level, json=settings.log_json)
    setup_metrics()
    log = structlog.get_logger("captcha.worker.http.main")

    loop_name = _install_loop_policy()
    log.info("loop.installed", policy=loop_name)

    concurrency = args.concurrency or settings.worker_http_concurrency
    log.info(
        "config.loaded",
        consumers=args.consumers,
        concurrency=concurrency,
        batch_size=args.batch_size,
        redis_prefix=settings.redis_stream_prefix,
    )

    # ---- discover solvers --------------------------------------------------
    registry = discover_solvers(registry=global_registry())
    solver_types = sorted({t.value for cls in registry.all_solvers() for t in cls.info.types})
    solver_names = sorted({cls.info.name for cls in registry.all_solvers()})
    log.info(
        "solvers.discovered",
        count=len(solver_names),
        names=solver_names,
        types=solver_types,
    )
    if not solver_names:
        log.warning("solvers.empty", hint="no captcha.solvers entry points found")

    # ---- redis -------------------------------------------------------------
    try:
        from redis.asyncio import Redis
    except ImportError as exc:  # pragma: no cover
        log.error("redis.import_failed", error=str(exc))
        return 2

    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    try:
        await redis.ping()
    except Exception as exc:  # noqa: BLE001
        log.error("redis.ping_failed", error=str(exc), url=settings.redis_url)
        await redis.aclose()
        return 2
    log.info("redis.connected", url=settings.redis_url)

    # ---- optional pg -------------------------------------------------------
    # `init_engine` is sync — it builds the SQLAlchemy AsyncEngine but doesn't
    # connect until the first checkout. A ping is deferred to the first query;
    # failing lazily here keeps a fresh dev box from hard-crashing the worker.
    pg: Any = None
    try:
        from captcha_db import init_engine
        pg = init_engine(
            settings.postgres_url,
            pool_size=settings.postgres_pool_size,
        )
        log.info("pg.engine_ready", pool_size=settings.postgres_pool_size)
    except Exception as exc:  # noqa: BLE001
        log.warning("pg.unavailable", error=str(exc))
        pg = None

    # ---- context, state, stats, limiter -----------------------------------
    bundle = await build_context(settings, redis=redis, pg=pg, logger=log)
    state = TaskStateStore(redis, prefix=settings.redis_stream_prefix)
    stats = SuccessRateTracker(
        redis,
        prefix=settings.redis_stream_prefix,
        window_sec=settings.http_fallback_window_sec,
    )
    limiter = ConcurrencyLimiter(concurrency)

    # ---- workers -----------------------------------------------------------
    workers: list[Worker] = []
    for i in range(args.consumers):
        w = Worker(
            worker_id=f"{args.worker_id_prefix}-{i}",
            redis=redis,
            registry=registry,
            state=state,
            bundle=bundle,
            limiter=limiter,
            stats=stats,
            settings=settings,
            consumer_count=args.batch_size,
            block_ms=args.block_ms,
            max_retries=args.max_retries,
        )
        await w.ensure_group()
        workers.append(w)

    # ---- signal handling ---------------------------------------------------
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _stop(sig: str) -> None:
        if not stop_event.is_set():
            log.info("signal.received", signal=sig)
            stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop, sig.name)
        except (NotImplementedError, RuntimeError):
            # Windows: add_signal_handler isn't supported on the proactor loop
            # for non-SIGINT signals; fall back to signal.signal.
            def _handler(signum: int, _frame: Any) -> None:
                _stop(signal.Signals(signum).name)
            try:
                signal.signal(sig, _handler)
            except (OSError, ValueError):
                pass

    # ---- run ---------------------------------------------------------------
    worker_tasks = [asyncio.create_task(w.run(), name=f"worker-{w._id}") for w in workers]

    await stop_event.wait()
    log.info("shutdown.start")
    for w in workers:
        w._running.clear()
    for t in worker_tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*worker_tasks, return_exceptions=True)
    await asyncio.gather(*(w.shutdown() for w in workers), return_exceptions=True)

    await bundle.aclose()
    try:
        await redis.aclose()
    except Exception:  # noqa: BLE001
        pass
    if pg is not None:
        try:
            from captcha_db import shutdown_engine
            await shutdown_engine()
        except Exception as exc:  # noqa: BLE001
            log.warning("pg.shutdown_failed", error=str(exc))

    log.info("shutdown.done")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(argv)
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
