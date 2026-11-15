"""HTTP worker runtime: lease heartbeat, queue-depth gauge, terminal counter.

No real Redis and no browser: the in-memory double covers the handful of
commands used by CapabilityRegistry and XLEN, and the consumer/persistence
stand-ins mirror tests/services/test_escalation.py.
"""

from __future__ import annotations

import asyncio
import fnmatch
import time
import uuid
from typing import Any

from captcha.adapters.queue.capabilities import heartbeat_key
from captcha.config import Settings
from captcha.domain.enums import CaptchaType, TaskStatus
from captcha.domain.errors import CaptchaError
from captcha.domain.task import SolverResult, Task, TaskEnvelope
from captcha.observability import metrics
from captcha.services.worker_http.concurrency import ConcurrencyLimiter
from captcha.services.worker_http.context import WorkerContextBundle
from captcha.services.worker_http.heartbeat import BEAT_INTERVAL_SEC, HttpLaneHeartbeat
from captcha.services.worker_http.worker import Worker
from captcha.solvers.base import Solver, SolverContext, SolverInfo
from captcha.solvers.registry import SolverRegistry

TYPE = CaptchaType.TURNSTILE


# --------------------------------------------------------------------------- fakes


class FakeRedis:
    """In-memory subset: capabilities (SETEX/SADD/DEL), XINFO GROUPS, XLEN."""

    def __init__(self, *, depth: int = 0, lag: int | None = None, fail_setex: bool = False) -> None:
        self.strings: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}
        self.depth = depth
        self.lag = lag
        self.fail_setex = fail_setex
        self.setex_calls: list[str] = []
        self.xlen_calls: list[str] = []
        self.xinfo_calls: list[str] = []

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if self.fail_setex:
            raise ConnectionError("redis down")
        self.setex_calls.append(key)
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
        async def _iter():
            for key in list(self.strings):
                if match is None or fnmatch.fnmatch(key, match):
                    yield key.encode()

        return _iter()

    async def xinfo_groups(self, stream: str) -> list[dict[Any, Any]]:
        self.xinfo_calls.append(stream)
        if self.lag is None:
            return []
        return [{"name": b"group:lane-http", "lag": self.lag}]

    async def xlen(self, stream: str) -> int:
        self.xlen_calls.append(stream)
        return self.depth


class BlockingConsumer:
    """Returns one empty batch, then blocks; lets run() be cancelled cleanly."""

    stream = "test:stream:http"
    group = "group:lane-http"

    def __init__(self) -> None:
        self.reads = 0
        self.blocked = asyncio.Event()

    async def read(self) -> list[tuple[str, TaskEnvelope]]:
        self.reads += 1
        if self.reads == 1:
            return []
        await self.blocked.wait()
        return []

    async def ack(self, message_id: str) -> None:
        pass

    async def fail(self, message_id: str, envelope: TaskEnvelope, reason: str) -> bool:
        return False


class FakeConsumer:
    stream = "test:stream:http"
    group = "group:lane-http"

    def __init__(self, *, dead_on_fail: bool = False) -> None:
        self.acked: list[str] = []
        self.failed: list[str] = []
        self.dead_on_fail = dead_on_fail

    async def ack(self, message_id: str) -> None:
        self.acked.append(message_id)

    async def fail(self, message_id: str, envelope: TaskEnvelope, reason: str) -> bool:
        self.failed.append(reason)
        return self.dead_on_fail


class FakeProducer:
    async def publish(self, envelope: TaskEnvelope, *, lane: Any = None) -> str:
        return "1-0"


class FakeState:
    def __init__(self) -> None:
        self.ready: list[tuple[Any, SolverResult]] = []
        self.failed: list[tuple[Any, dict[str, Any]]] = []

    async def set_processing(self, task_id: Any, worker_id: str, *, started_at: float) -> None:
        pass

    async def set_ready(self, task_id: Any, result: SolverResult, *, finished_at: float) -> None:
        self.ready.append((task_id, result))

    async def set_failed(self, task_id: Any, error: dict[str, Any], *, finished_at: float) -> None:
        self.failed.append((task_id, error))


class FakePersistence:
    async def processing(self, task_id: Any, worker_id: str) -> None:
        pass

    async def ready(self, task_id: Any, *, result: Any, latency_ms: float, solver_name: str, task_type: str) -> None:
        pass

    async def failed(
        self,
        task_id: Any,
        *,
        error: dict[str, Any],
        latency_ms: float,
        solver_name: str,
        task_type: str,
        refund: bool,
    ) -> None:
        pass


