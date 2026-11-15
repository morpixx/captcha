"""Heartbeat per lane: cv/protocol leases + periodic capability refresh.

Contract §2/§7. No live Redis: the fake below is the in-memory subset the
capability adapter actually touches, and the gateway refresh loop is driven by a
fake clock, so the timing assertions are deterministic.

What is pinned here:
  * cv and protocol workers write `captcha:lane:<lane>:<worker>` with TTL 90,
    advertise only the types that route to their own lane, refresh every 30 s
    and clear the lease on stop;
  * the gateway re-reads those leases on a timer instead of only at boot, keeps
    the browser-lane gate, and unions `CAPTCHA_EXTRA_TYPES` without letting it
    resurrect a dead browser lane.
"""

from __future__ import annotations

import asyncio
import fnmatch
from typing import Any

from captcha.adapters.queue.capabilities import (
    HEARTBEAT_TTL_SECONDS,
    CapabilityRegistry,
    capability_key,
    heartbeat_key,
)
from captcha.adapters.queue.heartbeat import REFRESH_INTERVAL_SEC, LaneHeartbeat
from captcha.config import Settings
from captcha.domain.enums import CaptchaType
from captcha.domain.task import SolverResult, Task
from captcha.services.gateway.app import LiveTypesRefresher, parse_extra_types
from captcha.services.gateway.state import AppState
from captcha.services.worker_cv.worker import Worker as CvWorker
from captcha.services.worker_protocol.worker import Worker as ProtocolWorker
from captcha.solvers.base import Solver, SolverContext, SolverInfo
from captcha.solvers.registry import SolverRegistry

STABLE = CaptchaType.RECAPTCHA_V3  # http lane
BROWSER = CaptchaType.HCAPTCHA  # browser lane
CV_TYPE = CaptchaType.SLIDER  # cv lane
PROTOCOL_TYPE = CaptchaType.FRIENDLY  # protocol lane


class FakeRedis:
    """In-memory subset of redis.asyncio used by capabilities/heartbeat."""

    def __init__(self, *, fail_scan: bool = False) -> None:
        self.strings: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}
        self.fail_scan = fail_scan

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.strings[key] = value
        self.ttls[key] = ttl

    async def sadd(self, key: str, *members: str) -> None:
        self.sets.setdefault(key, set()).update(members)

    async def smembers(self, key: str) -> set[bytes]:
        return {m.encode() for m in self.sets.get(key, set())}

    async def exists(self, key: str) -> int:
        return int(key in self.strings)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(self.strings.pop(key, None) is not None)
            removed += int(self.sets.pop(key, None) is not None)
        return removed

    def scan_iter(self, match: str | None = None, count: int | None = None):
        async def _iter() -> Any:
            if self.fail_scan:
                raise RuntimeError("redis down")
            for key in list(self.strings):
                if match is None or fnmatch.fnmatch(key, match):
                    yield key.encode()

        return _iter()


