"""Lane dispatch: can each lane worker pick and run a solver for its types?

tests/domain/test_lanes.py pins the catalog. This file checks the other half:
the enrolled solvers must match the catalog transport flags, every lane must be
able to pick a solver whose declared transport that worker implements, and the
gateway must not sell a browser-only type while no browser worker beats.

No Redis, no browser, no network: the real registry plus fake solver classes.
"""

from __future__ import annotations

import pytest

from captcha.domain.catalog import CATALOG, lane_for, requires_browser, types_by_lane
from captcha.domain.enums import CaptchaType, SolveMode
from captcha.domain.task import SolverResult, Task, new_task_id
from captcha.solvers.base import Solver, SolverContext, SolverInfo
from captcha.solvers.registry import SolverRegistry, discover_solvers, global_registry

REGISTRY = discover_solvers(registry=global_registry())
ALL_TYPES = list(CaptchaType)


def _pick(ct: CaptchaType, mode: SolveMode) -> type[Solver] | None:
    return REGISTRY.pick(ct, mode=mode, allow_experimental=True)


# --------------------------------------------------------------- registry coverage


def test_registry_covers_all_63_types() -> None:
    assert set(ALL_TYPES) <= REGISTRY.all_types()


@pytest.mark.parametrize("ct", ALL_TYPES)
def test_catalog_browser_flag_matches_the_registry(ct: CaptchaType) -> None:
    infos = [cls.info for cls in REGISTRY.for_type(ct)]
    assert infos, f"{ct.value} has no solver"
    assert CATALOG[ct].browser is any(info.browser for info in infos)
    if CATALOG[ct].lane == "browser":
        assert CATALOG[ct].browser is True


@pytest.mark.parametrize("lane", ["http", "cv", "protocol"])
def test_http_based_lanes_have_an_http_solver_per_type(lane: str) -> None:
    """worker_http, worker_cv and worker_protocol all call solve_http."""
    missing = [ct.value for ct in types_by_lane(lane) if _pick(ct, SolveMode.HTTP) is None]
    assert missing == []


def test_browser_lane_has_a_browser_solver_per_type() -> None:
    missing = [ct.value for ct in types_by_lane("browser") if _pick(ct, SolveMode.BROWSER) is None]
    assert missing == []


def test_browser_lane_types_keep_an_http_probe() -> None:
    missing = [ct.value for ct in types_by_lane("browser") if _pick(ct, SolveMode.HTTP) is None]
    assert missing == []


def test_no_type_outside_the_browser_lane_relies_on_a_browser_only_solver() -> None:
    stranded = [
        ct.value
        for ct in ALL_TYPES
        if CATALOG[ct].lane != "browser" and all(not cls.info.http for cls in REGISTRY.for_type(ct))
    ]
    assert stranded == []


def test_escalation_types_have_a_browser_solver() -> None:
    missing = [ct.value for ct in ALL_TYPES if requires_browser(ct) and _pick(ct, SolveMode.BROWSER) is None]
    assert missing == []


# ------------------------------------------------------------------ selection order


