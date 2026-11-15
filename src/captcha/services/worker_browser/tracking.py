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
from contextlib import asynccontextmanager
from typing import Any

import structlog

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

    def __init__(self, inner: Any, tracker: SessionTracker) -> None:
        self._inner = inner
        self._tracker = tracker
        self._active = 0

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
        cm = self._inner.session(spec) if spec is not None else self._inner.session()
        async with cm as session:
            job_id = getattr(session, "job_id", None)
            if job_id:
                self._tracker.track(label, str(job_id))
            self._active += 1
            try:
                yield session
            finally:
                self._active -= 1
                if job_id:
                    self._tracker.forget(label, str(job_id))
