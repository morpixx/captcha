"""Cloudflare managed challenge (cf_clearance): fake page, fake cookie jar.

No browser, no network. The page reports title/URL/challenge markers; the
session's context reports the jar. The solver's policy -- cookie only counts
after the interstitial is gone, UA is mandatory, nothing is invented -- is what
these tests pin down.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from captcha.domain import CaptchaType, SolveMode, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, NeedsBrowserError, UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext
from captcha.solvers.antibot.cloudflare.solver import CloudflareChallengeSolver

SITE_URL = "https://site.test/login"
HOST = "site.test"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"
CLEARANCE = "clear-1"


# ---- fakes ------------------------------------------------------------------


class FakeKeyboard:
    def __init__(self) -> None:
        self.presses: list[str] = []

    async def press(self, key: str) -> None:
        self.presses.append(key)


class FakePage:
    def __init__(self, results: list, *, keyboard: bool = True) -> None:
        self.results = list(results)
        self.calls: list[str] = []
        self.keyboard = FakeKeyboard() if keyboard else None

    async def evaluate(self, script: str, arg=None):
        self.calls.append(script)
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


class FakeContext:
    def __init__(self, cookies: list[dict] | None = None, *, fail: bool = False) -> None:
        self._cookies = cookies or []
        self._fail = fail

    async def cookies(self) -> list[dict]:
        if self._fail:
            raise RuntimeError("context is gone")
        return self._cookies


class FakeSession:
    def __init__(self, page: FakePage, cookies: list[dict] | None = None, *, fail_jar: bool = False) -> None:
        self._page = page
        self.context = FakeContext(cookies, fail=fail_jar)
        self.gotos: list[tuple[str, str, int]] = []

    async def page(self) -> FakePage:
        return self._page

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 60_000):
        self.gotos.append((url, wait_until, timeout_ms))
        return self._page


class FakeProvider:
    def __init__(self, page: FakePage, cookies: list[dict] | None = None, *, fail_jar: bool = False) -> None:
        self.page = page
        self.cookies = cookies
        self.fail_jar = fail_jar
        self.specs: list = []
        self.sessions: list[FakeSession] = []
        self.closed = 0

    @asynccontextmanager
    async def session(self, spec):
        self.specs.append(spec)
        session = FakeSession(self.page, self.cookies, fail_jar=self.fail_jar)
        self.sessions.append(session)
        try:
            yield session
        finally:
            self.closed += 1


def _state(**overrides) -> dict:
    out = {"url": SITE_URL, "host": HOST, "ua": UA, "title": "Login", "challenge": False}
    out.update(overrides)
    return out


def _jar() -> list[dict]:
    return [
        {"name": "cf_clearance", "value": CLEARANCE, "domain": ".site.test"},
        {"name": "__cf_bm", "value": "bm-1", "domain": "site.test"},
        {"name": "cf_clearance", "value": "widget-cookie", "domain": ".challenges.cloudflare.com"},
    ]


def _task(payload: dict | None = None, *, url: str | None = SITE_URL, user_agent: str | None = None) -> Task:
    return Task(
        id=new_task_id(),
        type=CaptchaType.CF_CHALLENGE,
        payload=payload or {},
        site_url=url,
        user_agent=user_agent,
        preferred_mode=SolveMode.BROWSER,
    )


async def _solve(page: FakePage, ctx: SolverContext, task: Task | None = None):
    return await CloudflareChallengeSolver().solve_browser(task or _task(), ctx)


# ---- happy path -------------------------------------------------------------


async def test_returns_clearance_with_the_jar_and_page_ua():
    page = FakePage([_state()])
    provider = FakeProvider(page, _jar())

    result = await _solve(page, SolverContext(browser=provider))

    assert result.token == CLEARANCE
    assert result.extra == {
        "cookies": {"cf_clearance": CLEARANCE, "__cf_bm": "bm-1"},
        "userAgent": UA,
    }
    assert result.solve_mode is SolveMode.BROWSER
    assert result.latency_ms >= 0.0
    assert provider.closed == 1
    assert provider.sessions[0].gotos == [(SITE_URL, "domcontentloaded", 90_000)]
    assert provider.specs[0].label.startswith("cloudflare:cf_challenge:")


async def test_payload_pageurl_is_accepted():
    page = FakePage([_state()])
    task = _task({"pageurl": SITE_URL}, url=None)

    result = await _solve(page, SolverContext(browser=FakeProvider(page, _jar())), task)

    assert result.token == CLEARANCE


async def test_widget_host_cookie_is_not_a_clearance():
    jar = [{"name": "cf_clearance", "value": "widget-cookie", "domain": ".challenges.cloudflare.com"}]
    page = FakePage([_state()])

    with pytest.raises(ChallengeExpiredError) as exc:
        await _solve(page, SolverContext(browser=FakeProvider(page, jar), extra={"timeout_s": 0.05}))

    assert exc.value.context["reason"] == "no_clearance"


# ---- failure mapping --------------------------------------------------------


async def test_no_cookie_is_retryable():
    page = FakePage([_state()])

    with pytest.raises(ChallengeExpiredError) as exc:
        await _solve(page, SolverContext(browser=FakeProvider(page), extra={"timeout_s": 0.05}))

    assert exc.value.context["reason"] == "no_clearance"
    assert exc.value.context["challenge"] is False


async def test_clearance_while_still_on_the_challenge_is_rejected():
    challenge_url = "https://site.test/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1"
    page = FakePage([_state(url=challenge_url)])

    with pytest.raises(ChallengeExpiredError) as exc:
        await _solve(page, SolverContext(browser=FakeProvider(page, _jar()), extra={"timeout_s": 0.05}))

    assert exc.value.context["reason"] == "clearance_before_redirect"


async def test_clearance_with_challenge_markers_still_up_is_rejected():
    page = FakePage([_state(challenge=True, title="Just a moment...")])

    with pytest.raises(ChallengeExpiredError) as exc:
        await _solve(page, SolverContext(browser=FakeProvider(page, _jar()), extra={"timeout_s": 0.05}))

    assert exc.value.context["reason"] == "clearance_before_redirect"


async def test_missing_user_agent_is_terminal():
    page = FakePage([_state(ua="")])

    with pytest.raises(ConfigError):
        await _solve(page, SolverContext(browser=FakeProvider(page, _jar())))


async def test_client_user_agent_does_not_replace_the_page_ua():
    page = FakePage([_state(ua="")])

    with pytest.raises(ConfigError):
        await _solve(page, SolverContext(browser=FakeProvider(page, _jar())), _task(user_agent="UA-client"))


async def test_broken_cookie_jar_cannot_produce_a_solution():
    page = FakePage([_state()])

    with pytest.raises(ChallengeExpiredError):
        await _solve(
            page,
            SolverContext(browser=FakeProvider(page, _jar(), fail_jar=True), extra={"timeout_s": 0.05}),
        )


async def test_page_script_returning_nothing_is_retryable():
    page = FakePage([None])

    with pytest.raises(ChallengeExpiredError):
        await _solve(page, SolverContext(browser=FakeProvider(page), extra={"timeout_s": 0.05}))


async def test_nudges_with_tab_and_space_before_the_redirect():
    page = FakePage([_state(challenge=True, title="Just a moment...")] * 3 + [_state()])
    ctx = SolverContext(browser=FakeProvider(page, _jar()), extra={"timeout_s": 0.2})

    result = await _solve(page, ctx)

    assert result.token == CLEARANCE
    assert page.keyboard.presses == ["Tab", "Space"]


async def test_missing_provider_is_a_config_error():
    with pytest.raises(ConfigError):
        await _solve(FakePage([_state()]), SolverContext())


@pytest.mark.parametrize("url", [None, "ftp://site.test/login", "not a url"])
async def test_unusable_url_is_unsolvable(url: str | None):
    with pytest.raises(UnsolvableError):
        await _solve(FakePage([_state()]), SolverContext(browser=FakeProvider(FakePage([_state()]))), _task(url=url))


# ---- HTTP lane is an escalation point ---------------------------------------


async def test_http_lane_escalates_to_the_browser():
    with pytest.raises(NeedsBrowserError) as exc:
        await CloudflareChallengeSolver().solve_http(_task(), SolverContext(http=object()))

    assert exc.value.context["url"] == SITE_URL


async def test_http_lane_rejects_a_broken_url_before_escalating():
    with pytest.raises(UnsolvableError):
        await CloudflareChallengeSolver().solve_http(_task(url=None), SolverContext())


async def test_http_lane_finishes_in_process_when_a_provider_is_present():
    page = FakePage([_state()])
    ctx = SolverContext(browser=FakeProvider(page, _jar()))

    result = await CloudflareChallengeSolver().solve_http(_task(), ctx)

    assert result.token == CLEARANCE
