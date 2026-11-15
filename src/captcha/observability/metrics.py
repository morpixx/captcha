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
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    45.0,
    90.0,
    180.0,
)
# sensor builds are fast — ms territory
_SENSOR_BUCKETS: Final[tuple[float, ...]] = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.0,
    5.0,
)
# cold Camoufox launch sits in the seconds-to-a-minute range
_BROWSER_LAUNCH_BUCKETS: Final[tuple[float, ...]] = (
    0.5,
    1.0,
    2.0,
    3.0,
    5.0,
    8.0,
    12.0,
    20.0,
    30.0,
    60.0,
)

# NOTE: cannot be called `captcha_tasks_created_total`. prometheus_client emits a
# synthetic `<base>_created` sample per counter, so that name would claim
# `captcha_tasks_created` — exactly the series derived from the contract's
# `captcha_tasks_total`. `accepted` keeps both families registrable.
tasks_created_total: Final[Counter] = Counter(
    "captcha_tasks_accepted_total",
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

escalations_total: Final[Counter] = Counter(
    "captcha_escalations_total",
    "HTTP-lane tasks rerouted to the browser lane after ERR_NEEDS_BROWSER.",
    labelnames=("type",),
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

# --- contract §10: the canonical task counter (type/lane/status) ------------
tasks_total: Final[Counter] = Counter(
    "captcha_tasks_total",
    "Tasks by captcha type, lane and terminal status (contract §10).",
    labelnames=("type", "lane", "status"),
    registry=REGISTRY,
)

queue_depth: Final[Gauge] = Gauge(
    "captcha_queue_depth",
    "Entries waiting in a lane's Redis stream.",
    labelnames=("lane",),
    registry=REGISTRY,
    multiprocess_mode="livesum",
)

dlq_total: Final[Counter] = Counter(
    "captcha_dlq_total",
    "Envelopes moved to the dead-letter stream.",
    labelnames=("lane", "reason"),
    registry=REGISTRY,
)

# --- browser lane. The worker owns the values; these are the gauges/histogram
# it publishes. Nothing here is populated speculatively: no measurement, no
# sample until worker-browser reports one.
browser_sessions_active: Final[Gauge] = Gauge(
    "captcha_browser_sessions_active",
    "Live Camoufox sessions in this worker.",
    registry=REGISTRY,
    multiprocess_mode="livesum",
)

browser_launch_seconds: Final[Histogram] = Histogram(
    "captcha_browser_launch_seconds",
    "Camoufox launch time, process start to first usable page.",
    buckets=_BROWSER_LAUNCH_BUCKETS,
    registry=REGISTRY,
)

browser_rss_bytes: Final[Gauge] = Gauge(
    "captcha_browser_rss_bytes",
    "Resident memory of all Camoufox processes in this worker.",
    registry=REGISTRY,
    multiprocess_mode="livesum",
)

# --- canary / per-type health ----------------------------------------------
canary_pass_rate: Final[Gauge] = Gauge(
    "captcha_canary_pass_rate",
    "Last canary pass rate per captcha type.",
    labelnames=("type",),
    registry=REGISTRY,
)

type_health: Final[Gauge] = Gauge(
    "captcha_type_health",
    "Canary verdict per captcha type: 1 = ok, 0 = degraded.",
    labelnames=("type",),
    registry=REGISTRY,
)


def record_canary_type(type_: str, *, pass_rate: float, healthy: bool) -> None:
    """Publish one type's canary verdict. Used by services.canary."""
    canary_pass_rate.labels(type=type_).set(pass_rate)
    type_health.labels(type=type_).set(1.0 if healthy else 0.0)


def record_browser_launch(seconds: float) -> None:
    """worker-browser calls this after a session is usable."""
    browser_launch_seconds.observe(seconds)


def set_browser_sessions_active(count: int) -> None:
    """worker-browser publishes its live session count."""
    browser_sessions_active.set(count)


def set_browser_rss_bytes(total: int) -> None:
    """worker-browser publishes summed RSS over the Camoufox process tree."""
    browser_rss_bytes.set(total)


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
