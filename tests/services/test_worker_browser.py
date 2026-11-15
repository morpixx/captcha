"""Browser-lane worker tests: fake provider, fake solver, fake consumer.

No Redis, no Postgres, no browser, no live captcha. Everything the worker touches
is injected, which is exactly the seam the production wiring uses.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from captcha.domain import SolveMode, SolverResult, Task, TaskEnvelope, UnsolvableError
from captcha.domain.enums import CaptchaType
from captcha.domain.errors import NeedsBrowserError
from captcha.services.worker_browser.gate import ConcurrencyGate
from captcha.services.worker_browser.heartbeat import LaneHeartbeat, cap_key, lane_key
from captcha.services.worker_browser.limits import (
    DEFAULT_CONCURRENCY,
    MAX_TIMEOUT_SEC,
    MIN_TIMEOUT_SEC,
    clamp_timeout,
)
from captcha.services.worker_browser.provider import resolve_provider
from captcha.services.worker_browser.tracking import (
    SessionTracker,
    TrackingProvider,
    bind_label,
    reset_label,
    task_label,
)
from captcha.services.worker_browser.worker import Worker
from captcha.solvers import Solver, SolverContext, SolverInfo, SolverRegistry

# ---------------------------------------------------------------- fixtures


def settings_for(tmp_path: Path, **overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "redis_url": "redis://localhost:6379/0",
        "redis_stream_prefix": "captcha",
        "worker_browser_sessions": 2,
        "worker_browser_headless": True,
        "worker_browser_min_free_mb": 0,
        "worker_browser_task_timeout_sec": 240,
        "worker_browser_ramp_start": 2,
        "proxy_pool_file": str(tmp_path / "no-pool.txt"),
        "log_level": "INFO",
        "log_json": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def envelope(
    *,
    task_type: CaptchaType = CaptchaType.HCAPTCHA,
    deadline_ts: float | None = None,
) -> TaskEnvelope:
    task = Task(
        id=uuid4(),
        type=task_type,
        payload={"sitekey": "site-key"},
        preferred_mode=SolveMode.BROWSER,
        created_at=time.time(),
        deadline_ts=deadline_ts,
    )
    return TaskEnvelope(task=task)


class FakeSession:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.closed = False


class FakeProvider:
    """Provider stub: counts opens/closes and peak concurrency."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.opened = 0
        self.closed = 0
        self.peak = 0
        self.active = 0
        self.supervisor: Any = None

    @contextlib.asynccontextmanager
    async def session(self, spec: Any = None):
        self.opened += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        session = FakeSession(job_id=f"job-{self.opened}")
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield session
        finally:
            self.active -= 1
            session.closed = True
            self.closed += 1


def solver_class(
    *,
    delay: float = 0.0,
    error: BaseException | None = None,
    open_session: bool = True,
    types: tuple[CaptchaType, ...] = (CaptchaType.HCAPTCHA,),
    name: str = "fake-browser",
) -> type[Solver]:
    class _FakeSolver(Solver):
        info = SolverInfo(name=name, types=types, http=False, browser=True)

        async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
            raise AssertionError("solve_http must never run on the browser lane")

        async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
            if open_session and ctx.browser is not None:
                async with ctx.browser.session(SimpleNamespace(label=str(task.id))) as _session:
                    if delay:
                        await asyncio.sleep(delay)
                    if error is not None:
                        raise error
            elif delay:
                await asyncio.sleep(delay)
            if error is not None:
                raise error
            return SolverResult(token="solved-token")

    return _FakeSolver


class FakeConsumer:
    def __init__(self, batches: list[list[tuple[str, TaskEnvelope]]], *, dead_on_fail: bool = False) -> None:
        self._batches = [list(batch) for batch in batches]
        self.dead_on_fail = dead_on_fail
        self.acked: list[str] = []
        self.failed: list[tuple[str, str, int]] = []
        self.expected = sum(len(batch) for batch in batches)
        self.done = asyncio.Event()

    def _settle(self) -> None:
        if len(self.acked) + len(self.failed) >= self.expected:
            self.done.set()

    async def read(self) -> list[tuple[str, TaskEnvelope]]:
        if self._batches:
            return self._batches.pop(0)
        await asyncio.sleep(0.01)
        return []

    async def ack(self, message_id: str) -> None:
        self.acked.append(message_id)
        self._settle()

    async def fail(self, message_id: str, envelope: TaskEnvelope, reason: str) -> bool:
        self.failed.append((message_id, reason, envelope.retries))
        self._settle()
        return self.dead_on_fail


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


