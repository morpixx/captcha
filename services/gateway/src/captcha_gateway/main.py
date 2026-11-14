"""CLI entry: `python -m captcha_gateway.main`.

Uses uvloop on Linux/macOS; falls back to asyncio's default loop on Windows
where uvloop isn't installable. The `--workers` flag only has effect when
running under a single-process invocation; prod should front with gunicorn
and set PROMETHEUS_MULTIPROC_DIR.
"""
from __future__ import annotations

import argparse
import sys

import uvicorn

from captcha_common.config import get_settings


def _loop_for_platform() -> str:
    # uvloop wheels don't ship for Windows; let uvicorn pick asyncio there.
    if sys.platform.startswith("win"):
        return "asyncio"
    return "uvloop"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    cfg = get_settings()
    p = argparse.ArgumentParser(prog="captcha-gateway", description="captcha-solver API gateway")
    p.add_argument("--host", default=cfg.gateway_host, help="bind host (env CAPTCHA_GATEWAY_HOST)")
    p.add_argument("--port", type=int, default=cfg.gateway_port, help="bind port (env CAPTCHA_GATEWAY_PORT)")
    p.add_argument("--workers", type=int, default=cfg.gateway_workers,
                   help="uvicorn worker count (ignored when --reload is on)")
    p.add_argument("--reload", action="store_true", help="dev auto-reload")
    p.add_argument("--log-level", default=cfg.log_level.lower(),
                   choices=("debug", "info", "warning", "error", "critical"))
    return p.parse_args(argv)


def run() -> None:
    args = parse_args()
    uvicorn.run(
        "captcha_gateway.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=args.workers if not args.reload else 1,
        reload=args.reload,
        loop=_loop_for_platform(),
        log_level=args.log_level,
        access_log=False,  # structlog-driven access logs land through the app
        http="httptools",
    )


if __name__ == "__main__":
    run()
