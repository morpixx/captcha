"""CLI entry.

python -m captcha.services.pipeline.cli                 # daemon, all jobs
python -m captcha.services.pipeline.cli --once sample   # one sample run, exit
python -m captcha.services.pipeline.cli --once drift    # one drift scan, exit
python -m captcha.services.pipeline.cli --once retrain --type turnstile --reason manual
python -m captcha.services.pipeline.cli --once golden --manifest golden/v1/manifest.json --predict hopscotch.solver:predict
python -m captcha.services.pipeline.cli --once check-model --name hopscotch-v1
python -m captcha.services.pipeline.cli --once all      # sample + drift, in order

--once golden exits 0 when accuracy/coverage clear the thresholds (default 0.95 /
0.80 from the production contract), 1 when they do not, 2 on a broken manifest.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import os
import signal
import sys
from collections.abc import Callable
from typing import Any

import structlog

from captcha.adapters.storage import init_engine, shutdown_engine
from captcha.config import get_settings
from captcha.domain.errors import ConfigError
from captcha.observability import setup_logging
from captcha.services.pipeline.drift import DriftMonitor
from captcha.services.pipeline.golden import GoldenManifest, run_golden
from captcha.services.pipeline.retrain import RetrainTrigger
from captcha.services.pipeline.s3client import S3Writer
from captcha.services.pipeline.sampler import Sampler
from captcha.services.pipeline.scheduler import PipelineScheduler

logger = structlog.get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser("captcha-pipeline")
    p.add_argument(
        "--once",
        choices=("sample", "drift", "retrain", "golden", "check-model", "all"),
        default=None,
        help="run one job and exit instead of starting the daemon",
    )
    p.add_argument("--type", default="", help="(retrain only) captcha type to request retrain for")
    p.add_argument("--reason", default="manual", help="(retrain only) reason string")
    p.add_argument("--manifest", default="", help="(golden only) path to a golden manifest JSON")
    p.add_argument("--predict", default="", help="(golden only) 'module:function' taking a task payload")
    p.add_argument("--loader", default="", help="(golden only) 'module:function' mapping an image key to bytes")
    p.add_argument("--min-accuracy", type=float, default=None, help="(golden only) override manifest gate")
    p.add_argument("--min-coverage", type=float, default=None, help="(golden only) override manifest gate")
    p.add_argument("--concurrency", type=int, default=4, help="(golden only) cases in flight")
    p.add_argument("--name", default="", help="(check-model only) artifact name, e.g. hopscotch-v1")
    p.add_argument("--version", default="latest", help="(check-model only) version, default latest")
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


def _load_callable(spec: str) -> Callable[..., Any]:
    """Resolve 'module:function'. The harness never imports a model itself."""
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ValueError(f"expected 'module:function', got {spec!r}")
    fn = getattr(importlib.import_module(module_name), attr, None)
    if not callable(fn):
        raise ValueError(f"{spec!r} is not a callable attribute")
    return fn


def _run_check_model(settings: Any, *, name: str, version: str) -> int:
    """Whole-bundle check: every file's sha256, the required set, config/release JSON."""
    if not name:
        logger.error("pipeline.check_model.usage", need="--name")
        return 2
    from captcha.adapters.weights import Registry

    registry = Registry(
        s3_endpoint=settings.s3_endpoint,
        bucket=settings.s3_bucket,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )
    try:
        check = registry.check_artifact(name, version)
    except Exception as exc:
        logger.error("pipeline.check_model.failed", name=name, error=str(exc))
        return 1
    print(json.dumps(check.to_dict(), indent=2, ensure_ascii=False))
    return 0 if check.ok else 1


async def _run_golden(args: argparse.Namespace) -> int:
    if not args.manifest or not args.predict:
        logger.error("pipeline.golden.usage", need="--manifest and --predict")
        return 2
    try:
        predict = _load_callable(args.predict)
        loader = _load_callable(args.loader) if args.loader else None
        manifest = GoldenManifest.from_file(args.manifest)
    except (ValueError, ImportError, ConfigError) as exc:
        logger.error("pipeline.golden.setup_failed", error=str(exc))
        return 2

    report = await run_golden(
        manifest,
        predict,
        loader=loader,
        concurrency=args.concurrency,
        min_accuracy=args.min_accuracy,
        min_coverage=args.min_coverage,
    )
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0 if report.passed else 1


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
        except Exception as exc:
            logger.exception("pipeline.sample.crashed", error=str(exc))

    async def drift_job() -> None:
        try:
            await _run_drift(monitor, retrain, auto_retrain=auto_retrain)
        except Exception as exc:
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
        # Windows can't deliver SIGTERM to an asyncio loop; SIGINT still works.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _on_signal)

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
        if args.once == "golden":
            return await _run_golden(args)
        if args.once == "check-model":
            return _run_check_model(settings, name=args.name, version=args.version)
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
