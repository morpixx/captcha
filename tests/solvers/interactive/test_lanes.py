"""Lane routing for the interactive vendors.

The gateway sends a task to the lane named by `domain/catalog.py`, and only the
browser worker calls `solve_browser`. A browser-only vendor whose catalog row
still said `cv` would be replayed forever by a worker that cannot escalate, so
these rows are part of the solver contract and are pinned here.
"""

from __future__ import annotations

from captcha.domain import CaptchaType
from captcha.domain.catalog import lane_for
from captcha.domain.enums import SolveMode
from captcha.solvers.interactive.captchafox.solver import CaptchaFoxSolver
from captcha.solvers.interactive.mtcaptcha.solver import MTCaptchaSolver
from captcha.solvers.interactive.prosopo.solver import ProsopoSolver
from captcha.solvers.interactive.vk.solver import VKSolver
from captcha.solvers.interactive.yandex.solver import YandexSolver
from captcha.solvers.registry import global_registry


def test_widget_minted_tokens_route_to_browser() -> None:
    assert lane_for(CaptchaType.PROSOPO) == "browser"
    assert lane_for(CaptchaType.CAPTCHAFOX) == "browser"
    assert lane_for(CaptchaType.YANDEX) == "browser"


def test_http_solvable_vendors_keep_their_lane() -> None:
    assert lane_for(CaptchaType.MTCAPTCHA) == "cv"
    assert lane_for(CaptchaType.VK) == "http"


def test_registry_serves_each_lane() -> None:
    registry = global_registry()
    assert registry.pick(CaptchaType.MTCAPTCHA, mode=SolveMode.HTTP, allow_experimental=True) is MTCaptchaSolver
    assert registry.pick(CaptchaType.MTCAPTCHA, mode=SolveMode.BROWSER, allow_experimental=True) is None
    assert registry.pick(CaptchaType.VK, mode=SolveMode.HTTP, allow_experimental=True) is VKSolver
    for captcha_type, solver in (
        (CaptchaType.VK, VKSolver),
        (CaptchaType.PROSOPO, ProsopoSolver),
        (CaptchaType.CAPTCHAFOX, CaptchaFoxSolver),
        (CaptchaType.YANDEX, YandexSolver),
    ):
        assert registry.pick(captcha_type, mode=SolveMode.BROWSER, allow_experimental=True) is solver
