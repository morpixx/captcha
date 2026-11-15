"""Contract §10 metrics + readiness surface.

Two things are under test here:

* every metric named in the production contract is registered exactly once —
  `captcha_escalations_total` included, because router-escalation already owns
  it and a second registration would raise at import time;
* `/readyz` refuses traffic unless Redis, Postgres and at least one lane
  heartbeat are all fresh, and it names the failing probe in the 503 body.

No real Redis/Postgres: the probes only need ping/scan_iter and an async
context manager, so fakes keep this fast and deterministic.
"""

from __future__ import annotations

import fnmatch
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI
from prometheus_client import generate_latest

from captcha.observability import metrics
from captcha.services.gateway.routes.health import router as health_router

#: Exactly the names in docs/production-contract.md §10.
CONTRACT_METRICS = (
    "captcha_tasks_total",
    "captcha_task_latency_seconds",
    "captcha_queue_depth",
    "captcha_browser_sessions_active",
    "captcha_browser_launch_seconds",
    "captcha_browser_rss_bytes",
    "captcha_escalations_total",
    "captcha_canary_pass_rate",
    "captcha_type_health",
    "captcha_dlq_total",
)


# --------------------------------------------------------------------- metrics


def test_every_contract_metric_is_registered_once() -> None:
    text = generate_latest(metrics.REGISTRY).decode()

    for name in CONTRACT_METRICS:
        assert f"# HELP {name} " in text, f"{name} missing HELP"
        assert f"# TYPE {name} " in text, f"{name} missing TYPE"
        assert text.count(f"# TYPE {name} ") == 1, f"{name} registered more than once"


def test_tasks_total_has_the_contract_labels() -> None:
    metrics.tasks_total.labels(type="obs_probe", lane="http", status="ready").inc()

    value = metrics.REGISTRY.get_sample_value(
        "captcha_tasks_total",
        {"type": "obs_probe", "lane": "http", "status": "ready"},
    )
    assert value == 1.0


def test_queue_depth_and_dlq_are_wired() -> None:
    metrics.queue_depth.labels(lane="browser").set(7)
    metrics.dlq_total.labels(lane="http", reason="ERR_TIMEOUT").inc()

    assert metrics.REGISTRY.get_sample_value("captcha_queue_depth", {"lane": "browser"}) == 7.0
    assert metrics.REGISTRY.get_sample_value("captcha_dlq_total", {"lane": "http", "reason": "ERR_TIMEOUT"}) == 1.0


def test_canary_type_health_sets_both_gauges() -> None:
    metrics.record_canary_type("obs_probe", pass_rate=0.5, healthy=False)

    assert metrics.REGISTRY.get_sample_value("captcha_canary_pass_rate", {"type": "obs_probe"}) == 0.5
    assert metrics.REGISTRY.get_sample_value("captcha_type_health", {"type": "obs_probe"}) == 0.0

    metrics.record_canary_type("obs_probe", pass_rate=1.0, healthy=True)
    assert metrics.REGISTRY.get_sample_value("captcha_type_health", {"type": "obs_probe"}) == 1.0


def test_browser_helpers_publish_reported_values() -> None:
    # The gauges/histogram exist for worker-browser; nothing here invents data.
    # The registry is process-global, and tests/services/test_worker_browser
    # launches sessions before this file runs — assert the delta, not a total.
    launches_before = metrics.REGISTRY.get_sample_value("captcha_browser_launch_seconds_count") or 0.0
    metrics.record_browser_launch(2.5)
    metrics.set_browser_sessions_active(3)
    metrics.set_browser_rss_bytes(1024 * 1024 * 700)

    assert metrics.REGISTRY.get_sample_value("captcha_browser_sessions_active") == 3.0
    assert metrics.REGISTRY.get_sample_value("captcha_browser_rss_bytes") == 734003200.0
    assert metrics.REGISTRY.get_sample_value("captcha_browser_launch_seconds_count") == launches_before + 1


# ------------------------------------------------------------------- readiness


class FakeRedis:
    def __init__(self, *, lanes: tuple[str, ...] = ("http",), ping_ok: bool = True) -> None:
        self.lanes = lanes
        self.ping_ok = ping_ok

    async def ping(self) -> bool:
        if not self.ping_ok:
            raise ConnectionError("redis down")
        return True

    async def scan_iter(self, match: str, count: int = 100) -> Any:
        for lane in self.lanes:
            key = f"captcha:lane:{lane}:w-0"
            if fnmatch.fnmatchcase(key, match):
                yield key


class FakeConn:
    async def execute(self, _statement: Any) -> None:
        return None


class FakeConnect:
    def __init__(self, ok: bool) -> None:
        self.ok = ok

    async def __aenter__(self) -> FakeConn:
        if not self.ok:
            raise ConnectionError("pg down")
        return FakeConn()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeEngine:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok

    def connect(self) -> FakeConnect:
        return FakeConnect(self.ok)


def _state(*, redis: Any = None, engine: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        redis=redis or FakeRedis(),
        engine=engine or FakeEngine(),
        settings=SimpleNamespace(redis_stream_prefix="captcha"),
    )


def _client(state: SimpleNamespace) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(health_router)
    app.state.ctx = state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")


async def test_readyz_ok_with_redis_pg_and_one_live_lane() -> None:
    async with _client(_state()) as client:
        response = await client.get("/readyz")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "ok": True,
        "redis": True,
        "postgres": True,
        "lanes": True,
        "live_lanes": ["http"],
    }


async def test_readyz_503_when_no_lane_heartbeat() -> None:
    async with _client(_state(redis=FakeRedis(lanes=()))) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["lanes"] is False
    assert "no live lane heartbeat" in body["errors"]["lanes"]


async def test_readyz_503_names_a_dead_postgres() -> None:
    async with _client(_state(engine=FakeEngine(ok=False))) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["postgres"] is False
    assert body["redis"] is True
    assert "pg down" in body["errors"]["postgres"]


async def test_readyz_503_when_redis_is_down_skips_lane_probe() -> None:
    async with _client(_state(redis=FakeRedis(ping_ok=False))) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["redis"] is False
    assert body["lanes"] is False
    assert "redis unavailable" in body["errors"]["lanes"]


async def test_healthz_stays_liveness_only() -> None:
    # Both dependencies are down; /healthz must still answer 200 and not probe.
    async with _client(_state(redis=FakeRedis(ping_ok=False), engine=FakeEngine(ok=False))) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