class _Verified(Solver):
    info = SolverInfo(name="fake-verified", types=(CaptchaType.TEXT,), http=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        return SolverResult(text="ok")


class _Experimental(Solver):
    info = SolverInfo(name="fake-experimental", types=(CaptchaType.TEXT,), http=True, experimental=True)

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        return SolverResult(text="ok")


def test_resolution_order_puts_verified_solvers_first() -> None:
    registry = SolverRegistry()
    registry.register(_Experimental)
    registry.register(_Verified)

    assert [cls.info.name for cls in registry.resolution_order()] == ["fake-verified", "fake-experimental"]
    # pick() agrees; resolution_order exists for the workers that keep the first hit.
    assert registry.pick(CaptchaType.TEXT, mode=SolveMode.HTTP, allow_experimental=True) is _Verified


def _first_per_type(registry: SolverRegistry, lane: str) -> dict[CaptchaType, type[Solver]]:
    chosen: dict[CaptchaType, type[Solver]] = {}
    for cls in registry.resolution_order():
        for ct in cls.info.types:
            if lane_for(ct) == lane:
                chosen.setdefault(ct, cls)
    return chosen


def test_cv_lane_prefers_a_verified_solver_when_one_exists() -> None:
    chosen = _first_per_type(REGISTRY, "cv")
    for ct in types_by_lane("cv"):
        candidates = REGISTRY.for_type(ct)
        if any(not cls.info.experimental for cls in candidates):
            assert chosen[ct].info.experimental is False, ct.value
    assert chosen[CaptchaType.CLASSIFICATION_GRID].info.experimental is False


def test_cv_and_protocol_reject_a_browser_only_solver() -> None:
    from captcha.config import Settings
    from captcha.services.worker_cv.worker import Worker as CvWorker
    from captcha.services.worker_protocol.worker import Worker as ProtocolWorker

    class _BrowserOnly(Solver):
        info = SolverInfo(name="fake-browser-only", types=(CaptchaType.TEXT,), http=False, browser=True)

        async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
            raise AssertionError("browser-only solver ran solve_http")

    settings = Settings(redis_stream_prefix="test")
    assert CvWorker(settings, registry=REGISTRY)._accepted_types(_BrowserOnly) == []
    assert ProtocolWorker(settings, registry=REGISTRY)._accepted_types(_BrowserOnly) == []


def test_cv_worker_accepts_every_cv_row() -> None:
    from captcha.config import Settings
    from captcha.services.worker_cv.worker import Worker

    worker = Worker(Settings(redis_stream_prefix="test"), registry=REGISTRY)
    for ct in types_by_lane("cv"):
        cls = _pick(ct, SolveMode.HTTP)
        assert cls is not None, ct.value
        assert ct in worker._accepted_types(cls), ct.value


# --------------------------------------------------------------- cv task transport


def test_cv_task_context_binds_http_per_task() -> None:
    from captcha.services.worker_cv.context import HttpFactory, task_context

    template = SolverContext()
    task = Task(
        id=new_task_id(),
        type=CaptchaType.GEETEST_V3,
        payload={},
        proxy="http://127.0.0.1:8080",
        user_agent="UA/1",
        language="en",
    )
    ctx, client = task_context(template, HttpFactory("chrome124"), task)

    assert ctx.http is client
    assert template.http is None, "the process-wide template must never hold a client"
    assert client.proxy == "http://127.0.0.1:8080"
    assert client.default_headers == {"User-Agent": "UA/1", "Accept-Language": "en"}


def test_cv_task_context_without_proxy_or_headers() -> None:
    from captcha.services.worker_cv.context import HttpFactory, task_context

    task = Task(id=new_task_id(), type=CaptchaType.MTCAPTCHA, payload={})
    ctx, client = task_context(SolverContext(), HttpFactory(""), task)

    assert ctx.http is client
    assert client.proxy is None
    assert client.default_headers == {}


# --------------------------------------------------------------------- capabilities


def test_escalation_types_are_gated_on_a_live_browser_worker() -> None:
    from captcha.services.gateway.app import _available_types

    registry = frozenset({CaptchaType.TURNSTILE, CaptchaType.FRIENDLY, CaptchaType.HCAPTCHA})

    assert _available_types(registry, frozenset()) == frozenset({CaptchaType.FRIENDLY})
    assert _available_types(registry, frozenset({CaptchaType.TURNSTILE, CaptchaType.HCAPTCHA})) == registry


def test_http_only_deployment_still_sells_its_own_types() -> None:
    from captcha.services.gateway.app import _available_types

    registry = frozenset({CaptchaType.RECAPTCHA_V3, CaptchaType.AKAMAI_SENSOR, CaptchaType.PROSOPO})
    assert _available_types(registry, frozenset()) == frozenset(
        {CaptchaType.RECAPTCHA_V3, CaptchaType.AKAMAI_SENSOR}
    )
