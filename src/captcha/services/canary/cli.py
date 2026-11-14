"""CLI: `--once` runs the suite once and exits (CI-friendly);
default starts an apscheduler daemon that runs on every interval."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

import structlog

from captcha.config import get_settings
from captcha.observability import setup_logging

from captcha.services.canary.alerts import WebhookAlerter
from captcha.services.canary.scheduler import CanaryScheduler
from captcha.services.canary.suite import CanarySuite

logger = structlog.get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser("captcha-canary")
    p.add_argument("--once", action="store_true", help="run suite once and exit")
    p.add_argument("--fixtures", default=None, help="override fixtures dir")
    p.add_argument("--gateway", default=None, help="override gateway URL")
    return p.parse_args()


def _build_suite(args: argparse.Namespace) -> tuple[CanarySuite, WebhookAlerter]:
    settings = get_settings()
    gateway_url = args.gateway or os.getenv("CAPTCHA_GATEWAY_URL") or (
        f"http://{settings.gateway_host}:{settings.gateway_port}"
    )
    fixtures_dir = Path(
        args.fixtures
        or os.getenv("CANARY_FIXTURES_DIR")
        or "tests/canary/fixtures"
    )
    pass_threshold = float(os.getenv("CANARY_PASS_THRESHOLD", "0.9"))
    poll_interval = float(os.getenv("CANARY_POLL_INTERVAL_SEC", "5"))
    task_timeout = float(os.getenv("CANARY_TASK_TIMEOUT_SEC", "180"))

    suite = CanarySuite(
        gateway_url=gateway_url,
        fixtures_dir=fixtures_dir,
        poll_interval_sec=poll_interval,
        task_timeout_sec=task_timeout,
    )
    alerter = WebhookAlerter(
        webhook_url=settings.canary_alert_webhook,
        pass_threshold=pass_threshold,
        gateway_url=gateway_url,
    )
    return suite, alerter


async def _run_once(suite: CanarySuite, alerter: WebhookAlerter) -> int:
    report = await suite.run_once()
    logger.info(
        "canary.run.completed",
        total=report.total,
        passed=report.passed,
        failed=report.failed,
        pass_rate=round(report.pass_rate, 4),
        p50_ms=round(report.p50_latency_ms, 1),
        p95_ms=round(report.p95_latency_ms, 1),
    )
    for r in report.results:
        if not r.ok:
            logger.warning(
                "canary.fixture.failed",
                name=r.name,
                type=r.type_,
                status=r.status,
                error=r.error,
                latency_ms=round(r.latency_ms, 1),
            )
    await alerter.maybe_alert(report)
    return 0 if report.pass_rate >= alerter.pass_threshold else 1


async def _daemon(suite: CanarySuite, alerter: WebhookAlerter, interval_sec: int) -> None:
    async def job() -> None:
        try:
            await _run_once(suite, alerter)
        except Exception as exc:  # noqa: BLE001 — scheduler eats raises silently
            logger.exception("canary.job.crashed", error=str(exc))

    sched = CanaryScheduler(interval_sec=interval_sec, job=job)
    # one run on boot so a broken deploy fails fast rather than after the first interval
    await job()
    sched.start()

    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        logger.info("canary.signal.received")
        sched.request_stop()

    # SIGTERM/SIGINT where available; on Windows only SIGINT is deliverable to asyncio loops
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            pass

    try:
        await sched.wait_shutdown()
    finally:
        sched.shutdown()


def main() -> int:
    args = _parse_args()
    settings = get_settings()
    setup_logging(level=settings.log_level, json=settings.log_json)

    suite, alerter = _build_suite(args)

    if args.once:
        return asyncio.run(_run_once(suite, alerter))

    asyncio.run(_daemon(suite, alerter, settings.canary_interval_sec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
