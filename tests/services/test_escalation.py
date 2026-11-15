"""Router escalation: `NeedsBrowserError` -> browser lane, one hop only.

No Redis, no Postgres, no browser. Fakes stand in for the consumer/producer
and the state/persistence mirrors; the registry is real, with toy solvers.
Contract points under test: §2 (reroute once, ack the source, never charge
twice) and §5/§7 (non-terminal error, mode-aware solver pick).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import msgspec
import pytest

from captcha.adapters.queue import Consumer, Producer
from captcha.config import Settings
from captcha.domain.enums import CaptchaType, SolveMode
from captcha.domain.errors import CaptchaError, NeedsBrowserError, UnsolvableError
from captcha.domain.task import MAX_LANE_HOPS, SolverResult, Task, TaskEnvelope
from captcha.observability import metrics
from captcha.services.worker_http.concurrency import ConcurrencyLimiter
from captcha.services.worker_http.context import WorkerContextBundle
from captcha.services.worker_http.worker import Worker
from captcha.solvers.base import Solver, SolverContext, SolverInfo
from captcha.solvers.registry import SolverRegistry

_ENVELOPE_DECODER = msgspec.msgpack.Decoder(TaskEnvelope)
TYPE = CaptchaType.HCAPTCHA


# --------------------------------------------------------------------------- fakes


class HttpNeedsBrowser(Solver):
    """HTTP solver that always asks for the browser."""

    info = SolverInfo(name="fake-http-escalate", types=(TYPE,), http=True, browser=False)
    calls = 0

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        type(self).calls += 1
        raise NeedsBrowserError("jsdom failed; needs a real browser", context={"type": task.type.value})


class BrowserSolver(Solver):
    """Browser-only solver; must never be instantiated by the HTTP lane."""

    info = SolverInfo(name="fake-browser", types=(TYPE,), http=False, browser=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        raise AssertionError("browser-only solver ran on the http lane")


class HttpOk(Solver):
    info = SolverInfo(name="fake-http-ok", types=(CaptchaType.TURNSTILE,), http=True, browser=False)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        return SolverResult(token="solved")


class FakeState:
    def __init__(self) -> None:
        self.processing: list[tuple[Any, str]] = []
        self.ready: list[tuple[Any, SolverResult]] = []
        self.failed: list[tuple[Any, dict[str, Any]]] = []

    async def set_processing(self, task_id: Any, worker_id: str, *, started_at: float) -> None:
        self.processing.append((task_id, worker_id))

    async def set_ready(self, task_id: Any, result: SolverResult, *, finished_at: float) -> None:
        self.ready.append((task_id, result))

    async def set_failed(self, task_id: Any, error: dict[str, Any], *, finished_at: float) -> None:
        self.failed.append((task_id, error))


class FakeConsumer:
    def __init__(self, *, dead_on_fail: bool = False) -> None:
        self.acked: list[str] = []
        self.failed: list[tuple[str, TaskEnvelope, str]] = []
        self.dead_on_fail = dead_on_fail

    async def ack(self, message_id: str) -> None:
        self.acked.append(message_id)

    async def fail(self, message_id: str, envelope: TaskEnvelope, reason: str) -> bool:
        self.failed.append((message_id, envelope, reason))
        return self.dead_on_fail


class FakeProducer:
    def __init__(self, *, boom: bool = False) -> None:
        self.published: list[tuple[TaskEnvelope, Any]] = []
        self.boom = boom

    async def publish(self, envelope: TaskEnvelope, *, lane: Any = None) -> str:
        if self.boom:
            raise ConnectionError("redis down")
        self.published.append((envelope, lane))
        return "1-0"


class FakePersistence:
    def __init__(self) -> None:
        self.processing_calls: list[Any] = []
        self.ready_calls: list[Any] = []
        self.failed_calls: list[dict[str, Any]] = []

    async def processing(self, task_id: Any, worker_id: str) -> None:
        self.processing_calls.append((task_id, worker_id))

    async def ready(self, task_id: Any, *, result: Any, latency_ms: float, solver_name: str, task_type: str) -> None:
        self.ready_calls.append(result)

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
        self.failed_calls.append({"task_id": task_id, "error": error, "refund": refund, "solver_name": solver_name})


class FakeHTTP:
    async def aclose(self) -> None:
        pass


def _http_factory(**kwargs: Any) -> FakeHTTP:
    return FakeHTTP()


class FakeStreamRedis:
    """Just enough Redis for Producer.publish / Consumer.fail."""

    def __init__(self) -> None:
        self.xadds: list[tuple[str, dict[Any, Any]]] = []
        self.acked: list[Any] = []

    async def xadd(self, stream: str, fields: dict[Any, Any], *, maxlen: int, approximate: bool) -> bytes:
        self.xadds.append((stream, fields))
        return b"1-0"

    async def xack(self, stream: str, group: str, message_id: str) -> None:
        self.acked.append(message_id)


def _settings() -> Settings:
    return Settings(redis_stream_prefix="test")


def _envelope(*, lane_hops: int = 0, ct: CaptchaType = TYPE) -> TaskEnvelope:
    task = Task(id=uuid.uuid4(), type=ct, payload={"sitekey": "k"})
    return TaskEnvelope(task=task, lane_hops=lane_hops)


def _registry(*classes: type[Solver]) -> SolverRegistry:
    registry = SolverRegistry()
    for cls in classes:
        registry.register(cls)
    return registry


def _worker(
    *,
    registry: SolverRegistry | None = None,
    consumer: FakeConsumer | None = None,
    producer: FakeProducer | None = None,
    state: FakeState | None = None,
    persistence: FakePersistence | None = None,
) -> Worker:
    settings = _settings()
    bundle = WorkerContextBundle(
        settings=settings,
        redis=None,  # type: ignore[arg-type]
        pg=None,
        template=SolverContext(),
        http_factory=_http_factory,
        js_runtime=None,
        proxy_pool=None,
    )
    return Worker(
        worker_id="http-0",
        redis=object(),  # type: ignore[arg-type]  # Consumer.__init__ only stores it
        registry=registry or _registry(),
        state=state or FakeState(),  # type: ignore[arg-type]
        bundle=bundle,
        limiter=ConcurrencyLimiter(1),
        persistence=persistence or FakePersistence(),  # type: ignore[arg-type]
        settings=settings,
        producer=producer or FakeProducer(),  # type: ignore[arg-type]
        consumer=consumer or FakeConsumer(),  # type: ignore[arg-type]
    )


def _metric(type_value: str) -> float:
    value = metrics.REGISTRY.get_sample_value("captcha_escalations_total", {"type": type_value})
    return value or 0.0


# ------------------------------------------------------------------- domain model


def test_needs_browser_is_its_own_non_terminal_code() -> None:
    err = NeedsBrowserError("needs a browser")
    assert isinstance(err, CaptchaError)
    assert err.code == "ERR_NEEDS_BROWSER"
    assert err.to_dict()["code"] == "ERR_NEEDS_BROWSER"
    assert not isinstance(err, UnsolvableError)


def test_lane_hops_defaults_to_zero_and_survives_msgpack() -> None:
    envelope = _envelope()
    assert envelope.lane_hops == 0
    decoded = _ENVELOPE_DECODER.decode(msgspec.msgpack.encode(envelope))
    assert decoded.lane_hops == 0


def test_escalation_spends_exactly_one_hop() -> None:
    envelope = _envelope()
    once = envelope.escalated()
    assert once.lane_hops == 1
    assert once.task.id == envelope.task.id
    with pytest.raises(UnsolvableError) as exc:
        once.escalated()
    assert exc.value.context["lane_hops"] == MAX_LANE_HOPS == 1


# --------------------------------------------------------------------- transport


async def test_producer_publishes_escalated_envelope_unchanged() -> None:
    redis = FakeStreamRedis()
    producer = Producer(redis)  # type: ignore[arg-type]

    await producer.publish(_envelope(lane_hops=1), lane="browser")

    stream, fields = redis.xadds[0]
    assert stream == "captcha:stream:browser"
    assert _ENVELOPE_DECODER.decode(fields[b"data"]).lane_hops == 1


async def test_consumer_requeue_keeps_lane_hops() -> None:
    redis = FakeStreamRedis()
    consumer = Consumer(
        redis,  # type: ignore[arg-type]
        lane="browser",
        consumer_name="w-0",
        prefix="captcha",
        max_retries=3,
    )

    dead = await consumer.fail("1-0", _envelope(lane_hops=1), reason="ERR_TIMEOUT")

    assert dead is False
    assert redis.acked == ["1-0"]
    stream, fields = redis.xadds[0]
    assert stream == "captcha:stream:browser"
    bumped = _ENVELOPE_DECODER.decode(fields[b"data"])
    assert bumped.retries == 1
    assert bumped.lane_hops == 1


# ----------------------------------------------------------------------- routing


def test_registry_mode_filters_transport() -> None:
    registry = _registry(BrowserSolver, HttpNeedsBrowser)

    assert registry.pick(TYPE, mode=SolveMode.HTTP) is HttpNeedsBrowser
    assert registry.pick(TYPE, mode=SolveMode.BROWSER) is BrowserSolver
    # AUTO keeps the historical preference: first registered wins.
    assert registry.pick(TYPE, mode=SolveMode.AUTO) is BrowserSolver

    browser_only = _registry(BrowserSolver)
    assert browser_only.pick(TYPE, mode=SolveMode.HTTP) is None
    assert browser_only.pick(TYPE, mode=SolveMode.BROWSER) is BrowserSolver


def test_registry_still_hides_experimental_by_default() -> None:
    class Experimental(HttpNeedsBrowser):
        info = SolverInfo(name="fake-experimental", types=(TYPE,), http=True, experimental=True)

    registry = _registry(Experimental)
    assert registry.pick(TYPE, mode=SolveMode.HTTP) is None
    assert registry.pick(TYPE, mode=SolveMode.HTTP, allow_experimental=True) is Experimental


async def test_http_lane_escalates_and_acks_source_without_billing() -> None:
    HttpNeedsBrowser.calls = 0
    state, persistence = FakeState(), FakePersistence()
    consumer, producer = FakeConsumer(), FakeProducer()
    worker = _worker(
        registry=_registry(BrowserSolver, HttpNeedsBrowser),  # browser first on purpose
        consumer=consumer,
        producer=producer,
        state=state,
        persistence=persistence,
    )
    before = _metric("hcaptcha")
    envelope = _envelope()

    await worker._dispatch("1-0", envelope, time.time())

    assert HttpNeedsBrowser.calls == 1, "HTTP solver tried once, browser solver untouched"
    assert consumer.acked == ["1-0"], "source entry acked after the browser copy was published"
    assert consumer.failed == [], "escalation is not a retry"
    assert len(producer.published) == 1
    next_envelope, lane = producer.published[0]
    assert lane == "browser"
    assert next_envelope.lane_hops == 1
    assert next_envelope.task.id == envelope.task.id
    assert state.failed == [], "escalation must not mark the task failed"
    assert persistence.failed_calls == [], "no refund and no second charge on reroute"
    assert persistence.processing_calls == [(envelope.task.id, "http-0")]
    assert state.ready == []
    assert _metric("hcaptcha") == before + 1


async def test_second_needs_browser_is_terminal_unsolvable() -> None:
    HttpNeedsBrowser.calls = 0
    state, persistence = FakeState(), FakePersistence()
    consumer, producer = FakeConsumer(), FakeProducer()
    worker = _worker(
        registry=_registry(HttpNeedsBrowser),
        consumer=consumer,
        producer=producer,
        state=state,
        persistence=persistence,
    )

    await worker._dispatch("1-0", _envelope(lane_hops=MAX_LANE_HOPS), time.time())

    assert producer.published == [], "budget is one hop, a second reroute is refused"
    assert consumer.acked == ["1-0"]
    assert state.failed and state.failed[0][1]["code"] == "ERR_UNSOLVABLE"
    assert persistence.failed_calls and persistence.failed_calls[0]["refund"] is True


async def test_failed_publish_retries_instead_of_dropping() -> None:
    HttpNeedsBrowser.calls = 0
    consumer = FakeConsumer()
    worker = _worker(
        registry=_registry(HttpNeedsBrowser),
        consumer=consumer,
        producer=FakeProducer(boom=True),
        state=FakeState(),
        persistence=FakePersistence(),
    )
    envelope = _envelope()

    await worker._dispatch("1-0", envelope, time.time())

    assert consumer.acked == [], "not acked: the source entry is still ours"
    assert [entry[2] for entry in consumer.failed] == ["ERR_NEEDS_BROWSER"]
    assert envelope.lane_hops == 0


async def test_browser_only_solver_is_not_picked_by_http_lane() -> None:
    state, persistence = FakeState(), FakePersistence()
    consumer = FakeConsumer()
    worker = _worker(
        registry=_registry(BrowserSolver),
        consumer=consumer,
        state=state,
        persistence=persistence,
    )

    await worker._dispatch("1-0", _envelope(ct=TYPE), time.time())

    assert consumer.acked == ["1-0"]
    assert state.failed and state.failed[0][1]["code"] == "ERR_UNSOLVABLE"
    assert persistence.failed_calls and persistence.failed_calls[0]["refund"] is True


async def test_http_success_still_works_with_mode_filtering() -> None:
    state, persistence = FakeState(), FakePersistence()
    consumer = FakeConsumer()
    worker = _worker(
        registry=_registry(HttpOk),
        consumer=consumer,
        state=state,
        persistence=persistence,
    )

    await worker._dispatch("1-0", _envelope(ct=CaptchaType.TURNSTILE), time.time())

    assert consumer.acked == ["1-0"]
    assert state.ready and state.ready[0][1].token == "solved"
    assert persistence.failed_calls == []
