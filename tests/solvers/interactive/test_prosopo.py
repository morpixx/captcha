"""Prosopo: the browser lane, the escalation, and the no-fake-token policy.

The 2026 Procaptcha response is a signed commitment produced by the widget's
own SR25519 user, so the tests pin that `solve_http` escalates (or finishes
in-process when a provider is present) and that the browser lane only returns
a value the page actually exposed.
"""

from __future__ import annotations

from typing import Any

import msgspec
import pytest
from tests.solvers.interactive._fakes import FakePage, FakeProvider, make_task

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ChallengeExpiredError, NeedsBrowserError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.solvers import SolverContext
from captcha.solvers.interactive.prosopo.solver import ProsopoSolver

SITE_URL = "https://shop.test/checkout"
SITEKEY = "5EZU3LG31uzq1Mwi8inwqxmfvFDpj7VzwDnZwj4Q3syyxBwV"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"
TOKEN = "0x00016c68747470733a2f2f70726f6e6f646533"


def _task(payload: dict[str, Any] | None = None, **kwargs: Any) -> Task:
    return make_task(CaptchaType.PROSOPO, payload, site_url=SITE_URL, sitekey=SITEKEY, **kwargs)


def test_metadata() -> None:
    assert ProsopoSolver.info.types == (CaptchaType.PROSOPO,)
    assert ProsopoSolver.info.http is True
    assert ProsopoSolver.info.browser is True


async def test_missing_sitekey_is_unsolvable() -> None:
    task = make_task(CaptchaType.PROSOPO, {}, site_url=SITE_URL)
    with pytest.raises(UnsolvableError, match="sitekey"):
        await ProsopoSolver().solve_http(task, SolverContext())


async def test_missing_url_is_unsolvable() -> None:
    task = make_task(CaptchaType.PROSOPO, {}, sitekey=SITEKEY)
    with pytest.raises(UnsolvableError, match="websiteURL"):
        await ProsopoSolver().solve_http(task, SolverContext())


async def test_http_lane_escalates_with_the_reason() -> None:
    with pytest.raises(NeedsBrowserError, match="SR25519") as exc:
        await ProsopoSolver().solve_http(_task(), SolverContext())
    assert exc.value.context["sitekey"] == SITEKEY
    assert exc.value.context["url"] == SITE_URL


async def _solve_browser(page: FakePage, task: Task | None = None):
    return await ProsopoSolver().solve_browser(
        task or _task(),
        SolverContext(browser=FakeProvider(page), extra={"timeout_s": 0.1}),
    )


async def test_browser_reads_the_hidden_field() -> None:
    page = FakePage({"procaptcha-response": TOKEN, "location.hostname": "shop.test"}, ua=UA)
    result = await _solve_browser(page)

    assert result.token == TOKEN
    assert result.extra == {"userAgent": UA}
    assert result.solve_mode.value == "browser"


async def test_browser_reads_the_global_api() -> None:
    page = FakePage({"getResponse": TOKEN, "location.hostname": "shop.test"}, ua=UA)
    result = await _solve_browser(page)
    assert result.token == TOKEN


async def test_solution_projects_to_token_and_ua() -> None:
    page = FakePage({"procaptcha-response": TOKEN, "location.hostname": "shop.test"}, ua=UA)
    result = await _solve_browser(page)
    assert build_solution(CaptchaType.PROSOPO, msgspec.structs.asdict(result)) == {
        "token": TOKEN,
        "userAgent": UA,
    }


async def test_solve_http_uses_a_local_provider() -> None:
    page = FakePage({"procaptcha-response": TOKEN, "location.hostname": "shop.test"}, ua=UA)
    result = await ProsopoSolver().solve_http(
        _task(),
        SolverContext(browser=FakeProvider(page), extra={"timeout_s": 0.1}),
    )
    assert result.token == TOKEN
    assert result.solve_mode.value == "browser"


async def test_browser_missing_token_is_retryable() -> None:
    page = FakePage({}, ua=UA)
    with pytest.raises(ChallengeExpiredError, match="no procaptcha-response"):
        await _solve_browser(page)
    assert page.clicks


async def test_browser_spec_carries_proxy_and_cookies() -> None:
    page = FakePage({"procaptcha-response": TOKEN, "location.hostname": "shop.test"}, ua=UA)
    provider = FakeProvider(page)
    task = _task({"cookies": "a=b; c=d"}, proxy="http://user:pass@proxy.test:8080")

    await ProsopoSolver().solve_browser(
        task,
        SolverContext(browser=provider, extra={"timeout_s": 0.1}),
    )

    spec = provider.specs[0]
    assert spec.proxy == "http://user:pass@proxy.test:8080"
    assert spec.cookies == [{"name": "a", "value": "b"}, {"name": "c", "value": "d"}]
    assert spec.label.startswith("prosopo:prosopo:")


async def test_browser_wrong_origin_is_unsolvable() -> None:
    page = FakePage({"procaptcha-response": TOKEN, "location.hostname": "evil.test"}, ua=UA)
    with pytest.raises(UnsolvableError, match="origin"):
        await _solve_browser(page)
