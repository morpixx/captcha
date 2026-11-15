"""Process-tree supervisor for browser sessions.

Contract (§4): the per-task hard timeout must kill the whole process tree and
leave zero orphans. The provider owns the browser processes, the worker owns the
deadline, so the worker records the root pid of every session a solver opens
(`TrackingProvider` below) and kills that pid plus all descendants on timeout
and on shutdown.

Everything is best-effort: a provider that does not expose a pid is still
cleaned up by its own `session()` context manager; the miss is logged, never
raised. Windows uses `taskkill /T /F`; POSIX walks `/proc` for descendants.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Browser-core names per-task profiles `<temp>/captcha-browser-*`; reap those.
PROFILE_GLOB = "captcha-browser-*"
PROFILE_TTL_SEC = 3600.0
_KILL_TIMEOUT_SEC = 15.0

_current_label: contextvars.ContextVar[str] = contextvars.ContextVar("captcha_browser_task_label", default="")


def task_label(task: Any) -> str:
    """Filesystem-safe per-task label used for pid tracking and profile dirs."""
    return str(getattr(task, "id", task))


def current_label() -> str:
    return _current_label.get()


def extract_pid(session: Any) -> int | None:
    """Best-effort root pid of a browser session; `None` when not exposed."""
    for attr in ("pid", "process_pid", "browser_pid"):
        value = getattr(session, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if isinstance(value, int) and value > 0:
            return value
    return None


class ProcessSupervisor:
    """Label -> pids registry with tree kills and profile reaping."""

    def __init__(
        self,
        *,
        profile_root: str | Path | None = None,
        profile_ttl_sec: float = PROFILE_TTL_SEC,
    ) -> None:
        self._pids: dict[str, set[int]] = {}
        self._profile_root = Path(profile_root) if profile_root is not None else Path(tempfile.gettempdir())
        self._profile_ttl = profile_ttl_sec

    # ---- tracking ------------------------------------------------------

    def track(self, label: str, pid: int) -> None:
        self._pids.setdefault(label, set()).add(pid)

    def pids(self, label: str) -> tuple[int, ...]:
        return tuple(sorted(self._pids.get(label, ())))

    def labels(self) -> tuple[str, ...]:
        return tuple(self._pids)

    def forget_pid(self, label: str, pid: int) -> None:
        bucket = self._pids.get(label)
        if bucket is None:
            return
        bucket.discard(pid)
        if not bucket:
            self._pids.pop(label, None)

    def forget(self, label: str) -> None:
        self._pids.pop(label, None)

    # ---- killing -------------------------------------------------------

    async def kill_label(self, label: str) -> int:
        """Kill every pid recorded for `label`; returns how many were signalled."""
        pids = self._pids.pop(label, set())
        killed = 0
        for pid in sorted(pids):
            killed += await self.kill_pid_tree(pid)
        if not pids:
            log.warning("supervisor.no_pid", label=label, hint="provider did not expose a session pid")
        return killed

    async def kill_all(self) -> int:
        killed = 0
        for label in list(self._pids):
            killed += await self.kill_label(label)
        return killed

    async def kill_pid_tree(self, pid: int) -> int:
        if pid <= 0:
            return 0
        try:
            return await asyncio.to_thread(self._kill_tree, pid)
        except Exception as exc:  # never let cleanup break the worker
            log.warning("supervisor.kill_failed", pid=pid, error=str(exc))
            return 0

    def _kill_tree(self, pid: int) -> int:
        if sys.platform == "win32":
            return _taskkill_tree(pid)
        return _posix_kill_tree(pid)

    # ---- profile reaping ----------------------------------------------

    def reap_profiles(self, *, now: float | None = None) -> int:
        """Delete stale `<temp>/captcha-browser-*` profile dirs; returns count."""
        if not self._profile_root.exists():
            return 0
        cutoff = (now if now is not None else time.time()) - self._profile_ttl
        removed = 0
        for path in self._profile_root.glob(PROFILE_GLOB):
            try:
                if path.stat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
            removed += 1
        if removed:
            log.info("supervisor.profiles_reaped", root=str(self._profile_root), removed=removed)
        return removed


class TrackingProvider:
    """Provider proxy that attributes each open session to the current task.

    Solvers receive this as `ctx.browser`, so the worker never wraps the session
    itself: it only observes sessions opening/closing and remembers their pids.
    Everything else is delegated to the real provider.
    """

    def __init__(self, inner: Any, supervisor: ProcessSupervisor) -> None:
        self._inner = inner
        self._supervisor = supervisor
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
            pid = extract_pid(session)
            if pid is not None:
                self._supervisor.track(label, pid)
            self._active += 1
            try:
                yield session
            finally:
                self._active -= 1
                if pid is not None:
                    self._supervisor.forget_pid(label, pid)


def _taskkill_tree(pid: int) -> int:
    try:
        proc = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=_KILL_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("supervisor.taskkill_failed", pid=pid, error=str(exc))
        return 0
    if proc.returncode == 0:
        log.info("supervisor.killed", pid=pid, backend="taskkill")
        return 1
    return 0


def _posix_kill_tree(pid: int) -> int:
    killed = 0
    victims = [pid, *_descendants(pid)]
    with suppress(OSError):
        pgid = os.getpgid(pid)
        if pgid == pid and pgid != os.getpgid(0):
            os.killpg(pgid, signal.SIGKILL)
            killed += 1
    for victim in victims:
        with suppress(OSError):
            os.kill(victim, signal.SIGKILL)
            killed += 1
    if killed:
        log.info("supervisor.killed", pid=pid, backend="posix", count=killed)
    return killed


def _descendants(root: int) -> list[int]:
    children: dict[int, list[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            stat = Path("/proc", entry, "stat").read_text(encoding="ascii", errors="ignore")
        except OSError:
            continue
        # Field 2 (comm) may contain spaces and parens; ppid follows the last ')'.
        try:
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (IndexError, ValueError):
            continue
        children.setdefault(ppid, []).append(int(entry))
    found: list[int] = []
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), ()):
            found.append(child)
            stack.append(child)
    return found
