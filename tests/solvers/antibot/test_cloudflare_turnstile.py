"""Turnstile solver: HTTP bootstrap + browser mint, against fakes only.

No browser, no network. The provider hands the solver a page whose evaluate()
returns a canned widget report, so every branch -- token, placeholder, widget
error code, wrong origin, escalation -- is deterministic.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from captcha.domain import CaptchaType, SolveMode, Task
from captcha.domain.errors import (
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    SensorError,
    UnsolvableError,
)
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext
from captcha.solvers.antibot.cloudflare.endpoints import TURNSTILE_API_JS, TURNSTILE_ORIGIN
from captcha.solvers.antibot.cloudflare.turnstile import TurnstileSolver

SITE_URL = "https://site.test/login"
HOST = "site.test"
SITEKEY = "0x4AAAAAAABkMYinukE8"
TOKEN = "0." + "A" * 120
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"
BUNDLE_URL = TURNSTILE_ORIGIN + "/turnstile/v0/b/8f1a2b3c/api.js"


# ---- fakes ------------------------------------------------------------------


class FakeKeyboard:
    def __init__(self) -> None:
        self.presses: list[str] = []

    async def press(self, key: str) -> None:
        self.presses.append(key)


class FakeLocator:
    def __init__(self) -> None:
        self.clicks: list[dict] = []

    async def count(self) -> int:
        return 1

    async def click(self, **kwargs) -> None:
        self.clicks.append(kwargs)


class FakeFrame:
    url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/key"

    def __init__(self) -> None:
        self.locator_obj = FakeLocator()

    def locator(self, selector: str) -> FakeLocator:
        self.selector = selector
        return self.locator_obj


class FakePage:
    def __init__(self, results: list, *, frames=(), keyboard: bool = True) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []
        self.frames = list(frames)
        self.keyboard = FakeKeyboard() if keyboard else None

    async def evaluate(self, script: str, arg=None):
        self.calls.append((script, arg or {}))
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


class FakeContext:
    def __init__(self, cookies: list[dict] | None = None) -> None:
        self._cookies = cookies or []

    async def cookies(self) -> list[dict]:
        return self._cookies


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.context = FakeContext()
        self.gotos: list[tuple[str, str, int]] = []

    async def page(self) -> FakePage:
        return self._page

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 60_000):
        self.gotos.append((url, wait_until, timeout_ms))
        return self._page


class FakeProvider:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.specs: list = []
        self.sessions: list[FakeSession] = []
        self.closed = 0

    @asynccontextmanager
    async def session(self, spec):
        self.specs.append(spec)
        session = FakeSession(self.page)
        self.sessions.append(session)
        try:
            yield session
        finally:
            self.closed += 1


class FakeResponse:
    def __init__(self, status: int = 200, body: str = "", final_url: str = "", headers: dict | None = None):
        self.status = status
        self.body = body.encode()
        self.final_url = final_url
        self.headers = list((headers or {}).items())

    @property
    def text(self) -> str:
        return self.body.decode()

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None


class FakeHTTP:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    async def get(self, url: str, **_kwargs):
        self.calls.append(url)
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected GET {url}")


def _widget(**overrides) -> dict:
    out = {"host": HOST, "href": SITE_URL, "ua": UA, "token": TOKEN, "error": None, "diag": {}}
    out.update(overrides)
    return out


def _task(
    payload: dict | None = None,
    *,
    type_: CaptchaType = CaptchaType.TURNSTILE,
    sitekey: str | None = SITEKEY,
    url: str | None = SITE_URL,
) -> Task:
    return Task(
        id=new_task_id(),
        type=type_,
        payload=payload or {},
        sitekey=sitekey,
        site_url=url,
        preferred_mode=SolveMode.BROWSER,
    )


# ---- browser lane -----------------------------------------------------------


async def test_solves_invisible_widget_and_returns_the_page_ua():
    page = FakePage([_widget()])
    provider = FakeProvider(page)

    result = await TurnstileSolver().solve_browser(
        _task(type_=CaptchaType.TURNSTILE_INVISIBLE), SolverContext(browser=provider)
    )

    assert result.token == TOKEN
    assert result.extra == {"userAgent": UA}
    assert result.solve_mode is SolveMode.BROWSER
    assert result.latency_ms >= 0.0
    assert provider.closed == 1
    # navigation goes through the provider's goto, not straight to the page
    assert provider.sessions[0].gotos == [(SITE_URL, "domcontentloaded", 90_000)]
    assert provider.specs[0].label.startswith("cloudflare:turnstile_invisible:")
    assert provider.specs[0].headless is True

    _, arg = page.calls[0]
    assert arg["sitekey"] == SITEKEY
    assert arg["expectedHost"] == HOST
    assert arg["invisible"] is True
    assert arg["action"] is None
    assert arg["cData"] is None


async def test_visible_enterprise_payload_reaches_the_widget():
    page = FakePage([_widget()])
    provider = FakeProvider(page)
    task = _task({"action": "login", "cData": "challenge-id", "enterprise": True})

    await TurnstileSolver().solve_browser(task, SolverContext(browser=provider))

    _, arg = page.calls[0]
    assert arg["invisible"] is False
    assert arg["action"] == "login"
    assert arg["cData"] == "challenge-id"


async def test_session_spec_carries_proxy_and_cookies():
    page = FakePage([_widget()])
    provider = FakeProvider(page)
    task = Task(
        id=new_task_id(),
        type=CaptchaType.TURNSTILE,
        payload={"cookies": "a=b; c=d"},
        sitekey=SITEKEY,
        site_url=SITE_URL,
        proxy="http://proxy.test:8080",
    )

    await TurnstileSolver().solve_browser(task, SolverContext(browser=provider))

    spec = provider.specs[0]
    assert spec.proxy == "http://proxy.test:8080"
    assert spec.cookies == [{"name": "a", "value": "b"}, {"name": "c", "value": "d"}]


async def test_no_token_is_retryable_and_nudges_the_page():
    page = FakePage([_widget(token=None)])

    with pytest.raises(ChallengeExpiredError) as exc:
        await TurnstileSolver().solve_browser(_task(), SolverContext(browser=FakeProvider(page)))

    assert exc.value.context["reason"] == "no_token"
    assert exc.value.context["sitekey"] == SITEKEY
    assert page.keyboard.presses == ["Tab", "Space"]
    assert len(page.calls) == 2, "a retryable miss gets a second look"


async def test_visible_miss_clicks_the_widget_checkbox_before_the_second_look():
    frame = FakeFrame()
    page = FakePage([_widget(token=None), _widget()], frames=[frame])
    ctx = SolverContext(browser=FakeProvider(page), extra={"timeout_s": 20})

    result = await TurnstileSolver().solve_browser(_task(), ctx)

    assert result.token == TOKEN
    assert frame.locator_obj.clicks == [{"timeout": 5000}]


async def test_terminal_widget_code_is_unsolvable_and_skips_interaction():
    page = FakePage([_widget(token=None, error="widget-error:110200")])

    with pytest.raises(UnsolvableError) as exc:
        await TurnstileSolver().solve_browser(_task(), SolverContext(browser=FakeProvider(page)))

    assert "110200" in exc.value.message
    assert page.keyboard.presses == []


async def test_retryable_widget_code_keeps_the_task_retryable():
    page = FakePage([_widget(token=None, error="widget-error:110500")])

    with pytest.raises(ChallengeExpiredError):
        await TurnstileSolver().solve_browser(_task(), SolverContext(browser=FakeProvider(page)))


@pytest.mark.parametrize(
    "result",
    [
        _widget(host="evil.example"),
        {"host": "", "href": SITE_URL, "ua": UA, "token": None, "error": "wrong-origin", "diag": {}},
    ],
)
async def test_wrong_origin_is_terminal(result: dict):
    with pytest.raises(UnsolvableError) as exc:
        await TurnstileSolver().solve_browser(_task(), SolverContext(browser=FakeProvider(FakePage([result]))))

    assert exc.value.context["expected_host"] == HOST


@pytest.mark.parametrize("token", [SITEKEY, "0.short", "0." + "A" * 30 + " B", "<html>" + "A" * 40 + "</html>"])
async def test_unusable_token_is_not_a_solution(token: str):
    with pytest.raises(UnsolvableError):
        await TurnstileSolver().solve_browser(
            _task(), SolverContext(browser=FakeProvider(FakePage([_widget(token=token)])))
        )


async def test_page_script_returning_nothing_is_a_sensor_error():
    with pytest.raises(SensorError):
        await TurnstileSolver().solve_browser(_task(), SolverContext(browser=FakeProvider(FakePage([None]))))


async def test_missing_provider_is_a_config_error():
    with pytest.raises(ConfigError):
        await TurnstileSolver().solve_browser(_task(), SolverContext())


@pytest.mark.parametrize(
    ("sitekey", "url"),
    [(None, SITE_URL), (SITEKEY, None)],
)
async def test_missing_sitekey_or_url_is_unsolvable(sitekey: str | None, url: str | None):
    with pytest.raises(UnsolvableError):
        await TurnstileSolver().solve_browser(
            _task(sitekey=sitekey, url=url), SolverContext(browser=FakeProvider(FakePage([_widget()])))
        )


# ---- HTTP lane: bootstrap, then escalate ------------------------------------


def _probe_http(*, loader=None, bundle_body: str = "", widget_status: int = 200) -> FakeHTTP:
    if loader is None:
        loader = FakeResponse(200, "<html>loader</html>", final_url=BUNDLE_URL)
    return FakeHTTP(
        {
            TURNSTILE_API_JS: loader,
            BUNDLE_URL: FakeResponse(
                200,
                bundle_body or 'var p = "/cdn-cgi/challenge-platform/h/g/turnstile/if/ov2/av0/";',
            ),
            TURNSTILE_ORIGIN + "/cdn-cgi/challenge-platform/": FakeResponse(widget_status, "<html>widget</html>"),
        }
    )


async def test_http_bootstrap_escalates_with_what_it_learned():
    http = _probe_http()

    with pytest.raises(NeedsBrowserError) as exc:
        await TurnstileSolver().solve_http(_task(), SolverContext(http=http))

    context = exc.value.context
    assert context["sitekey"] == SITEKEY
    assert context["variant"] == "g"
    assert context["bundle"] == BUNDLE_URL
    assert context["widget_status"] == 200
    assert context["bootstrap_error"] is None


async def test_http_probe_failure_still_escalates():
    http = _probe_http(loader=ChallengeExpiredError("antibot verdict: HTTP 403"))

    with pytest.raises(NeedsBrowserError) as exc:
        await TurnstileSolver().solve_http(_task(), SolverContext(http=http))

    assert "ERR_CHALLENGE_EXPIRED" in exc.value.context["bootstrap_error"]
    assert exc.value.context["bundle"] is None


async def test_http_lane_without_a_provider_is_a_config_error():
    with pytest.raises(ConfigError):
        await TurnstileSolver().solve_http(_task(), SolverContext())


async def test_http_finishes_in_process_when_a_provider_is_present():
    page = FakePage([_widget()])
    ctx = SolverContext(browser=FakeProvider(page))

    result = await TurnstileSolver().solve_http(_task(), ctx)

    assert result.token == TOKEN
