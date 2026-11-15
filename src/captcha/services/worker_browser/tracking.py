"""Session attribution for the browser lane.

`CamoufoxProvider` owns browser processes and temp profiles, keyed by its own
`job_id` (`captcha.adapters.browser.supervisor.Supervisor`). The worker needs a
different key — the task — so `SessionTracker` maps label -> live job ids and
`TrackingProvider` observes every session a solver opens.

The runner kills a task's tree by label on a hard timeout; the worker kills every
leftover job on shutdown. Killing itself stays in browser-core's supervisor:
this module only knows which job ids belong to which task.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from contextlib import asynccontextmanager, suppress
from typing import Any

import structlog

from captcha.observability import metrics

log = structlog.get_logger(__name__)

_current_label: contextvars.ContextVar[str] = contextvars.ContextVar("captcha_browser_task_label", default="")


def task_label(task: Any) -> str:
    """Per-task label used to attribute browser sessions."""
    return str(getattr(task, "id", task))


def bind_label(label: str) -> contextvars.Token[str]:
    """Mark the current task's label; each asyncio task owns its own context."""
    return _current_label.set(label)


def reset_label(token: contextvars.Token[str]) -> None:
    _current_label.reset(token)


def current_label() -> str:
    return _current_label.get()


class SessionTracker:
    """label -> job ids of live sessions, plus the kill path for them."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self._jobs: dict[str, set[str]] = {}

    @property
    def provider(self) -> Any:
        return self._provider

    @property
    def active(self) -> int:
        return sum(len(bucket) for bucket in self._jobs.values())

    def track(self, label: str, job_id: str) -> None:
        self._jobs.setdefault(label, set()).add(job_id)

    def forget(self, label: str, job_id: str) -> None:
        bucket = self._jobs.get(label)
        if bucket is None:
            return
        bucket.discard(job_id)
        if not bucket:
            self._jobs.pop(label, None)

    def jobs(self, label: str) -> tuple[str, ...]:
        return tuple(sorted(self._jobs.get(label, ())))

    def labels(self) -> tuple[str, ...]:
        return tuple(self._jobs)

    def rss_bytes(self) -> int:
        """Summed RSS of every live session's process tree. 0 when unmeasurable.

        `Supervisor.processes()` may scan the process table, so the caller
        throttles this; it runs on a task boundary, never per scrape.
        """
        supervisor = getattr(self._provider, "supervisor", None)
        processes = getattr(supervisor, "processes", None)
        if not callable(processes):
            return 0
        total = 0
        for bucket in list(self._jobs.values()):
            for job_id in bucket:
                try:
                    procs = processes(job_id)
                except Exception:
                    continue
                for proc in procs:
                    with suppress(Exception):
                        total += int(proc.memory_info().rss)
        return total

    async def kill_label(self, label: str) -> int:
        """Kill every session still open for `label`; returns the hit count."""
        return await self._kill(self._jobs.pop(label, set()))

    async def kill_all(self) -> int:
        """Shutdown path: kill every session this worker still tracks."""
        jobs = {job for bucket in self._jobs.values() for job in bucket}
        self._jobs.clear()
        return await self._kill(jobs)

    async def _kill(self, jobs: Any) -> int:
        if not jobs:
            return 0
        supervisor = getattr(self._provider, "supervisor", None)
        kill = getattr(supervisor, "kill", None)
        if not callable(kill):
            log.warning("browser.kill_unavailable", jobs=len(jobs), hint="provider exposes no supervisor")
            return 0
        killed = 0
        for job_id in sorted(jobs):
            try:
                killed += await asyncio.to_thread(kill, job_id)
            except Exception as exc:
                log.warning("browser.kill_failed", job_id=job_id, error=str(exc))
        return killed


class TrackingProvider:
    """Provider proxy that attributes each open session to the current task.

    Solvers receive this as `ctx.browser`, so the worker never wraps the session
    itself: it only observes sessions opening/closing and records their job ids.
    Everything else is delegated to the real provider.
    """

    def __init__(self, inner: Any, tracker: SessionTracker, *, rss_interval_sec: float = 15.0) -> None:
        self._inner = inner
        self._tracker = tracker
        self._active = 0
        self._rss_interval = rss_interval_sec
        self._last_rss_sample = 0.0

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    @property
    def inner(self) -> Any:
        return self._inner

    @property
    def active(self) -> int:
        return self._active

    @asynccontextmanager
    async def session(self, spec: Any = None):  # type: ignore[no-untyped-def]
        label = current_label()
        started = time.perf_counter()
        cm = self._inner.session(spec) if spec is not None else self._inner.session()
        async with cm as session:
            # Everything up to the first yield is launch (GeoIP, profile, start).
            metrics.record_browser_launch(time.perf_counter() - started)
            job_id = getattr(session, "job_id", None)
            if job_id:
                self._tracker.track(label, str(job_id))
            self._active += 1
            metrics.set_browser_sessions_active(self._active)
            try:
                yield session
            finally:
                self._active -= 1
                metrics.set_browser_sessions_active(self._active)
                if job_id:
                    self._tracker.forget(label, str(job_id))
                self._sample_rss()

    def _sample_rss(self) -> None:
        now = time.monotonic()
        if now - self._last_rss_sample < self._rss_interval:
            return
        sampler = getattr(self._tracker, "rss_bytes", None)
        if not callable(sampler):
            return
        self._last_rss_sample = now
        metrics.set_browser_rss_bytes(sampler())
