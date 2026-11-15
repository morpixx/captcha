"""CLI entry point for the browser-lane worker.

One `captcha.adapters.browser.Pool` is launched here and shared across all consumers
in this process. `--consumers` > 1 is still useful for XREADGROUP parallelism
even when the pool caps real concurrency.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import structlog

from captcha.adapters.browser import Pool as BrowserPool
from captcha.config import get_settings
from captcha.observability import setup_logging, setup_metrics

from .worker import DEFAULT_CONCURRENCY, LANE, Worker


def _install_loop_policy() -> str:
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
    p = argparse.ArgumentParser(
        prog="captcha-worker-browser",
        description=f"{LANE}-lane captcha worker (playwright/camoufox fallback)",
    )
    p.add_argument(
        "--consumers",
        type=int,
        default=int(os.environ.get("WORKER_BROWSER_CONSUMERS", "1")),
        help="parallel XREADGROUP consumers sharing the browser pool",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="in-flight cap per consumer (defaults to WORKER_BROWSER_SESSIONS)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=4,
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
        default=2,
        help="retries before an envelope is dead-lettered (browser is expensive, retry less)",
    )
    p.add_argument(
        "--worker-id-prefix",
        default=os.environ.get("HOSTNAME", "worker-browser"),
        help="prefix for XREADGROUP consumer names",
    )
    p.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("WORKER_BROWSER_HEADLESS", "1") != "0",
        help="run the browser headless (default) or headful",
    )
    return p.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup_logging(level=settings.log_level, json=settings.log_json)
    setup_metrics()
    log = structlog.get_logger("captcha.worker.browser.main")

    loop_name = _install_loop_policy()
    log.info("loop.installed", policy=loop_name)

    pool_size = settings.worker_browser_sessions or DEFAULT_CONCURRENCY
    log.info(
        "config.loaded",
        consumers=args.consumers,
        concurrency=args.concurrency or pool_size,
        pool_size=pool_size,
        headless=args.headless,
        redis_prefix=settings.redis_stream_prefix,
    )

    # Shared browser pool across consumers — launch once, teardown once.
    browser = BrowserPool(size=pool_size, headless=args.headless)
    await browser.launch()
    log.info("browser.pool.launched", size=browser.size, headless=args.headless)

    workers: list[Worker] = [
        Worker(
            settings,
            worker_id=f"{args.worker_id_prefix}-{i}",
            concurrency=args.concurrency or pool_size,
            batch_size=args.batch_size,
            block_ms=args.block_ms,
            max_retries=args.max_retries,
            headless=args.headless,
            browser_pool=browser,
        )
        for i in range(args.consumers)
    ]

    try:
        await asyncio.gather(*(w.run() for w in workers))
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await browser.close_all()
        except Exception as exc:
            log.warning("browser.pool.close_failed", error=str(exc))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(argv)
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
