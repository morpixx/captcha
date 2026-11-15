"""Yandex SmartCaptcha: escalation and the browser lane's token sources.

The widget token lives in `input[name=smart-token]` (or behind
`window.smartCaptcha.getResponse`); the tests pin both readers, the
execute/checkbox nudges, and the rule that an empty page is a retry, not a
fabricated token.
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
from captcha.solvers.interactive.yandex.solver import YandexSolver

SITE_URL = "https://shop.test/support"
SITEKEY = "ysc1_abcDEF123"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"
TOKEN = "dR001:0:eyJhbGciOiJFUzI1NiJ9"


def _task(payload: dict[str, Any] | None = None, **kwargs: Any) -> Task:
    return make_task(CaptchaType.YANDEX, payload, site_url=SITE_URL, sitekey=SITEKEY, **kwargs)


def test_metadata() -> None:
    assert YandexSolver.info.types == (CaptchaType.YANDEX,)
    assert YandexSolver.info.http is True
    assert YandexSolver.info.browser is True


async def test_missing_sitekey_is_unsolvable() -> None:
    task = make_task(CaptchaType.YANDEX, {}, site_url=SITE_URL)
    with pytest.raises(UnsolvableError, match="sitekey"):
        await YandexSolver().solve_http(task, SolverContext())


async def test_missing_url_is_unsolvable() -> None:
    task = make_task(CaptchaType.YANDEX, {}, sitekey=SITEKEY)
    with pytest.raises(UnsolvableError, match="websiteURL"):
        await YandexSolver().solve_http(task, SolverContext())


async def test_http_lane_escalates() -> None:
    with pytest.raises(NeedsBrowserError, match="smart-token") as exc:
        await YandexSolver().solve_http(_task(), SolverContext())
    assert exc.value.context == {"sitekey": SITEKEY, "url": SITE_URL}


async def _solve_browser(page: FakePage, task: Task | None = None):
    return await YandexSolver().solve_browser(
        task or _task(),
        SolverContext(browser=FakeProvider(page), extra={"timeout_s": 0.1}),
    )


async def test_browser_reads_the_hidden_input() -> None:
    page = FakePage({"smart-token": TOKEN, "location.hostname": "shop.test"}, ua=UA)
    result = await _solve_browser(page)

    assert result.token == TOKEN
    assert result.extra == {"userAgent": UA}
    assert result.solve_mode.value == "browser"


async def test_browser_reads_the_widget_api() -> None:
    page = FakePage({"getResponse": TOKEN, "location.hostname": "shop.test"}, ua=UA)
    result = await _solve_browser(page)
    assert result.token == TOKEN


async def test_browser_executes_and_clicks_before_giving_up() -> None:
    page = FakePage({}, ua=UA)
    with pytest.raises(ChallengeExpiredError, match="no smart-token"):
        await _solve_browser(page)
    assert any("smartCaptcha.execute" in script for script, _ in page.scripts)
    assert page.clicks


async def test_browser_wrong_origin_is_unsolvable() -> None:
    page = FakePage({"smart-token": TOKEN, "location.hostname": "evil.test"}, ua=UA)
    with pytest.raises(UnsolvableError, match="origin"):
        await _solve_browser(page)


async def test_solution_projects_to_token_and_ua() -> None:
    page = FakePage({"smart-token": TOKEN, "location.hostname": "shop.test"}, ua=UA)
    result = await _solve_browser(page)
    assert build_solution(CaptchaType.YANDEX, msgspec.structs.asdict(result)) == {
        "token": TOKEN,
        "userAgent": UA,
    }
