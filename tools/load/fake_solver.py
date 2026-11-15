"""A solver that answers instantly and touches nothing.

No network, no model, no browser, no cipher: it exists so a load run exercises
the gateway, the stream, the worker loop and the state hash without spending a
real captcha or needing a proxy. Registered explicitly by `devstack` (or by a
process that imports it) — never on import, and never by `solvers/loader.py`.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.solvers import Solver, SolverContext, SolverInfo, register_solver

DEFAULT_LATENCY_MS = 20.0
DEFAULT_JITTER_MS = 10.0

_latency_ms = DEFAULT_LATENCY_MS
_jitter_ms = DEFAULT_JITTER_MS


def configure(*, latency_ms: float = DEFAULT_LATENCY_MS, jitter_ms: float = DEFAULT_JITTER_MS) -> None:
    """Set the fake solve time. Uniform in [latency, latency + jitter]."""
    global _latency_ms, _jitter_ms
    _latency_ms = max(0.0, float(latency_ms))
    _jitter_ms = max(0.0, float(jitter_ms))


class LoadTestSolver(Solver):
    """Returns `text='load-test'` after a configurable delay. Pure CPU sleep."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="load-test",
        types=(CaptchaType.TEXT,),
        http=True,
        version="0.1.0",
    )

    def __init__(self) -> None:
        self._calls = 0

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        delay = (_latency_ms + random.uniform(0.0, _jitter_ms)) / 1000.0
        if delay > 0:
            await asyncio.sleep(delay)
        self._calls += 1
        return SolverResult(
            text="load-test",
            extra={"fake": True, "calls": self._calls},
            latency_ms=delay * 1000.0,
        )


def register(captcha_type: CaptchaType = CaptchaType.TEXT) -> type[Solver]:
    """Register the fake solver for `captcha_type`; returns the class.

    Done through a subclass so the claim can follow `--type` without pretending
    one solver object answers every type in `SolverInfo.types`.
    """
    attrs: dict[str, Any] = {
        "info": SolverInfo(
            name=f"load-test-{captcha_type.value}",
            types=(captcha_type,),
            http=True,
            version="0.1.0",
        ),
        "__doc__": LoadTestSolver.__doc__,
    }
    cls = type("LoadTestSolver", (LoadTestSolver,), attrs)
    register_solver(cls)
    return cls
