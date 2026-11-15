"""createTask -> getTaskResult load scenario with open-loop pacing.

One virtual task is: POST /createTask, then poll the result until `ready` or
`failed`, backing off between polls. The scheduler is open loop — it offers a
fixed rate regardless of how fast the server answers — because a closed-loop
generator hides saturation by simply pushing less.

When the in-flight cap is reached the new sample is dropped and counted
(`dropped_overload`): an open-loop run that cannot keep up must say so, not
silently degenerate into a slower closed-loop one.

Everything measured here is client-observed: gateway latency, queue depth read
from Redis (`--redis-url`), and end-to-end create->ready. No live captcha is
involved when the run targets `devstack`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import LoadConfig
from .queue_probe import QueueProbe, lane_for_type
from .stats import ErrorTally, LatencyDigest, LoadResult, TimeBuckets

#: Scheduler tick. At 1k/s that is 10 tasks per tick; short enough to pace
#: evenly, long enough not to spin the event loop.
TICK_SEC = 0.01

ProgressFn = Callable[[dict[str, Any]], None]


def _make_client(cfg: LoadConfig) -> httpx.AsyncClient:
    cap = cfg.in_flight_cap
    return httpx.AsyncClient(
        base_url=cfg.url.rstrip("/"),
        timeout=httpx.Timeout(cfg.timeout),
        limits=httpx.Limits(max_connections=cap + 64, max_keepalive_connections=cap + 64),
        headers={"accept": "application/json", "user-agent": "captcha-load/1.0"},
    )


@dataclass(slots=True)
class Session:
    """Mutable run state: counters, digests and the per-request flow."""

    cfg: LoadConfig
    client: httpx.AsyncClient
    start: float
    created: int = 0
    ready: int = 0
    failed: int = 0
    solve_timeout: int = 0
    polls: int = 0
    create_lat: LatencyDigest = field(default_factory=LatencyDigest)
    poll_lat: LatencyDigest = field(default_factory=LatencyDigest)
    e2e_lat: LatencyDigest = field(default_factory=LatencyDigest)
    errors: ErrorTally = field(default_factory=ErrorTally)
    buckets: TimeBuckets = field(default_factory=TimeBuckets)
    payload: dict[str, Any] = field(default_factory=dict)

    def offset(self) -> float:
        return time.perf_counter() - self.start

    def _fail(self, phase: str, kind: str) -> None:
        self.errors.add(phase, kind)
        self.buckets.at(self.offset()).errors += 1

    async def one_task(self) -> None:
        t0 = time.perf_counter()
        try:
            response = await self.client.post("/createTask", json=self.payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail("createTask", exc.__class__.__name__)
            return
        self.create_lat.observe(time.perf_counter() - t0)
        bucket = self.buckets.at(self.offset())
        bucket.created += 1
        self.created += 1

        if response.status_code != 200:
            self._fail("createTask", f"http_{response.status_code}")
            return
        try:
            body = response.json()
        except Exception:
            self._fail("createTask", "bad_json")
            return
        if int(body.get("errorId") or 0) != 0:
            self._fail("createTask", str(body.get("errorCode") or "error_id"))
            return
        task_id = body.get("taskId")
        if not task_id:
            self._fail("createTask", "no_task_id")
            return

        await self._poll(str(task_id), t0)

    async def _poll(self, task_id: str, t0: float) -> None:
        delay = 0.0
        deadline = t0 + self.cfg.solve_timeout
        request = {"clientKey": self.cfg.client_key, "taskId": task_id}
        while True:
            if delay > 0:
                await asyncio.sleep(delay)
            if time.perf_counter() > deadline:
                self.solve_timeout += 1
                self._fail("getTaskResult", "solve_timeout")
                return
            p0 = time.perf_counter()
            try:
                response = await self.client.post("/getTaskResult", json=request)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._fail("getTaskResult", exc.__class__.__name__)
                delay = min(self.cfg.poll_max_interval, (delay or self.cfg.poll_interval) * 2)
                continue
            self.poll_lat.observe(time.perf_counter() - p0)
            self.polls += 1

            if response.status_code != 200:
                self._fail("getTaskResult", f"http_{response.status_code}")
                return
            try:
                body = response.json()
            except Exception:
                self._fail("getTaskResult", "bad_json")
                return
            if int(body.get("errorId") or 0) != 0:
                self._fail("getTaskResult", str(body.get("errorCode") or "error_id"))
                return

            status = body.get("status")
            bucket = self.buckets.at(self.offset())
            if status == "ready":
                self.ready += 1
                bucket.ready += 1
                e2e = time.perf_counter() - t0
                self.e2e_lat.observe(e2e)
                bucket.e2e.append(e2e)
                return
            if status == "failed":
                self.failed += 1
                bucket.failed += 1
                self._fail("getTaskResult", str(body.get("errorCode") or "task_failed"))
                return

            delay = min(self.cfg.poll_max_interval, (delay or self.cfg.poll_interval) * 1.5)


async def run_load(
    cfg: LoadConfig,
    *,
    client: httpx.AsyncClient | None = None,
    on_progress: ProgressFn | None = None,
) -> LoadResult:
    """Offer `cfg.rate` createTask/s for `cfg.duration` seconds, then drain.

    `client` exists for tests (an ASGI transport or a mocked one); in normal use
    the driver builds its own and closes it.
    """
    started_wall = datetime.now(UTC).isoformat(timespec="seconds")
    start = time.perf_counter()
    owns_client = client is None
    http = client or _make_client(cfg)
    lane = cfg.lane or lane_for_type(cfg.captcha_type)
    probe = QueueProbe(redis_url=cfg.redis_url, lane=lane, prefix=cfg.redis_prefix)
    session = Session(cfg=cfg, client=http, start=start, payload=cfg.payload())
    cap = cfg.in_flight_cap

    pending: set[asyncio.Task[None]] = set()
    dropped = 0
    spawned = 0
    offered = 0.0
    last_progress = start

    await probe.start()
    try:
        while True:
            elapsed = time.perf_counter() - start
            if elapsed >= cfg.duration:
                break
            due = int(elapsed * cfg.rate) + 1
            while spawned < due:
                spawned += 1
                if len(pending) >= cap:
                    dropped += 1
                    continue
                task = asyncio.create_task(session.one_task())
                pending.add(task)
                task.add_done_callback(pending.discard)
            if on_progress is not None and cfg.progress_sec > 0:
                now = time.perf_counter()
                if now - last_progress >= cfg.progress_sec:
                    last_progress = now
                    on_progress(
                        {
                            "elapsed": round(now - start, 1),
                            "created": session.created,
                            "ready": session.ready,
                            "errors": session.errors.total,
                            "in_flight": len(pending),
                            "dropped": dropped,
                        }
                    )
            await asyncio.sleep(min(TICK_SEC, max(0.0, cfg.duration - elapsed)))

        offered = time.perf_counter() - start
        drain_until = time.perf_counter() + cfg.solve_timeout + 5.0
        while pending and time.perf_counter() < drain_until:
            await asyncio.sleep(0.05)
    finally:
        unfinished = sum(1 for task in pending if not task.done())
        for task in list(pending):
            task.cancel()
        if pending:
            with contextlib.suppress(Exception):
                await asyncio.wait(list(pending), timeout=5.0)
        await probe.stop()
        if owns_client:
            with contextlib.suppress(Exception):
                await http.aclose()

    return LoadResult(
        started_at=started_wall,
        config=cfg.as_dict(),
        duration=offered,
        elapsed=time.perf_counter() - start,
        created=session.created,
        ready=session.ready,
        failed=session.failed,
        solve_timeout=session.solve_timeout,
        unfinished=unfinished,
        dropped=dropped,
        polls=session.polls,
        create=session.create_lat.stats(),
        poll=session.poll_lat.stats(),
        e2e=session.e2e_lat.stats(),
        errors=session.errors.as_dict(),
        buckets=session.buckets.rows(),
        queue=probe.summary(),
        label=cfg.label,
    )
