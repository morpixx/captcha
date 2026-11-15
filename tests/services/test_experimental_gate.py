"""ALLOW_EXPERIMENTAL_TASKS gating (contract §7).

Contract §7: the flag is off by default, so a type whose only solver is
experimental is not sellable; `1` is an explicit beta override. The behaviour
that matters here: at False an experimental-only type is withheld (the route
then answers ERROR_TASK_NOT_SUPPORTED before billing, see
tests/gateway/test_api_flow.py), and a live heartbeat cannot smuggle it back in.
"""

from __future__ import annotations

from typing import Any

import pytest

from captcha.config import Settings
from captcha.domain.enums import CaptchaType, SolveMode
from captcha.domain.task import SolverResult, Task
from captcha.services.gateway.app import sellable_types
from captcha.solvers import discover_solvers
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


def test_setting_defaults_to_false(monkeypatch: Any) -> None:
    monkeypatch.delenv("ALLOW_EXPERIMENTAL_TASKS", raising=False)

    assert Settings.model_fields["allow_experimental_tasks"].default is False
    assert Settings(_env_file=None).allow_experimental_tasks is False


@pytest.mark.parametrize(("raw", "expected"), [("0", False), ("1", True)])
def test_setting_reads_the_env_switch(monkeypatch: Any, raw: str, expected: bool) -> None:
    monkeypatch.setenv("ALLOW_EXPERIMENTAL_TASKS", raw)

    assert Settings(_env_file=None).allow_experimental_tasks is expected


def test_default_deployment_sells_only_the_stable_types() -> None:
    """The real registry at the default: 24 vendor types, all non-experimental.

    The count is the contract snapshot (38 experimental-only types withheld).
    It moves only when a type earns a non-experimental solver after L3.
    """
    registry = discover_solvers()

    sold = sellable_types(registry, frozenset(), allow_experimental=False)

    assert len(sold) == 24
    assert CaptchaType.RECAPTCHA_V3 not in sold
    assert all(registry.pick(ct, mode=SolveMode.AUTO, allow_experimental=False) is not None for ct in sold)


def test_beta_override_sells_the_experimental_types_again() -> None:
    registry = discover_solvers()

    sold = sellable_types(registry, frozenset(), allow_experimental=True)

    assert CaptchaType.RECAPTCHA_V3 in sold


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
