"""Concurrency gate: session cap + RAM guard + ramp-up.

Contract (§4): `WORKER_BROWSER_SESSIONS=12`, `WORKER_BROWSER_MIN_FREE_MB=2048`;
one active page costs roughly 700 MB, so a fixed semaphore either wastes RAM or
OOMs the host. The gate starts at `ramp_start` sessions, grows one at a time
after `ramp_every` clean completions, and refuses to grow (or start new work)
while free RAM is under the guard.

Free-RAM probing is best-effort and platform-aware. If the number cannot be
measured the guard is treated as satisfied — refusing to start a worker because
`/proc` is odd is worse than running without the guard.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import sys
import time
from collections.abc import Callable
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_MEMINFO = Path("/proc/meminfo")
_MEMORY_CACHE_SEC = 1.0


class _MemoryStatusEx(ctypes.Structure):  # pragma: no cover - Windows only
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def free_memory_mb() -> int | None:
    """Available physical RAM in MiB, or `None` when the platform won't say."""
    if sys.platform == "win32":
        return _windows_free_mb()
    if _MEMINFO.exists():
        return _proc_free_mb()
    return None


def _proc_free_mb() -> int | None:
    try:
        for line in _MEMINFO.read_text(encoding="ascii", errors="ignore").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _windows_free_mb() -> int | None:  # pragma: no cover - exercised on Windows hosts
    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPhys) // (1024 * 1024)
    except Exception:
        return None


class ConcurrencyGate:
    """Admission control for browser sessions.

    `await gate.acquire()` blocks until a slot exists *and* the RAM guard allows
    another browser; `gate.release(ok=...)` frees it and feeds the ramp.
    Passing a `stop` event makes both waits return `False` on shutdown.
    """

    def __init__(
        self,
        target: int,
        *,
        min_free_mb: int = 0,
        ramp_start: int = 1,
        ramp_step: int = 1,
        ramp_every: int = 8,
        free_mb_fn: Callable[[], int | None] = free_memory_mb,
        poll_sec: float = 0.25,
    ) -> None:
        if target <= 0:
            raise ValueError("target must be positive")
        self._target = target
        self._min_free_mb = max(0, min_free_mb)
        self._limit = max(1, min(ramp_start, target))
        self._ramp_step = max(1, ramp_step)
        self._ramp_every = max(1, ramp_every)
        self._free_mb_fn = free_mb_fn
        self._poll = poll_sec
        self._active = 0
        self._clean_streak = 0
        self._cond = asyncio.Condition()
        self._cached_free_mb: int | None = None
        self._cached_at = 0.0
        self._low_logged = False

    @property
    def target(self) -> int:
        return self._target

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    def snapshot(self) -> dict[str, int | None]:
        return {
            "active": self._active,
            "limit": self._limit,
            "target": self._target,
            "free_mb": self._read_free_mb(),
            "min_free_mb": self._min_free_mb,
        }

    def _read_free_mb(self) -> int | None:
        now = time.monotonic()
        if now - self._cached_at < _MEMORY_CACHE_SEC:
            return self._cached_free_mb
        self._cached_at = now
        self._cached_free_mb = self._free_mb_fn()
        return self._cached_free_mb

    def memory_ok(self) -> bool:
        if self._min_free_mb <= 0:
            return True
        free = self._read_free_mb()
        if free is None:
            return True
        ok = free >= self._min_free_mb
        if not ok and not self._low_logged:
            self._low_logged = True
            log.warning("gate.memory_low", free_mb=free, min_free_mb=self._min_free_mb)
        elif ok and self._low_logged:
            self._low_logged = False
            log.info("gate.memory_recovered", free_mb=free, min_free_mb=self._min_free_mb)
        return ok

    async def wait_for_memory(self, *, stop: asyncio.Event | None = None) -> bool:
        """Block while the RAM guard is tripped. Returns `False` if told to stop."""
        while not self.memory_ok():
            if stop is not None and stop.is_set():
                return False
            await asyncio.sleep(self._poll)
        return True

    async def acquire(self, *, stop: asyncio.Event | None = None) -> bool:
        """Take one slot. Returns `False` when shutdown interrupted the wait."""
        async with self._cond:
            while True:
                if stop is not None and stop.is_set():
                    return False
                if self._active < self._limit and self.memory_ok():
                    self._active += 1
                    return True
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._cond.wait(), timeout=self._poll)

    def release(self, *, ok: bool = True) -> None:
        """Free one slot. A clean completion counts towards the next ramp step."""
        if self._active > 0:
            self._active -= 1
        if ok:
            self._clean_streak += 1
            if self._clean_streak >= self._ramp_every and self._limit < self._target:
                self._limit = min(self._target, self._limit + self._ramp_step)
                self._clean_streak = 0
                log.info("gate.ramp_up", limit=self._limit, target=self._target)
        else:
            self._clean_streak = 0
        self._wake()

    def _wake(self) -> None:
        """Sync `release` cannot await the condition lock; schedule the notify."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - no loop means no waiters
            return
        loop.call_soon(lambda: asyncio.ensure_future(self._notify()))

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()
