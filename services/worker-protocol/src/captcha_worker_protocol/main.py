"""CLI entry point for the protocol-lane worker."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import structlog
from captcha_common.config import get_settings
from captcha_observability import setup_logging, setup_metrics

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
        prog="captcha-worker-protocol",
        description=f"{LANE}-lane captcha worker (PoW, sensor builds, pure CPU)",
    )
    p.add_argument(
        "--consumers",
        type=int,
        default=int(os.environ.get("WORKER_PROTOCOL_CONSUMERS", "2")),
        help="parallel Worker instances in this process",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=int(os.environ.get("WORKER_PROTOCOL_CONCURRENCY", str(DEFAULT_CONCURRENCY))),
        help="in-flight cap per worker",
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
        default=os.environ.get("HOSTNAME", "worker-protocol"),
        help="prefix for XREADGROUP consumer names",
    )
    return p.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup_logging(level=settings.log_level, json=settings.log_json)
    setup_metrics()
    log = structlog.get_logger("captcha.worker.protocol.main")

    loop_name = _install_loop_policy()
    log.info("loop.installed", policy=loop_name)
    log.info(
        "config.loaded",
        consumers=args.consumers,
        concurrency=args.concurrency,
        redis_prefix=settings.redis_stream_prefix,
    )

    workers: list[Worker] = [
        Worker(
            settings,
            worker_id=f"{args.worker_id_prefix}-{i}",
            concurrency=args.concurrency,
            batch_size=args.batch_size,
            block_ms=args.block_ms,
            max_retries=args.max_retries,
        )
        for i in range(args.consumers)
    ]

    try:
        await asyncio.gather(*(w.run() for w in workers))
    except asyncio.CancelledError:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(argv)
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
