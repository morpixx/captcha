"""ALLOW_EXPERIMENTAL_TASKS gating (contract §7).

The beta default is True, which is a documented divergence from the frozen
contract. The only behaviour that matters here: when the flag is False, an
experimental-only type is not sellable, and a live heartbeat cannot smuggle it
back in.
"""

from __future__ import annotations

from typing import Any

from captcha.config import Settings
from captcha.domain.enums import CaptchaType
from captcha.domain.task import SolverResult, Task
from captcha.services.gateway.app import sellable_types
from captcha.solvers.base import Solver, SolverContext, SolverInfo
from captcha.solvers.registry import SolverRegistry

STABLE = CaptchaType.FRIENDLY
EXPERIMENTAL = CaptchaType.RECAPTCHA_V3


class StableSolver(Solver):
    info = SolverInfo(name="gate-stable", types=(STABLE,), http=True, experimental=False)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        return SolverResult(token="stable")


class ExperimentalSolver(Solver):
    info = SolverInfo(name="gate-experimental", types=(EXPERIMENTAL,), http=True, experimental=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        return SolverResult(token="experimental")


class SecondStableSolver(Solver):
    """Same experimental type, but non-experimental: the type stays sellable."""

    info = SolverInfo(name="gate-stable-v3", types=(EXPERIMENTAL,), http=True, experimental=False)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        return SolverResult(token="stable-v3")


def _registry(*classes: type[Solver]) -> SolverRegistry:
    registry = SolverRegistry()
    for cls in classes:
        registry.register(cls)
    return registry


def test_setting_defaults_to_true_for_the_beta() -> None:
    assert Settings.model_fields["allow_experimental_tasks"].default is True


def test_setting_reads_the_env_switch(monkeypatch: Any) -> None:
    monkeypatch.setenv("ALLOW_EXPERIMENTAL_TASKS", "0")

    assert Settings(_env_file=None).allow_experimental_tasks is False


def test_true_keeps_experimental_types() -> None:
    registry = _registry(StableSolver, ExperimentalSolver)

    sold = sellable_types(registry, frozenset(), allow_experimental=True)

    assert sold == {STABLE, EXPERIMENTAL}


def test_false_hides_experimental_only_types() -> None:
    registry = _registry(StableSolver, ExperimentalSolver)

    sold = sellable_types(registry, frozenset(), allow_experimental=False)

    assert sold == {STABLE}


def test_live_heartbeat_does_not_bypass_the_gate() -> None:
    registry = _registry(StableSolver, ExperimentalSolver)

    sold = sellable_types(registry, frozenset({EXPERIMENTAL}), allow_experimental=False)

    assert sold == {STABLE}


def test_type_with_a_stable_solver_stays_sellable() -> None:
    registry = _registry(ExperimentalSolver, SecondStableSolver)

    assert sellable_types(registry, frozenset(), allow_experimental=False) == {EXPERIMENTAL}
