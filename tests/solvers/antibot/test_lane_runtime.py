"""Lane plumbing the five antibot solvers share (`solvers.antibot._runtime`).

Two things here are load-bearing:

* the **replay guard** -- a captured engine script is untrusted input, and letting it
  aim our transport at an arbitrary host turns a task payload into an SSRF
  primitive. Only the site itself, its subdomains and the vendor's own API hosts
  may be replayed to;
* the **capability classification** -- a worker without a JS runtime is not a
  config error, it is a task the browser lane can still serve (`NeedsBrowserError`,
  one hop), while a missing browser provider for a task already in the browser lane
  is terminal (`ConfigError`).

No network, no browser: every provider is a fake.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from captcha.domain import Task
from captcha.domain.enums import CaptchaType
from captcha.domain.errors import ConfigError, JSRuntimeError, NeedsBrowserError
from captcha.domain.ports import BrowserSpec
from captcha.domain.task import new_task_id
from captcha.solvers.antibot import _runtime

SITE = "https://acme.example/login"
PX_VENDORS = ("px-cloud.net", "perimeterx.net")


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.example/_Incapsula_Resource?e=1",
        "https://cdn.acme.example/sensor",
        "https://acme.example:8443/x",
    ],
)
def test_replayable_allows_the_site_and_its_subdomains(url: str) -> None:
    assert _runtime.replayable(url, SITE)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/steal",
        "https://acme.example.evil.example/x",
        "https://10.0.0.5/internal",
        "",
    ],
)
def test_replayable_refuses_anything_else(url: str) -> None:
    assert not _runtime.replayable(url, SITE)


def test_replayable_allows_the_named_vendors_only() -> None:
    assert _runtime.replayable("https://collector-PXabc.px-cloud.net/api/v2/collector", SITE, vendors=PX_VENDORS)
    assert _runtime.replayable("https://captcha.px-cloud.net/PXabc", SITE, vendors=PX_VENDORS)
    assert not _runtime.replayable("https://px-cloud.net.evil.example/x", SITE, vendors=PX_VENDORS)


def test_absolute_resolves_a_captured_path_against_the_site() -> None:
    # solvers pass an origin here (imperva/payload.origin strips the path)
    assert (
        _runtime.absolute("/_Incapsula_Resource", "https://acme.example") == "https://acme.example/_Incapsula_Resource"
    )
    assert _runtime.absolute("https://x.test/a", SITE) == "https://x.test/a"
    assert _runtime.absolute("TSPD/08abc", "https://acme.example/") == "https://acme.example/TSPD/08abc"


def test_cookie_shapes_normalize() -> None:
    assert _runtime.cookies_to_dict({"a": "b"}) == {"a": "b"}
    assert _runtime.cookies_to_dict([{"name": "a", "value": "b"}, {"name": "c", "value": 1}]) == {"a": "b", "c": "1"}
    assert _runtime.cookies_to_dict("a=b; c=d; path=/") == {"a": "b", "c": "d"}
    assert _runtime.cookies_to_dict(None) == {}
    assert _runtime.cookie_pairs("") == {}
    assert _runtime.cookie_pairs("a=; b=2") == {"b": "2"}


def test_cookie_spec_is_the_browser_provider_shape() -> None:
    assert _runtime.cookie_spec({"a": "b"}) == [{"name": "a", "value": "b"}]
    assert _runtime.cookie_spec("a=b; c=d") == [{"name": "a", "value": "b"}, {"name": "c", "value": "d"}]


def test_browser_spec_carries_the_task_proxy_and_payload_cookies() -> None:
    task = Task(id=new_task_id(), type=CaptchaType.IMPERVA, payload={}, proxy="http://p:1")
    spec = _runtime.browser_spec(task, {"cookies": {"reese84": "v"}}, label="imperva:1")
    assert isinstance(spec, BrowserSpec)
    assert spec.proxy == "http://p:1"
    assert spec.cookies == [{"name": "reese84", "value": "v"}]
    assert spec.label == "imperva:1"


class FakeSession:
    def __init__(self, jars: list[list[dict[str, Any]]] | None = None) -> None:
        self.jars = jars or []
        self.navigated: list[str] = []

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 60_000) -> Any:
        self.navigated.append(url)
        return self

    async def cookies(self, url: str | None = None) -> list[dict[str, Any]]:
        return self.jars.pop(0) if self.jars else []


class FakeResponse:
    def __init__(self, cookies: list[str] | None = None) -> None:
        self.headers = cookies or []

    def headers_all(self, name: str) -> list[str]:
        return list(self.headers) if name.lower() == "set-cookie" else []


async def test_wait_for_cookie_polls_until_a_prefix_matches() -> None:
    session = FakeSession([[{"name": "_pxvid", "value": "v1"}], [{"name": "incap_ses_1234", "value": "s1"}]])
    name, value = await _runtime.wait_for_cookie(session, prefixes=("incap_ses",), url=SITE, timeout_s=5, interval_s=0)
    assert (name, value) == ("incap_ses_1234", "s1")


async def test_wait_for_cookie_returns_empty_when_the_budget_runs_out() -> None:
    session = FakeSession([[{"name": "_pxvid", "value": "v1"}]])
    assert await _runtime.wait_for_cookie(session, prefixes=("TS",), url=SITE, timeout_s=0) == ("", "")


def test_set_cookie_keeps_repeated_headers() -> None:
    assert _runtime.set_cookie(FakeResponse(["a=1", "b=2"])) == ["a=1", "b=2"]


async def test_open_session_without_a_provider_is_terminal() -> None:
    with pytest.raises(ConfigError):
        async with _runtime.open_session(object(), BrowserSpec(label="x"), solver="imperva"):
            pass


class FakeProvider:
    def __init__(self, session: Any) -> None:
        self.session_obj = session

    def session(self, spec: Any) -> Any:
        @asynccontextmanager
        async def cm() -> Any:
            yield self.session_obj

        return cm()


async def test_open_session_yields_a_navigable_session() -> None:
    session = FakeSession()
    ctx = type("Ctx", (), {"browser": FakeProvider(session)})()
    async with _runtime.open_session(ctx, BrowserSpec(label="x"), solver="imperva") as opened:
        await _runtime.navigate(opened, SITE)
    assert session.navigated == [SITE]


async def test_run_script_escalates_when_the_worker_has_no_runtime() -> None:
    with pytest.raises(NeedsBrowserError):
        await _runtime.run_script(object(), engine="tspd", script="var x = 1;")


async def test_run_script_rejects_an_empty_script() -> None:
    class Runtime:
        async def exec_script(self, code: str, globals_: Any = None, *, timeout_ms: int | None = None) -> Any:
            return None

    with pytest.raises(JSRuntimeError):
        await _runtime.run_script(type("Ctx", (), {"js_runtime": Runtime()})(), engine="tspd", script="  ")


async def test_run_script_passes_globals_and_timeout_through() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def exec_script(self, code: str, globals_: Any = None, *, timeout_ms: int | None = None) -> Any:
            self.calls.append({"code": code, "globals": globals_, "timeout_ms": timeout_ms})
            return '{"cookies": {}}'

    runtime = Runtime()
    ctx = type("Ctx", (), {"js_runtime": runtime})()
    raw = await _runtime.run_script(ctx, engine="tspd", script="var x = 1;", globals_={"a": 1}, timeout_ms=1234)
    assert raw == '{"cookies": {}}'
    assert runtime.calls == [{"code": "var x = 1;", "globals": {"a": 1}, "timeout_ms": 1234}]
