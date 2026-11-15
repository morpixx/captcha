from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import msgspec

from captcha.domain import CaptchaType, SolverResult, Task


class SolverInfo(msgspec.Struct, frozen=True, kw_only=True):
    """Static metadata — discovered by the registry at import time."""

    name: str
    types: tuple[CaptchaType, ...]
    http: bool = True
    needs_js_runtime: bool = False
    needs_gpu: bool = False
    experimental: bool = False
    version: str = "0.1.0"


@dataclass
class SolverContext:
    """Runtime injection: shared clients the solver gets from the worker.

    Solvers never new up their own HTTP client, proxy, model handle, or JS runtime —
    they take what the worker gives. Makes tests isolate the solver, not the world.
    """

    http: Any = None  # captcha.adapters.transport.Client
    js_runtime: Any = None  # captcha.adapters.jssandbox.Runtime
    proxy_pool: Any = None  # captcha.adapters.proxy.Pool
    fingerprint_store: Any = None  # captcha.adapters.profiles.Store
    models: Any = None  # captcha.adapters.weights.Registry
    sensors: Any = None  # captcha.engines.Registry
    config: Any = None  # captcha.domain.Settings
    logger: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


class Solver(ABC):
    """Base class for all captcha-type plugins.

    Lifecycle:
      1. registry discovers via entry_points at worker startup
      2. worker instantiates, calls `prepare(ctx)` once
      3. for each task matching `info.types`, worker calls `solve_http(task, ctx)`
      4. worker calls `shutdown()` on worker stop

    Contract: raise a CaptchaError subclass on known failure modes; let
    anything else surface as unknown error — the worker classifies and logs.
    """

    info: ClassVar[SolverInfo]

    def __init__(self) -> None:
        pass

    async def prepare(self, ctx: SolverContext) -> None:
        """Preload models, warm caches, pre-build sensor templates. Called once."""

    async def shutdown(self) -> None:
        """Release GPU, close sessions. Called once."""

    @abstractmethod
    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        """Solve one task. Every solver is HTTP-first; there is no browser path."""
