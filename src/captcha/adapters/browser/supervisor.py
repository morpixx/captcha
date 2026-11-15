"""Process and profile lifecycle for browser sessions.

Every session gets a temporary profile and a process tree. Both leak when a
task dies badly (timeout, cancelled coroutine, worker crash), so all of them
are registered under a job id and can be killed or reaped from one place.
`kill_all` is what shutdown calls; `reap_stale` is what a fresh process calls
to clean up after a crashed one.

`psutil` walks the tree because Playwright's context handle does not expose a
PID: the browser is located by the one string it always carries, the profile
path in its command line.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

PROFILE_PREFIX = "captcha-browser-"
PROFILE_GLOB = PROFILE_PREFIX + "*"
BROWSER_PROCESS_NAMES: tuple[str, ...] = ("camoufox", "firefox")
REMOVE_ATTEMPTS = 10
REMOVE_DELAY_SEC = 0.2


@dataclass
class _Job:
    profile: Path
    pid: int | None = None
    created_at: float = 0.0


class Supervisor:
    """Registry of live browser jobs: `job_id -> profile + optional pid`."""

    def __init__(
        self,
        *,
        temp_root: str | Path | None = None,
        prefix: str = PROFILE_PREFIX,
        process_names: tuple[str, ...] = BROWSER_PROCESS_NAMES,
    ) -> None:
        self._temp_root = Path(temp_root) if temp_root is not None else Path(tempfile.gettempdir())
        self._prefix = prefix
        self._process_names = process_names
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    @property
    def temp_root(self) -> Path:
        return self._temp_root

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def active_jobs(self) -> dict[str, Path]:
        with self._lock:
            return {job_id: job.profile for job_id, job in self._jobs.items()}

    def register(self, job_id: str, profile: str | Path, *, pid: int | None = None) -> Path:
        """Track a profile (and the browser root pid, when known) under `job_id`."""
        path = Path(profile)
        with self._lock:
            self._jobs[job_id] = _Job(profile=path, pid=pid, created_at=time.time())
        return path

    def unregister(self, job_id: str) -> Path | None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        return job.profile if job is not None else None

    def profile_for(self, job_id: str) -> Path | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return job.profile if job is not None else None

    # ---- processes -----------------------------------------------------

    def processes(self, job_id: str) -> list[psutil.Process]:
        """Root browser processes for a job: explicit pid first, then profile scan."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return []
        if job.pid is not None:
            try:
                return [psutil.Process(job.pid)]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return []
        return self.find_processes(job.profile, names=self._process_names)

    @staticmethod
    def find_processes(
        profile: str | Path,
        *,
        names: tuple[str, ...] = BROWSER_PROCESS_NAMES,
    ) -> list[psutil.Process]:
        """Find browser roots whose command line names this profile directory.

        Content processes do not repeat the profile path, so they are picked up
        later as children of these roots.
        """
        marker = os.path.normcase(os.path.abspath(str(profile)))
        if not marker:
            return []
        found: dict[int, psutil.Process] = {}
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                name = str(proc.info.get("name") or "").lower()
                if names and not any(expected in name for expected in names):
                    continue
                cmdline = os.path.normcase(" ".join(proc.info.get("cmdline") or []))
                if marker not in cmdline:
                    continue
                found[proc.pid] = proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return list(found.values())

    def kill(self, job_id: str, *, timeout: float = 5.0) -> int:
        """Terminate the whole tree under a job. Returns the process count hit."""
        return terminate_tree(self.processes(job_id), timeout=timeout)

    def kill_all(self, *, timeout: float = 5.0) -> int:
        """Kill every registered job and drop the registry. Shutdown path."""
        with self._lock:
            job_ids = list(self._jobs)
        killed = 0
        for job_id in job_ids:
            killed += self.kill(job_id, timeout=timeout)
            self.unregister(job_id)
        return killed

    # ---- profiles ------------------------------------------------------

    def reap_stale(self, *, max_age_sec: float | None = None, kill: bool = True) -> list[Path]:
        """Remove `<temp>/captcha-browser-*` profiles no live job owns."""
        root = self._temp_root
        if not root.is_dir():
            return []
        with self._lock:
            registered = {str(job.profile) for job in self._jobs.values()}
        removed: list[Path] = []
        for child in sorted(root.glob(self._prefix + "*")):
            try:
                if not child.is_dir() or str(child) in registered:
                    continue
                if max_age_sec is not None:
                    age = time.time() - child.stat().st_mtime
                    if age < max_age_sec:
                        continue
            except OSError:
                continue
            if kill:
                terminate_tree(self.find_processes(child, names=self._process_names), timeout=3.0)
            if remove_profile(child):
                removed.append(child)
        return removed


def terminate_tree(roots: list[psutil.Process], *, timeout: float = 5.0) -> int:
    """Terminate roots and their children, then kill whatever survived."""
    tree: dict[int, psutil.Process] = {}
    for root in roots:
        try:
            for child in root.children(recursive=True):
                tree[child.pid] = child
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        tree[root.pid] = root
    if not tree:
        return 0

    signalled: list[psutil.Process] = []
    for proc in tree.values():
        try:
            proc.terminate()
            signalled.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, survivors = psutil.wait_procs(signalled, timeout=timeout)
    for proc in survivors:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if survivors:
        psutil.wait_procs(survivors, timeout=1.0)
    return len(signalled)


def remove_profile(profile: str | Path, *, attempts: int = REMOVE_ATTEMPTS) -> bool:
    """Remove a temp profile, retrying: Windows keeps profile files locked briefly."""
    path = Path(profile)
    for attempt in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        if attempt < attempts - 1:
            time.sleep(REMOVE_DELAY_SEC)
    return False


__all__ = [
    "BROWSER_PROCESS_NAMES",
    "PROFILE_GLOB",
    "PROFILE_PREFIX",
    "Supervisor",
    "remove_profile",
    "terminate_tree",
]
