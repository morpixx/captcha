"""CLI entry.

  python -m captcha.services.pipeline.cli                 # daemon, all jobs
  python -m captcha.services.pipeline.cli --once sample   # one sample run, exit
  python -m captcha.services.pipeline.cli --once drift    # one drift scan, exit
  python -m captcha.services.pipeline.cli --once retrain --type turnstile --reason manual
  python -m captcha.services.pipeline.cli --once all      # one of each, in order
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

import structlog

from captcha.config import get_settings
from captcha.adapters.storage import init_engine, shutdown_engine
from captcha.observability import setup_logging

from captcha.services.pipeline.drift import DriftMonitor
from captcha.services.pipeline.retrain import RetrainTrigger
from captcha.services.pipeline.s3client import S3Writer
from captcha.services.pipeline.sampler import Sampler
from captcha.services.pipeline.scheduler import PipelineScheduler

logger = structlog.get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser("captcha-pipeline")
    p.add_argument(
        "--once",
        choices=("sample", "drift", "retrain", "all"),
        default=None,
        help="run one job and exit instead of starting the daemon",
    )
    p.add_argument("--type", default="", help="(retrain only) captcha type to request retrain for")
    p.add_argument("--reason", default="manual", help="(retrain only) reason string")
    return p.parse_args()


async def _run_sample(sampler: Sampler) -> int:
    report = await sampler.run_once()
    logger.info(
        "pipeline.sample.completed",
        total=report.total_sampled,
        types=len(report.per_type),
        failures=len(report.failures),
        duration_sec=round(report.finished_at - report.started_at, 2),
    )
    return 0 if not report.failures else 1


async def _run_drift(monitor: DriftMonitor, retrain: RetrainTrigger, *, auto_retrain: bool) -> int:
    report = await monitor.run_once()
    logger.info(
        "pipeline.drift.completed",
        flags=len(report.flags),
        duration_sec=round(report.finished_at - report.started_at, 2),
    )
    if auto_retrain and report.flags:
        for f in report.flags:
            await retrain.trigger(
                f.type_,
                reason=f"drift: {','.join(f.reasons)}",
                extra={"recent_rate": f.recent_rate, "baseline_rate": f.baseline_rate, "recent_n": f.recent_n},
            )
    return 0


async def _run_retrain(retrain: RetrainTrigger, *, type_: str, reason: str) -> int:
    ok = await retrain.trigger(type_, reason=reason)
    return 0 if ok else 1


async def _daemon(
    *,
    sampler: Sampler,
    monitor: DriftMonitor,
    retrain: RetrainTrigger,
    sample_interval: int,
    drift_interval: int,
    auto_retrain: bool,
) -> None:
    sched = PipelineScheduler()

    async def sample_job() -> None:
        try:
            await _run_sample(sampler)
        except Exception as exc:  # noqa: BLE001 — scheduler eats raises silently
            logger.exception("pipeline.sample.crashed", error=str(exc))

    async def drift_job() -> None:
        try:
            await _run_drift(monitor, retrain, auto_retrain=auto_retrain)
        except Exception as exc:  # noqa: BLE001
            logger.exception("pipeline.drift.crashed", error=str(exc))

    sched.add_job("sample", interval_sec=sample_interval, job=sample_job)
    sched.add_job("drift", interval_sec=drift_interval, job=drift_job)

    # run each once on boot — a broken config fails loud, not after an interval
    await sample_job()
    await drift_job()

    sched.start()

    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        logger.info("pipeline.signal.received")
        sched.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows can't deliver SIGTERM to an asyncio loop; SIGINT works.
            pass

    try:
        await sched.wait_shutdown()
    finally:
        sched.shutdown()


async def _main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    setup_logging(level=settings.log_level, json=settings.log_json)
    init_engine(settings.postgres_url, pool_size=max(2, settings.postgres_pool_size // 8))

    s3 = S3Writer.from_settings(settings)
    sampler = Sampler.from_env(s3)
    monitor = DriftMonitor.from_env()
    retrain = RetrainTrigger.from_env(s3)

    auto_retrain = _bool_env("PIPELINE_RETRAIN_ON_DRIFT", default=False)
    sample_interval = int(os.getenv("PIPELINE_SAMPLE_INTERVAL_SEC", "3600"))
    drift_interval = int(os.getenv("PIPELINE_DRIFT_INTERVAL_SEC", "900"))

    try:
        if args.once == "sample":
            return await _run_sample(sampler)
        if args.once == "drift":
            return await _run_drift(monitor, retrain, auto_retrain=auto_retrain)
        if args.once == "retrain":
            if not args.type:
                logger.error("pipeline.retrain.missing_type")
                return 2
            return await _run_retrain(retrain, type_=args.type, reason=args.reason)
        if args.once == "all":
            s = await _run_sample(sampler)
            d = await _run_drift(monitor, retrain, auto_retrain=auto_retrain)
            return s or d
        await _daemon(
            sampler=sampler,
            monitor=monitor,
            retrain=retrain,
            sample_interval=sample_interval,
            drift_interval=drift_interval,
            auto_retrain=auto_retrain,
        )
        return 0
    finally:
        await shutdown_engine()


def main() -> int:
    args = _parse_args()
    return asyncio.run(_main_async(args))


def _bool_env(name: str, *, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    sys.exit(main())
