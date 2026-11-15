from __future__ import annotations

import pytest
from helpers import PAGE_URL, FakeProvider, make_task

from captcha.domain import SolveMode
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.solvers import SolverContext
from captcha.solvers.funcaptcha import browser
from captcha.solvers.funcaptcha.funcaptcha import FunCaptchaSolver

TOKEN = "abcdef0123456789abcdef0123456789|r=eu-west-1|1712000000"


async def run(provider: FakeProvider | None, task=None):
    ctx = SolverContext(browser=provider)
    return await FunCaptchaSolver().solve_browser(task or make_task(), ctx)


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    """Keep the polling loop out of the test runtime budget."""
    monkeypatch.setattr(browser, "DEFAULT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(browser, "MIN_TIMEOUT_S", 0.05)
    monkeypatch.setattr(browser, "POLL_INTERVAL_S", 0.01)


async def test_widget_token_is_the_answer():
    provider = FakeProvider([{"token": TOKEN, "challenge_shown": False}])

    result = await run(provider)

    assert result.token == TOKEN
    assert result.solve_mode is SolveMode.BROWSER
    assert result.extra["path"] == "browser"
    assert result.extra["userAgent"].startswith("Mozilla/5.0")
    assert provider.sessions[0].navigated == [PAGE_URL]


async def test_polling_waits_for_the_widget_to_finish():
    provider = FakeProvider([{"token": None, "challenge_shown": True}, {"token": TOKEN}])

    result = await run(provider)

    assert result.token == TOKEN
    assert provider.sessions[0].evaluate_calls == 2


async def test_visible_challenge_without_a_token_is_challenge_expired():
    provider = FakeProvider([{"token": None, "challenge_shown": True}])

    with pytest.raises(ChallengeExpiredError) as exc:
        await run(provider)

    assert exc.value.context["reason"] == "challenge_shown"
    assert exc.value.context["url"] == PAGE_URL


async def test_short_token_is_unsolvable():
    provider = FakeProvider([{"token": "short"}])

    with pytest.raises(UnsolvableError) as exc:
        await run(provider)

    assert exc.value.context["length"] == 5


async def test_missing_browser_provider_is_config_error():
    with pytest.raises(ConfigError):
        await run(None)


async def test_missing_page_url_is_unsolvable():
    task = make_task(site_url=None, payload={"pageurl": None, "websiteURL": None})

    with pytest.raises(UnsolvableError):
        await run(FakeProvider([{"token": TOKEN}]), task)


async def test_non_http_page_url_is_rejected():
    task = make_task(site_url="file:///etc/passwd")

    with pytest.raises(UnsolvableError):
        await run(FakeProvider([{"token": TOKEN}]), task)


async def test_session_spec_carries_proxy_and_cookies():
    provider = FakeProvider([{"token": TOKEN}])
    task = make_task(proxy="http://user:pass@127.0.0.1:9000", payload={"cookies": {"session": "abc"}})

    await run(provider, task)

    spec = provider.specs[0]
    assert spec.proxy == "http://user:pass@127.0.0.1:9000"
    assert spec.cookies == [{"name": "session", "value": "abc"}]
    assert spec.label == f"funcaptcha:{task.id}"
    assert spec.headless is True