class FakeHTTP:
    async def aclose(self) -> None:
        pass


def _http_factory(**kwargs: Any) -> FakeHTTP:
    return FakeHTTP()


class HttpOk(Solver):
    info = SolverInfo(name="fake-http-ok-runtime", types=(TYPE,), http=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        return SolverResult(token="solved")


class HttpFails(Solver):
    info = SolverInfo(name="fake-http-fails-runtime", types=(TYPE,), http=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        raise CaptchaError("upstream hiccup")


class BrowserOnly(Solver):
    info = SolverInfo(name="fake-browser-only-runtime", types=(CaptchaType.HCAPTCHA,), http=False, browser=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        raise AssertionError("browser-only solver must not be advertised on the http lane")


def _registry(*classes: type[Solver]) -> SolverRegistry:
    registry = SolverRegistry()
    for cls in classes:
        registry.register(cls)
    return registry


def _settings() -> Settings:
    return Settings(redis_stream_prefix="test")


def _envelope() -> TaskEnvelope:
    return TaskEnvelope(task=Task(id=uuid.uuid4(), type=TYPE, payload={"sitekey": "k"}))


def _worker(
    *,
    registry: SolverRegistry,
    redis: FakeRedis,
    consumer: Any | None = None,
) -> Worker:
    bundle = WorkerContextBundle(
        settings=_settings(),
        redis=redis,  # type: ignore[arg-type]
        pg=None,
        template=SolverContext(),
        http_factory=_http_factory,
        js_runtime=None,
        proxy_pool=None,
    )
    return Worker(
        worker_id="http-0",
        redis=redis,  # type: ignore[arg-type]
        registry=registry,
        state=FakeState(),  # type: ignore[arg-type]
        bundle=bundle,
        limiter=ConcurrencyLimiter(1),
        persistence=FakePersistence(),  # type: ignore[arg-type]
        settings=_settings(),
        producer=FakeProducer(),  # type: ignore[arg-type]
        consumer=consumer or FakeConsumer(),  # type: ignore[arg-type]
    )


def test_worker_injects_solver_registry_into_context() -> None:
    registry = _registry(HttpOk)
    worker = _worker(registry=registry, redis=FakeRedis())

    assert worker._bundle.template.extra["solver_registry"] is registry


def _tasks_total(status: TaskStatus) -> float:
    return (
        metrics.REGISTRY.get_sample_value(
            "captcha_tasks_total",
            {"type": TYPE.value, "lane": "http", "status": status.value},
        )
        or 0.0
    )


async def _wait_for(predicate, *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met before timeout")


# ---------------------------------------------------------------------- heartbeat


def _heartbeat(redis: FakeRedis, *, interval_sec: int | None = None) -> HttpLaneHeartbeat:
    return HttpLaneHeartbeat(
        redis,  # type: ignore[arg-type]
        worker_id="http-0",
        prefix="test",
        types=[TYPE],
        interval_sec=interval_sec,
    )


async def test_heartbeat_writes_http_lease_and_capabilities() -> None:
    redis = FakeRedis()
    beat = _heartbeat(redis)

    await beat.start()
    try:
        key = heartbeat_key("test", "http", "http-0")
        assert redis.strings[key] == "1"
        assert redis.ttls[key] == 90
        assert redis.sets["test:cap:http"] == {"turnstile"}
        assert BEAT_INTERVAL_SEC == 30
    finally:
        await beat.stop()


async def test_heartbeat_refreshes_on_its_interval() -> None:
    redis = FakeRedis()
    beat = _heartbeat(redis, interval_sec=0.01)

    await beat.start()
    await _wait_for(lambda: len(redis.setex_calls) >= 2)
    await beat.stop()

    assert len(redis.setex_calls) >= 2


async def test_heartbeat_clears_lease_and_cap_set_on_stop() -> None:
    redis = FakeRedis()
    beat = _heartbeat(redis)

    await beat.start()
    await beat.stop()

    assert heartbeat_key("test", "http", "http-0") not in redis.strings
    assert "test:cap:http" not in redis.sets


async def test_lost_beat_does_not_raise() -> None:
    redis = FakeRedis(fail_setex=True)
    beat = _heartbeat(redis)

    assert await beat.beat() is False


def test_http_capabilities_exclude_browser_only_solvers() -> None:
    worker = _worker(registry=_registry(HttpOk, BrowserOnly), redis=FakeRedis())

    # turnstile has an HTTP probe but is browser-gated (`needs_browser`), so an
    # HTTP worker must not advertise it as sellable.
    assert worker._heartbeat.types == ()


# -------------------------------------------------------------------- worker run


async def test_run_starts_heartbeat_and_publishes_queue_depth() -> None:
    redis = FakeRedis(lag=7)
    consumer = BlockingConsumer()
    worker = _worker(registry=_registry(HttpOk), redis=redis, consumer=consumer)

    task = asyncio.create_task(worker.run())
    await _wait_for(lambda: bool(redis.xinfo_calls))
    try:
        assert redis.strings[heartbeat_key("test", "http", "http-0")] == "1"
        assert redis.xinfo_calls[0] == "test:stream:http"
        assert metrics.REGISTRY.get_sample_value("captcha_queue_depth", {"lane": "http"}) == 7.0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.shutdown()

    assert heartbeat_key("test", "http", "http-0") not in redis.strings, "shutdown drops the lease"


async def test_heartbeat_failure_does_not_stop_the_read_loop() -> None:
    redis = FakeRedis(lag=0, fail_setex=True)
    consumer = BlockingConsumer()
    worker = _worker(registry=_registry(HttpOk), redis=redis, consumer=consumer)

    task = asyncio.create_task(worker.run())
    await _wait_for(lambda: bool(redis.xinfo_calls))
    try:
        assert consumer.reads >= 1, "the loop kept reading after the beat failed"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.shutdown()


async def test_queue_depth_falls_back_to_xlen_without_group_lag() -> None:
    redis = FakeRedis(depth=9)
    worker = _worker(registry=_registry(HttpOk), redis=redis)

    await worker._refresh_queue_depth()

    assert redis.xlen_calls == ["test:stream:http"]
    assert metrics.REGISTRY.get_sample_value("captcha_queue_depth", {"lane": "http"}) == 9.0


# --------------------------------------------------------------- terminal metrics


async def test_ready_task_increments_tasks_total_ready_once() -> None:
    redis = FakeRedis()
    worker = _worker(registry=_registry(HttpOk), redis=redis)
    before_ready = _tasks_total(TaskStatus.READY)
    before_failed = _tasks_total(TaskStatus.FAILED)

    await worker._dispatch("1-0", _envelope(), time.time())

    assert _tasks_total(TaskStatus.READY) == before_ready + 1
    assert _tasks_total(TaskStatus.FAILED) == before_failed


async def test_terminal_failure_increments_tasks_total_failed_once() -> None:
    redis = FakeRedis()
    consumer = FakeConsumer()
    worker = _worker(registry=_registry(), redis=redis, consumer=consumer)
    before_ready = _tasks_total(TaskStatus.READY)
    before_failed = _tasks_total(TaskStatus.FAILED)

    await worker._dispatch("1-0", _envelope(), time.time())

    assert consumer.acked == ["1-0"]
    assert _tasks_total(TaskStatus.FAILED) == before_failed + 1
    assert _tasks_total(TaskStatus.READY) == before_ready


async def test_retry_is_not_a_terminal_sample() -> None:
    redis = FakeRedis()
    consumer = FakeConsumer(dead_on_fail=False)
    worker = _worker(registry=_registry(HttpFails), redis=redis, consumer=consumer)
    before_ready = _tasks_total(TaskStatus.READY)
    before_failed = _tasks_total(TaskStatus.FAILED)

    await worker._dispatch("1-0", _envelope(), time.time())

    assert consumer.failed == ["ERR_GENERIC"]
    assert _tasks_total(TaskStatus.FAILED) == before_failed
    assert _tasks_total(TaskStatus.READY) == before_ready


async def test_dead_letter_is_a_terminal_failure_sample() -> None:
    redis = FakeRedis()
    consumer = FakeConsumer(dead_on_fail=True)
    worker = _worker(registry=_registry(HttpFails), redis=redis, consumer=consumer)
    before_failed = _tasks_total(TaskStatus.FAILED)

    await worker._dispatch("1-0", _envelope(), time.time())

    assert _tasks_total(TaskStatus.FAILED) == before_failed + 1