class FakeHeartbeat:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.types: tuple[Any, ...] = ()

    def set_types(self, types: Any) -> None:
        self.types = tuple(types)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class FakeTracker:
    def __init__(self) -> None:
        self.killed_labels: list[str] = []
        self.kill_all_calls = 0

    def track(self, label: str, job_id: str) -> None:  # pragma: no cover - unused
        pass

    def forget(self, label: str, job_id: str) -> None:  # pragma: no cover - unused
        pass

    async def kill_label(self, label: str) -> int:
        self.killed_labels.append(label)
        return 1

    async def kill_all(self) -> int:
        self.kill_all_calls += 1
        return 0

    def rss_bytes(self) -> int:
        return 0


def build_worker(
    tmp_path: Path,
    *,
    solver: type[Solver] | None,
    batches: list[list[tuple[str, TaskEnvelope]]],
    provider: Any = None,
    tracker: Any = None,
    gate: ConcurrencyGate | None = None,
    timeout_sec: float | None = None,
    concurrency: int = 2,
    dead_on_fail: bool = False,
) -> tuple[Worker, FakeConsumer, FakeState, FakeHeartbeat, Any]:
    registry = SolverRegistry()
    if solver is not None:
        registry.register(solver)
    consumer = FakeConsumer(batches, dead_on_fail=dead_on_fail)
    state = FakeState()
    heartbeat = FakeHeartbeat()
    provider = provider if provider is not None else FakeProvider()
    worker = Worker(
        settings_for(tmp_path),
        worker_id="browser-w1",
        concurrency=concurrency,
        provider=provider,
        registry=registry,
        consumer=consumer,
        state=state,
        heartbeat=heartbeat,
        gate=gate,
        tracker=tracker if tracker is not None else FakeTracker(),
        timeout_sec=timeout_sec,
        install_signals=False,
    )
    return worker, consumer, state, heartbeat, provider


async def drive(worker: Worker, consumer: FakeConsumer, *, timeout: float = 5.0) -> None:
    run_task = asyncio.create_task(worker.run())
    await asyncio.wait_for(consumer.done.wait(), timeout)
    worker.request_stop()
    await asyncio.wait_for(run_task, timeout)


