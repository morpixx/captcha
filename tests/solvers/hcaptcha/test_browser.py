"""Browser-lane hCaptcha solver, driven against fake page/session objects.

No browser, no network: the fake provider hands the solver a page whose
evaluate() returns a canned widget result, so every branch of the failure
mapping is exercised deterministically.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from captcha.domain import CaptchaType, SolveMode, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext
from captcha.solvers.hcaptcha import HCaptchaBrowserSolver, Site, all_sites, register_site
from captcha.solvers.hcaptcha.common import token_problem, validate_token
from captcha.solvers.hcaptcha.sites import DEFAULT_MINT_URL, resolve_site

RIOT_HOST = "authenticate.riotgames.com"
RIOT_URL = f"https://{RIOT_HOST}/oauth2?client_id=riot-client&redirect_uri=https%3A%2F%2Fexample"
RIOT_KEY = "a5f74b19-9e45-40e0-b45d-47ff91b7a6c2"
TOKEN = "P1_" + "A" * 120
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"


# ---- fakes ------------------------------------------------------------------


class FakeLocator:
    def __init__(self) -> None:
        self.clicks: list[dict] = []

    async def count(self) -> int:
        return 1

    async def click(self, **kwargs) -> None:
        self.clicks.append(kwargs)


class FakeFrame:
    def __init__(self) -> None:
        self.url = "https://newassets.hcaptcha.com/captcha/v1/deadbeef/checkbox"
        self.locator_obj = FakeLocator()

    def locator(self, selector: str) -> FakeLocator:
        assert selector == "#checkbox"
        return self.locator_obj


class FakePage:
    def __init__(self, *, url: str = RIOT_URL, result: dict | None = None, frames=()) -> None:
        self.url = url
        self.result = result if result is not None else {}
        self.frames = list(frames)
        self.gotos: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    async def goto(self, url: str, **_kwargs) -> None:
        self.gotos.append(url)
        self.url = url

    async def evaluate(self, script: str, arg=None) -> dict:
        self.calls.append((script, arg or {}))
        return dict(self.result)


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def page(self) -> FakePage:
        return self._page


class PortSession(FakeSession):
    """Session shape from domain/ports.py: navigation lives on the session."""

    def __init__(self, page: FakePage) -> None:
        super().__init__(page)
        self.gotos: list[tuple[str, str, int]] = []

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 60_000):
        self.gotos.append((url, wait_until, timeout_ms))
        return self._page


class FakeProvider:
    """Provider-shaped browser: the session(spec) contract from production-contract §4."""

    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.specs: list[object] = []
        self.sessions: list[PortSession] = []
        self.closed = 0

    @asynccontextmanager
    async def session(self, spec):
        self.specs.append(spec)
        session = PortSession(self.page)
        self.sessions.append(session)
        try:
            yield session
        finally:
            self.closed += 1


class LegacyPool:
    """Pre-provider pool from the baseline tree: acquire() with no spec."""

    def __init__(self, page: FakePage) -> None:
        self.page = page

    @asynccontextmanager
    async def acquire(self):
        yield FakeSession(self.page)


def _task(
    payload: dict | None = None,
    *,
    type_: CaptchaType = CaptchaType.HCAPTCHA,
    url: str | None = RIOT_URL,
    user_agent: str | None = None,
) -> Task:
    return Task(
        id=new_task_id(),
        type=type_,
        payload=payload or {},
        preferred_mode=SolveMode.BROWSER,
        site_url=url,
        user_agent=user_agent,
    )


def _widget(**overrides) -> dict:
    out = {"host": RIOT_HOST, "token": TOKEN, "ua": UA, "diag": {}}
    out.update(overrides)
    return out


async def _solve(page: FakePage, task: Task, ctx: SolverContext) -> object:
    return await HCaptchaBrowserSolver().solve_browser(task, ctx)


# ---- happy path -------------------------------------------------------------


async def test_solves_invisible_widget_and_returns_solution():
    page = FakePage(result=_widget(respKey="resp-key-1", diag={"api": True}))
    provider = FakeProvider(page)
    ctx = SolverContext(browser=provider)

    result = await _solve(page, _task(), ctx)

    assert result.token == TOKEN
    assert result.solve_mode is SolveMode.BROWSER
    assert result.extra == {"userAgent": UA, "respKey": "resp-key-1"}
    assert result.latency_ms >= 0.0
    assert provider.closed == 1
    # navigation goes through the provider's goto, not straight to the page
    assert provider.sessions[0].gotos == [(RIOT_URL, "domcontentloaded", 90_000)]
    assert page.gotos == []

    spec = provider.specs[0]
    assert spec.label.startswith("hcaptcha:riot-authenticate:")
    assert spec.headless is True
    assert spec.cookies == []
    assert spec.proxy is None

    script, arg = page.calls[0]
    assert "h-captcha-response" in script
    assert arg["sitekey"] == RIOT_KEY
    assert arg["expectedHost"] == RIOT_HOST
    assert arg["mintUrl"] == DEFAULT_MINT_URL
    assert arg["invisible"] is True


async def test_enterprise_rqdata_reaches_the_widget():
    page = FakePage(result=_widget(token="E0_" + "B" * 140, diag={"api": True}))
    ctx = SolverContext(browser=FakeProvider(page))
    task = _task({"rqdata": "rq-abc"}, type_=CaptchaType.HCAPTCHA_ENTERPRISE)

    result = await _solve(page, task, ctx)

    assert result.token.startswith("E0_")
    _, arg = page.calls[0]
    assert arg["rqdata"] == "rq-abc"
    assert arg["enterprise"] is True


async def test_clicks_checkbox_frame_before_minting():
    frame = FakeFrame()
    page = FakePage(result=_widget(), frames=[frame])

    await _solve(page, _task(), SolverContext(browser=FakeProvider(page)))

    assert frame.locator_obj.clicks == [{"timeout": 5_000}]


async def test_works_with_legacy_pool_acquire():
    page = FakePage(result=_widget())

    result = await _solve(page, _task(), SolverContext(browser=LegacyPool(page)))

    assert result.token == TOKEN
    # no session.goto in the baseline pool: navigation falls back to the page
    assert page.gotos == [RIOT_URL]


async def test_solution_ua_is_the_page_ua_not_the_client_one():
    page = FakePage(result=_widget(ua="page-ua"))
    task = _task(user_agent="client-ua")

    result = await _solve(page, task, SolverContext(browser=FakeProvider(page)))

    assert result.extra == {"userAgent": "page-ua"}


# ---- failure mapping --------------------------------------------------------


async def test_challenge_shown_is_retryable():
    page = FakePage(result=_widget(token=None, diag={"challengeOpened": True}))
    ctx = SolverContext(browser=FakeProvider(page))

    with pytest.raises(ChallengeExpiredError) as exc:
        await _solve(page, _task(), ctx)

    assert exc.value.context["reason"] == "challenge_shown"
    assert exc.value.context["host"] == RIOT_HOST


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (_widget(token=None, diag={}), "no_token"),
        (_widget(token=None, error="rate-limited", diag={}), "rate-limited"),
    ],
)
async def test_missing_token_is_retryable(result: dict, reason: str):
    page = FakePage(result=result)
    with pytest.raises(ChallengeExpiredError) as exc:
        await _solve(page, _task(), SolverContext(browser=FakeProvider(page)))

    assert exc.value.context["reason"] == reason


@pytest.mark.parametrize(
    "result",
    [_widget(host="evil.example", token=TOKEN), {"host": "", "token": None, "error": "wrong-origin"}],
)
async def test_wrong_origin_is_terminal(result: dict):
    page = FakePage(result=result)
    with pytest.raises(UnsolvableError) as exc:
        await _solve(page, _task(), SolverContext(browser=FakeProvider(page)))

    assert exc.value.context["expected_host"] == RIOT_HOST


async def test_echoed_sitekey_is_not_a_solution():
    page = FakePage(result=_widget(token=RIOT_KEY))
    with pytest.raises(UnsolvableError) as exc:
        await _solve(page, _task(), SolverContext(browser=FakeProvider(page)))

    assert "sitekey" in exc.value.message


async def test_missing_provider_is_config_error():
    page = FakePage(result=_widget())

    with pytest.raises(ConfigError):
        await _solve(page, _task(), SolverContext())


async def test_unknown_site_without_sitekey_is_unsolvable():
    page = FakePage(result=_widget())

    with pytest.raises(UnsolvableError) as exc:
        await _solve(page, _task(url="https://unknown.example/login"), SolverContext(browser=FakeProvider(page)))

    assert exc.value.context["host"] == "unknown.example"


async def test_missing_website_url_is_unsolvable():
    page = FakePage(result=_widget())

    with pytest.raises(UnsolvableError):
        await _solve(page, _task(url=None), SolverContext(browser=FakeProvider(page)))


async def test_browser_only_solver_rejects_http():
    with pytest.raises(ConfigError):
        await HCaptchaBrowserSolver().solve_http(_task(), SolverContext())


# ---- token shape ------------------------------------------------------------


@pytest.mark.parametrize("token", ["P1_" + "a" * 120, "E0_" + "b" * 140, "E1_" + "c" * 100])
def test_validate_token_accepts_pass_prefixes(token: str) -> None:
    assert validate_token(token) == token


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("", "empty token"),
        ("P1_short", "too short"),
        ("X0_" + "z" * 150, "prefix"),
        (RIOT_KEY, "sitekey"),
    ],
)
def test_validate_token_rejects_unusable_values(value: str, fragment: str) -> None:
    assert fragment in (token_problem(value, sitekey=RIOT_KEY) or "")
    with pytest.raises(UnsolvableError):
        validate_token(value, sitekey=RIOT_KEY)


def test_validate_token_unwraps_response_objects() -> None:
    assert validate_token({"response": TOKEN}) == TOKEN


# ---- site profiles ----------------------------------------------------------


def test_riot_profile_is_browser_only():
    site = resolve_site(site_url=RIOT_URL)

    assert site.name == "riot-authenticate"
    assert site.mode is SolveMode.BROWSER
    assert site.allows_browser is True
    assert site.sitekey == RIOT_KEY
    assert site.invisible is True


def test_steam_profile_stays_auto_and_takes_payload_sitekey():
    site = resolve_site(
        site_url="https://steamcommunity.com/openid/login",
        payload={"sitekey": "steam-key", "isInvisible": "false"},
    )

    assert site.mode is SolveMode.AUTO
    assert site.sitekey == "steam-key"
    assert site.invisible is False


def test_unknown_host_falls_back_to_auto():
    site = resolve_site(site_url="https://captcha.example/join")

    assert site.mode is SolveMode.AUTO
    assert site.name == "unknown:captcha.example"
    assert site.sitekey is None


def test_enterprise_payload_sets_flag_and_rqdata():
    site = resolve_site(
        site_url=RIOT_URL,
        payload={"enterprisePayload": {"rqdata": "rq-1"}},
        captcha_type=CaptchaType.HCAPTCHA_ENTERPRISE,
    )

    assert site.enterprise is True
    assert site.rqdata == "rq-1"


def test_payload_cannot_override_mint_url():
    site = resolve_site(
        site_url=RIOT_URL,
        payload={"mintUrl": "https://evil.example/api.js", "mint_url": "https://evil.example/api.js"},
    )

    assert site.mint_url == DEFAULT_MINT_URL


def test_subdomain_matches_the_profile():
    site = resolve_site(site_url="https://www.steamcommunity.com/openid/login")

    assert site.name == "steam-community"
    assert site.mode is SolveMode.AUTO


def test_register_site_makes_a_profile_resolvable():
    register_site(
        Site(
            name="custom-login",
            host="login.example.test",
            sitekey="custom-key",
            invisible=True,
            mode=SolveMode.BROWSER,
        )
    )

    site = resolve_site(site_url="https://login.example.test/enter")
    assert site.sitekey == "custom-key"
    assert site.mode is SolveMode.BROWSER
    assert any(known.host == "login.example.test" for known in all_sites())
