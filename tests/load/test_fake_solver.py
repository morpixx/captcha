"""The fake solver: registered on demand, answers without touching anything."""

from __future__ import annotations

import asyncio

from tools.load import fake_solver

from captcha.domain import CaptchaType
from captcha.domain.enums import SolveMode
from captcha.domain.task import Task
from captcha.solvers import SolverInfo, global_registry


def _task(captcha_type: CaptchaType) -> Task:
    import uuid

    return Task(id=uuid.uuid4(), type=captcha_type, payload={})


async def test_registered_solver_is_picked_for_its_type_and_returns_text() -> None:
    fake_solver.configure(latency_ms=0.0, jitter_ms=0.0)
    cls = fake_solver.register(CaptchaType.TEXT)

    assert issubclass(cls, fake_solver.LoadTestSolver)
    assert isinstance(cls.info, SolverInfo)
    picked = global_registry().pick(CaptchaType.TEXT, mode=SolveMode.HTTP)
    assert picked is not None
    assert CaptchaType.TEXT in picked.info.types

    result = await asyncio.wait_for(cls().solve_http(_task(CaptchaType.TEXT), None), timeout=1.0)  # type: ignore[arg-type]
    assert result.text == "load-test"
    assert result.extra is not None
    assert result.extra["fake"] is True


async def test_configured_latency_is_honoured() -> None:
    fake_solver.configure(latency_ms=30.0, jitter_ms=0.0)
    try:
        cls = fake_solver.register(CaptchaType.TEXT)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await cls().solve_http(_task(CaptchaType.TEXT), None)  # type: ignore[arg-type]
        assert loop.time() - started >= 0.025
    finally:
        fake_solver.configure(latency_ms=0.0, jitter_ms=0.0)


def test_register_claims_only_the_requested_type() -> None:
    cls = fake_solver.register(CaptchaType.TEXT)
    assert cls.info.types == (CaptchaType.TEXT,)