class FakeClock:
    """Stands in for `asyncio.sleep`: records the interval, yields to the loop."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        await asyncio.sleep(0)


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def _caps(redis: FakeRedis) -> CapabilityRegistry:
    return CapabilityRegistry(redis)  # type: ignore[arg-type]


# ---- workers ---------------------------------------------------------------


class CvSolver(Solver):
    info = SolverInfo(name="lane-cv", types=(CV_TYPE, STABLE), http=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        return SolverResult(offset=1)


class ProtocolSolver(Solver):
    info = SolverInfo(name="lane-protocol", types=(PROTOCOL_TYPE, STABLE), http=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        return SolverResult(token="pow")


def _registry(*classes: type[Solver]) -> SolverRegistry:
    registry = SolverRegistry()
    for cls in classes:
        registry.register(cls)
    return registry


async def test_contract_timing() -> None:
    assert HEARTBEAT_TTL_SECONDS == 90
    assert REFRESH_INTERVAL_SEC == 30


async def test_cv_worker_beats_only_its_lane() -> None:
    redis = FakeRedis()
    heartbeat = LaneHeartbeat(redis, lane="cv", worker_id="cv-1")  # type: ignore[arg-type]
    worker = CvWorker(
        _settings(),
        worker_id="cv-1",
        registry=_registry(CvSolver),
        heartbeat=heartbeat,
    )

    accepted = worker._accepted_types(CvSolver)
    assert accepted == [CV_TYPE], "recaptcha_v3 routes to http, not cv"

    worker._by_type = {ct: CvSolver() for ct in accepted}
    await worker._start_heartbeat()

    lane_key = heartbeat_key("captcha", "cv", "cv-1")
    assert redis.ttls[lane_key] == 90
    assert heartbeat.types == ("slider",)
    assert redis.sets[capability_key("captcha", "cv")] == {"slider"}
    assert await _caps(redis).live_types() == frozenset({CV_TYPE})
    assert heartbeat.interval_sec == 30

    await worker._teardown()


async def test_protocol_worker_beats_only_its_lane() -> None:
    redis = FakeRedis()
    heartbeat = LaneHeartbeat(redis, lane="protocol", worker_id="protocol-1")  # type: ignore[arg-type]
    worker = ProtocolWorker(
        _settings(),
        worker_id="protocol-1",
        registry=_registry(ProtocolSolver),
        heartbeat=heartbeat,
    )

    accepted = worker._accepted_types(ProtocolSolver)
    assert accepted == [PROTOCOL_TYPE]

    worker._by_type = {ct: ProtocolSolver() for ct in accepted}
    await worker._start_heartbeat()

    assert redis.ttls[heartbeat_key("captcha", "protocol", "protocol-1")] == 90
    assert heartbeat.types == ("friendly_captcha",)
    assert redis.sets[capability_key("captcha", "protocol")] == {"friendly_captcha"}


async def test_worker_stop_clears_its_lease() -> None:
    redis = FakeRedis()
    heartbeat = LaneHeartbeat(redis, lane="cv", worker_id="cv-stop")  # type: ignore[arg-type]
    worker = CvWorker(_settings(), worker_id="cv-stop", registry=_registry(CvSolver), heartbeat=heartbeat)
    worker._by_type = {CV_TYPE: CvSolver()}
    await worker._start_heartbeat()
    assert await _caps(redis).worker_alive("cv", "cv-stop") is True

    await worker._teardown()

    assert await _caps(redis).worker_alive("cv", "cv-stop") is False
    assert capability_key("captcha", "cv") not in redis.sets
    assert await _caps(redis).live_types() == frozenset()


# ---- gateway ---------------------------------------------------------------


class BrowserSolver(Solver):
    info = SolverInfo(name="lane-browser", types=(BROWSER,), http=True, browser=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        return SolverResult(token="browser")


def _app_state(redis: FakeRedis, registry: SolverRegistry, settings: Settings) -> AppState:
    return AppState(
        settings=settings,
        redis=redis,  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        sessionmaker=None,  # type: ignore[arg-type]
        state_store=None,  # type: ignore[arg-type]
        producer=None,  # type: ignore[arg-type]
        solver_registry=registry,
    )


def _refresher(
    redis: FakeRedis,
    *,
    settings: Settings | None = None,
    interval_sec: float | None = 20,
    sleep: Any = None,
) -> tuple[LiveTypesRefresher, AppState]:
    cfg = settings or _settings()
    registry = _registry(CvSolver, BrowserSolver)
    state = _app_state(redis, registry, cfg)
    return LiveTypesRefresher(state, registry, interval_sec=interval_sec, sleep=sleep), state


async def test_refresh_tracks_heartbeats_without_a_restart() -> None:
    redis = FakeRedis()
    refresher, state = _refresher(redis)

    await refresher.tick()
    assert state.solver_types == {CV_TYPE, STABLE}, "registry types are sold; browser needs a lease"

    await _caps(redis).heartbeat("browser", "b-1", [BROWSER])
    await refresher.tick()
    assert BROWSER in state.solver_types

    # SETEX expiry: the lease key is gone, the capability set is not.
    redis.strings.clear()
    await refresher.tick()
    assert BROWSER not in state.solver_types, "a stale cap set must not resurrect the browser lane"
    assert state.solver_types == {CV_TYPE, STABLE}


async def test_refresh_adds_types_learned_from_a_live_worker() -> None:
    redis = FakeRedis()
    refresher, state = _refresher(redis)
    await refresher.tick()
    assert PROTOCOL_TYPE not in state.solver_types

    await _caps(redis).heartbeat("protocol", "p-1", [PROTOCOL_TYPE])
    await refresher.tick()

    assert state.solver_types == {CV_TYPE, STABLE, PROTOCOL_TYPE}


async def test_refresh_survives_a_redis_fault() -> None:
    redis = FakeRedis(fail_scan=True)
    refresher, state = _refresher(redis)

    sold = await refresher.tick()

    assert sold == {CV_TYPE, STABLE}, "no proof of life means registry only"
    assert BROWSER not in state.solver_types


async def test_extra_types_are_manual_declarations() -> None:
    redis = FakeRedis()
    refresher, state = _refresher(redis, settings=_settings(CAPTCHA_EXTRA_TYPES="rotate; SLIDER, not_a_type"))
    await refresher.tick()

    assert refresher.extra_types == {CaptchaType.ROTATE, CV_TYPE}
    assert CaptchaType.ROTATE in state.solver_types
    assert STABLE in state.solver_types


async def test_extra_types_cannot_open_the_browser_lane() -> None:
    redis = FakeRedis()
    refresher, state = _refresher(redis, settings=_settings(CAPTCHA_EXTRA_TYPES="hcaptcha"))
    await refresher.tick()

    assert BROWSER not in state.solver_types, "manual declaration is not a heartbeat"


def test_extra_types_env_switch() -> None:
    settings = _settings(CAPTCHA_EXTRA_TYPES="rotate,slider")

    assert settings.captcha_extra_types == "rotate,slider"
    assert parse_extra_types(settings.captcha_extra_types) == {CaptchaType.ROTATE, CV_TYPE}
    assert parse_extra_types("") == frozenset()
    assert parse_extra_types("nope") == frozenset()


def test_refresh_interval_defaults_to_the_setting() -> None:
    redis = FakeRedis()
    refresher, _ = _refresher(redis, interval_sec=None)

    assert refresher.interval_sec == _settings().gateway_types_refresh_sec
    assert 15 <= refresher.interval_sec <= 30


async def test_refresh_loop_ticks_on_the_interval() -> None:
    redis = FakeRedis()
    clock = FakeClock()
    refresher, state = _refresher(redis, interval_sec=5, sleep=clock.sleep)
    await _caps(redis).heartbeat("browser", "b-1", [BROWSER])
    await refresher.start()
    assert BROWSER in state.solver_types

    # The worker dies between two ticks; the loop has to notice on its own.
    redis.strings.clear()
    for _ in range(200):
        if BROWSER not in state.solver_types:
            break
        await asyncio.sleep(0)
    assert BROWSER not in state.solver_types

    assert clock.sleeps, "the loop must wait on the configured interval"
    assert set(clock.sleeps) == {5.0}

    await refresher.stop()
    sleeps_after_stop = len(clock.sleeps)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(clock.sleeps) == sleeps_after_stop, "stop() must cancel the refresh loop"
