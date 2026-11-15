"""CLI entry point for the browser-lane worker.

The provider is resolved once per process (`resolve_provider`) and shared by
every consumer; there is no process-wide Playwright context pool any more.
Each task opens its own session through `ctx.browser`, so several consumers
share a bounded factory instead of a bound context.

Consumer processes are capped at `WORKER_BROWSER_SESSIONS`: more consumers than
sessions would only oversubscribe the same RAM budget.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys

import structlog

from captcha.config import get_settings
from captcha.observability import setup_logging, setup_metrics

from .limits import DEFAULT_CONCURRENCY, LANE
from .provider import close_provider, resolve_provider, start_provider
from .worker import Worker


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
        description=f"{LANE}-lane captcha worker (per-task browser sessions)",
    )
    p.add_argument(
        "--consumers",
        type=int,
        default=int(os.environ.get("WORKER_BROWSER_CONSUMERS", "1")),
        help="parallel XREADGROUP consumers sharing one provider (capped by sessions)",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="in-flight cap per consumer (defaults to sessions / consumers)",
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

    sessions = int(settings.worker_browser_sessions or DEFAULT_CONCURRENCY)
    consumers = max(1, min(args.consumers, sessions))
    per_consumer = args.concurrency or max(1, sessions // consumers)
    log.info(
        "config.loaded",
        consumers=consumers,
        concurrency=per_consumer,
        sessions=sessions,
        headless=args.headless,
        min_free_mb=settings.worker_browser_min_free_mb,
        redis_prefix=settings.redis_stream_prefix,
    )

    provider = resolve_provider(settings, headless=args.headless)
    await start_provider(provider)
    log.info(
        "browser.provider.ready",
        provider=type(provider).__name__,
        sessions=sessions,
    )

    workers: list[Worker] = [
        Worker(
            settings,
            worker_id=f"{args.worker_id_prefix}-{i}",
            concurrency=per_consumer,
            batch_size=args.batch_size,
            block_ms=args.block_ms,
            max_retries=args.max_retries,
            headless=args.headless,
            provider=provider,
        )
        for i in range(consumers)
    ]

    try:
        await asyncio.gather(*(w.run() for w in workers))
    except asyncio.CancelledError:
        pass
    finally:
        with contextlib.suppress(Exception):
            await close_provider(provider)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(argv)
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
