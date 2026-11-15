#!/usr/bin/env python3
"""Container healthcheck for the queue workers.

Docker restarts a dead process on its own. This exists for the failure Docker
cannot see: the process is up but its dependencies are gone, or the browser it
needs was never installed in the image. A worker that cannot reach Redis is not
draining a stream, and a browser-lane worker with no Camoufox build fails the
first hCaptcha task instead of failing the container.

Exit 0 healthy, 1 unhealthy; the reason goes to stderr, which is what
`docker inspect` shows for the last failed check.

    captcha-healthcheck --redis
    captcha-healthcheck --redis --browser
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

REDIS_TIMEOUT_SEC = 3.0


def _redis_url() -> str:
    # Same default as captcha.config.Settings; importing the Settings model here
    # would pull pydantic-settings into a check that must stay cheap.
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


async def _ping_redis(url: str) -> str:
    from redis.asyncio import Redis

    client = Redis.from_url(url, socket_connect_timeout=REDIS_TIMEOUT_SEC, socket_timeout=REDIS_TIMEOUT_SEC)
    try:
        if not await client.ping():
            raise RuntimeError("PING returned false")
    finally:
        await client.aclose()
    return "redis ok"


def _check_redis() -> str:
    url = _redis_url()
    try:
        return asyncio.run(asyncio.wait_for(_ping_redis(url), timeout=REDIS_TIMEOUT_SEC * 2))
    except Exception as exc:
        raise SystemExit(f"redis unreachable at {url}: {type(exc).__name__}: {exc}") from None


def _check_browser() -> str:
    try:
        from camoufox.pkgman import camoufox_path, launch_path
    except ImportError as exc:
        raise SystemExit(f"camoufox is not installed in this image: {exc}") from None

    # camoufox_path(download_if_missing=False) is deliberate: the default would
    # fetch ~2.5 GB from GitHub inside a healthcheck.
    try:
        executable = launch_path(camoufox_path(download_if_missing=False))
    except Exception as exc:
        raise SystemExit(f"camoufox not installed: {type(exc).__name__}: {exc}") from None
    if not os.access(executable, os.X_OK):
        raise SystemExit(f"camoufox binary is not executable: {executable}")

    server = "/app/src/captcha/adapters/hcaptcha_js/server.js"
    modules = "/app/src/captcha/adapters/hcaptcha_js/node_modules"
    if not os.path.isfile(server):
        raise SystemExit(f"hcaptcha bundle worker missing: {server}")
    if not os.path.isdir(modules):
        raise SystemExit(f"hcaptcha bundle worker dependencies missing: {modules}")
    return f"camoufox ok ({executable})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="captcha-healthcheck",
        description="Docker healthcheck for the captcha queue workers (see the module docstring).",
    )
    parser.add_argument("--redis", action="store_true", help="ping REDIS_URL")
    parser.add_argument(
        "--browser", action="store_true", help="assert the Camoufox build and Node bundle are installed"
    )
    args = parser.parse_args(argv)
    if not (args.redis or args.browser):
        parser.error("nothing to check: pass --redis and/or --browser")

    started = time.monotonic()
    parts: list[str] = []
    if args.redis:
        parts.append(_check_redis())
    if args.browser:
        parts.append(_check_browser())
    print(f"healthy: {', '.join(parts)} ({time.monotonic() - started:.2f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
