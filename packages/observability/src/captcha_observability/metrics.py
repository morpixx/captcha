from __future__ import annotations

import os
from typing import Final

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    make_asgi_app,
    multiprocess,
)
from starlette.applications import Starlette
from starlette.routing import Mount

# dedicated registry — keeps /metrics clean, lets tests rebuild without re-registration errors
REGISTRY: Final[CollectorRegistry] = CollectorRegistry(auto_describe=True)

# latency buckets tuned for captcha work: ~50ms OCR up to a browser solve pushing a minute
_LATENCY_BUCKETS: Final[tuple[float, ...]] = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 45.0, 90.0, 180.0,
)
# sensor builds are fast — ms territory
_SENSOR_BUCKETS: Final[tuple[float, ...]] = (
    0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0,
)

tasks_created_total: Final[Counter] = Counter(
    "captcha_tasks_created_total",
    "Tasks accepted by the gateway, by captcha type and lane.",
    labelnames=("type", "lane"),
    registry=REGISTRY,
)

tasks_done_total: Final[Counter] = Counter(
    "captcha_tasks_done_total",
    "Tasks that reached a terminal status.",
    labelnames=("type", "status", "solve_mode"),
    registry=REGISTRY,
)

task_latency_seconds: Final[Histogram] = Histogram(
    "captcha_task_latency_seconds",
    "End-to-end solve latency, from accept to terminal status.",
    labelnames=("type", "solve_mode"),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

solver_errors_total: Final[Counter] = Counter(
    "captcha_solver_errors_total",
    "Solver-raised errors, tagged with CaptchaError.code.",
    labelnames=("type", "error_code"),
    registry=REGISTRY,
)

proxy_bans_total: Final[Counter] = Counter(
    "captcha_proxy_bans_total",
    "Proxy IPs / sessions marked bad by the pool.",
    labelnames=("provider",),
    registry=REGISTRY,
)

sensor_build_latency_seconds: Final[Histogram] = Histogram(
    "captcha_sensor_build_latency_seconds",
    "Time to produce an antibot sensor payload.",
    labelnames=("engine",),
    buckets=_SENSOR_BUCKETS,
    registry=REGISTRY,
)

active_tasks: Final[Gauge] = Gauge(
    "captcha_active_tasks",
    "Tasks currently in flight, by lane.",
    labelnames=("lane",),
    registry=REGISTRY,
    multiprocess_mode="livesum",
)


def setup_metrics() -> CollectorRegistry:
    """No-op hook — metrics are module-level singletons. Returns the registry for callers that want it."""
    return REGISTRY


def build_metrics_app() -> Starlette:
    """Starlette app serving /metrics against our registry.

    Mount it on any ASGI framework:
        app.mount("/metrics", build_metrics_app())

    If PROMETHEUS_MULTIPROC_DIR is set (gunicorn + several uvicorn workers), build a
    fresh registry backed by MultiProcessCollector so each scrape aggregates every worker.
    """
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry: CollectorRegistry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY

    return Starlette(routes=[Mount("/", app=make_asgi_app(registry=registry))])