async def wait_until(predicate: Any, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------- worker


async def test_success_marks_ready_and_closes_session(tmp_path: Path) -> None:
    env = envelope()
    worker, consumer, state, heartbeat, provider = build_worker(
        tmp_path,
        solver=solver_class(),
        batches=[[("1-0", env)]],
    )
    await drive(worker, consumer)

    assert consumer.acked == ["1-0"]
    assert consumer.failed == []
    assert len(state.ready) == 1
    task_id, result = state.ready[0]
    assert task_id == env.task.id
    assert result.token == "solved-token"
    assert result.solve_mode == SolveMode.BROWSER
    assert provider.opened == 1 and provider.closed == 1
    assert heartbeat.started and heartbeat.stopped


async def test_solver_sees_tracking_provider(tmp_path: Path) -> None:
    seen: list[Any] = []

    class _Probe(solver_class()):  # type: ignore[misc, valid-type]
        async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
            seen.append(ctx.browser)
            return SolverResult(token="t")

    env = envelope()
    worker, consumer, state, _hb, _provider = build_worker(tmp_path, solver=_Probe, batches=[[("1-0", env)]])
    await drive(worker, consumer)

    assert isinstance(seen[0], TrackingProvider)
    assert state.ready and state.ready[0][1].token == "t"


async def test_missing_solver_is_terminal_unsolvable(tmp_path: Path) -> None:
    env = envelope(task_type=CaptchaType.RECAPTCHA_V2)
    worker, consumer, state, _hb, _provider = build_worker(
        tmp_path,
        solver=solver_class(types=(CaptchaType.HCAPTCHA,)),
        batches=[[("2-0", env)]],
    )
    await drive(worker, consumer)

    assert consumer.acked == ["2-0"]
    assert consumer.failed == []
    assert len(state.failed) == 1
    assert state.failed[0][1]["code"] == UnsolvableError.code


async def test_hard_timeout_kills_tree_and_releases_slot(tmp_path: Path) -> None:
    tracker = FakeTracker()
    env = envelope()
    worker, consumer, state, _hb, _provider = build_worker(
        tmp_path,
        solver=solver_class(delay=30.0),
        batches=[[("3-0", env)]],
        tracker=tracker,
        timeout_sec=0.15,
    )
    started = time.monotonic()
    await drive(worker, consumer, timeout=10.0)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, "hard timeout must not wait for the solver"
    assert consumer.failed and consumer.failed[0][1] == "ERR_TIMEOUT"
    assert tracker.killed_labels == [task_label(env.task)]
    assert worker._gate is not None and worker._gate.active == 0
    # Retry path: state stays 'processing' until the consumer dead-letters.
    assert state.failed == []


async def test_timeout_dead_letter_sets_err_timeout(tmp_path: Path) -> None:
    env = envelope()
    worker, consumer, state, _hb, _provider = build_worker(
        tmp_path,
        solver=solver_class(delay=30.0),
        batches=[[("4-0", env)]],
        timeout_sec=0.1,
        dead_on_fail=True,
    )
    await drive(worker, consumer, timeout=10.0)

    assert len(state.failed) == 1
    assert state.failed[0][1]["code"] == "ERR_TIMEOUT"


async def test_needs_browser_in_browser_lane_is_terminal(tmp_path: Path) -> None:
    env = envelope()
    worker, consumer, state, _hb, _provider = build_worker(
        tmp_path,
        solver=solver_class(error=NeedsBrowserError("ask http again")),
        batches=[[("5-0", env)]],
    )
    await drive(worker, consumer)

    assert consumer.acked == ["5-0"]
    assert consumer.failed == []
    assert state.failed[0][1]["code"] == "ERR_UNSOLVABLE"


async def test_drain_finishes_inflight_before_shutdown(tmp_path: Path) -> None:
    first, second = envelope(), envelope()
    provider = FakeProvider()
    worker, consumer, state, _hb, _provider = build_worker(
        tmp_path,
        solver=solver_class(delay=0.1),
        batches=[[("6-0", first), ("6-1", second)]],
        provider=provider,
        concurrency=2,
    )
    run_task = asyncio.create_task(worker.run())
    await wait_until(lambda: provider.opened == 2)
    worker.request_stop()
    await asyncio.wait_for(run_task, timeout=10.0)

    assert {tid for tid, _ in state.ready} == {first.task.id, second.task.id}
    assert consumer.acked == ["6-0", "6-1"]
    assert provider.closed == 2
    assert provider.active == 0


async def test_concurrency_capped_by_sessions(tmp_path: Path) -> None:
    batches = [[(f"7-{i}", envelope())] for i in range(6)]
    provider = FakeProvider()
    worker, consumer, state, _hb, _provider = build_worker(
        tmp_path,
        solver=solver_class(delay=0.05),
        batches=batches,
        provider=provider,
        concurrency=2,
    )
    await drive(worker, consumer, timeout=10.0)

    assert len(state.ready) == 6
    assert provider.peak <= 2, f"peak sessions {provider.peak} exceeded cap 2"


async def test_teardown_kills_leftover_sessions(tmp_path: Path) -> None:
    tracker = FakeTracker()
    worker, consumer, _state, _hb, _provider = build_worker(
        tmp_path,
        solver=solver_class(),
        batches=[[("8-0", envelope())]],
        tracker=tracker,
    )
    await drive(worker, consumer)

    assert tracker.kill_all_calls == 1


# ---------------------------------------------------------------- gate


async def test_gate_ramps_up_after_clean_completions() -> None:
    gate = ConcurrencyGate(4, ramp_start=1, ramp_every=2, min_free_mb=0)

    assert gate.limit == 1
    assert await gate.acquire() is True
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(gate.acquire(), timeout=0.05)

    gate.release(ok=True)
    gate.release(ok=True)
    await asyncio.sleep(0)
    assert gate.limit == 2
    assert await gate.acquire() is True
    assert await gate.acquire() is True


async def test_gate_memory_guard_and_stop() -> None:
    gate = ConcurrencyGate(2, ramp_start=2, min_free_mb=2048, free_mb_fn=lambda: 128)
    stop = asyncio.Event()

    assert gate.memory_ok() is False
    assert gate.snapshot()["free_mb"] == 128
    stop.set()
    assert await gate.wait_for_memory(stop=stop) is False
    assert await gate.acquire(stop=stop) is False

    healthy = ConcurrencyGate(1, ramp_start=1, min_free_mb=2048, free_mb_fn=lambda: 4096)
    assert healthy.memory_ok() is True
    assert await healthy.acquire() is True
    healthy.release(ok=True)


# ---------------------------------------------------------------- heartbeat


class FakeRedis:
    def __init__(self) -> None:
        self.setex_calls: list[tuple[str, int, Any]] = []
        self.sadd_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.deleted: list[str] = []
        self.keys: set[str] = set()

    async def setex(self, key: str, ttl: int, value: Any) -> None:
        self.setex_calls.append((key, ttl, value))
        self.keys.add(key)

    async def sadd(self, key: str, *values: Any) -> None:
        self.sadd_calls.append((key, values))

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.keys.discard(key)

    async def scan_iter(self, match: str | None = None, count: int | None = None):
        for key in list(self.keys):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


async def test_heartbeat_sets_lease_and_capabilities() -> None:
    redis = FakeRedis()
    beat = LaneHeartbeat(
        redis,
        worker_id="browser-w1",
        prefix="captcha",
        types=(CaptchaType.HCAPTCHA, CaptchaType.RECAPTCHA_V2),
        interval_sec=3600,
    )

    assert await beat.beat() is True
    assert redis.setex_calls == [(lane_key("captcha", "browser", "browser-w1"), 90, "1")]
    assert redis.sadd_calls == [(cap_key("captcha", "browser"), ("hcaptcha", "recaptcha_v2"))]

    await beat.start()
    await beat.stop()
    assert redis.deleted[0] == lane_key("captcha", "browser", "browser-w1")


# ---------------------------------------------------------------- tracking


class FakeKillableSupervisor:
    def __init__(self) -> None:
        self.killed: list[str] = []

    def kill(self, job_id: str) -> int:
        self.killed.append(job_id)
        return 1


async def test_tracking_provider_attributes_sessions_to_tasks() -> None:
    provider = FakeProvider()
    provider.supervisor = FakeKillableSupervisor()
    tracker = SessionTracker(provider)
    tracked = TrackingProvider(provider, tracker)

    token = bind_label("task-9")
    try:
        async with tracked.session(SimpleNamespace(label="x")) as session:
            assert tracker.jobs("task-9") == (session.job_id,)
        assert tracker.jobs("task-9") == ()
    finally:
        reset_label(token)

    tracker.track("task-9", "job-9")
    assert await tracker.kill_label("task-9") == 1
    assert provider.supervisor.killed == ["job-9"]


async def test_tracker_kill_all_drops_every_job() -> None:
    provider = FakeProvider()
    provider.supervisor = FakeKillableSupervisor()
    tracker = SessionTracker(provider)
    tracker.track("a", "job-a")
    tracker.track("b", "job-b")

    assert await tracker.kill_all() == 2
    assert sorted(provider.supervisor.killed) == ["job-a", "job-b"]
    assert tracker.labels() == ()


# ---------------------------------------------------------------- provider


async def test_resolve_provider_builds_camoufox_provider() -> None:
    from captcha.adapters.browser.provider import CamoufoxProvider

    provider = resolve_provider(SimpleNamespace(worker_browser_sessions=12), headless=False)
    assert isinstance(provider, CamoufoxProvider)
    assert provider._headless is False


def test_resolve_provider_prefers_explicit_instance() -> None:
    sentinel = object()
    assert resolve_provider(SimpleNamespace(), provider=sentinel) is sentinel


def test_contract_defaults_and_timeout_window() -> None:
    assert DEFAULT_CONCURRENCY == 12
    assert clamp_timeout(10) == MIN_TIMEOUT_SEC
    assert clamp_timeout(1000) == MAX_TIMEOUT_SEC
    assert clamp_timeout(240) == 240


def test_task_label_is_filesystem_safe() -> None:
    env = envelope()
    label = task_label(env.task)
    assert label == str(env.task.id)
    assert os.sep not in label
