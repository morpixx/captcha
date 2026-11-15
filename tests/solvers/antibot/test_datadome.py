"""DataDome solver tests — recorded fixtures and fakes, no network.

Covers the three paths the solver owns:

* ``jspl`` / tags.js crypto (xorshift32, custom base64, seed) — pure functions,
  round-trip through the decoder plus frozen vectors so a refactor cannot
  silently change the byte stream;
* the tags POST (body fields, Set-Cookie vs JSON ``cookie``, SensorError /
  ChallengeExpiredError);
* Device Check / interstitial through the JS runtime and the browser lane
  (``NeedsBrowserError`` escalation, cookie shapes, COOKIE_ENGINE projection).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.errors import (
    ChallengeExpiredError,
    ConfigError,
    JSRuntimeError,
    NeedsBrowserError,
    SensorError,
    UnsolvableError,
)
from captcha.domain.solution import build_solution
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext
from captcha.solvers.antibot.datadome import endpoints, tags
from captcha.solvers.antibot.datadome.solver import DataDomeSolver

FIXTURES = Path(__file__).with_name("fixtures") / "datadome"
DDJSKEY = "6E4B2C7A9F1D3E5B"
PAGE_URL = "https://example.com/login"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"
TIMESTAMP = 1_700_000_000_000
COOKIE_FIXTURE = "fixture-cookie-value"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- fakes -------------------------------------------------------------------


class FakeResponse:
    """Duck-typed stand-in for captcha.adapters.transport.Response."""

    def __init__(self, body: Any = b"", *, status: int = 200, headers: Any = ()) -> None:
        self.status = status
        self.body = body if isinstance(body, (bytes, str)) else json.dumps(body).encode("utf-8")
        self.headers = list(headers)
        self.elapsed_ms = 0.0
        self.final_url = ""

    @classmethod
    def recorded(cls, data: dict[str, Any]) -> FakeResponse:
        return cls(data.get("body", ""), status=data["status"], headers=data["headers"])

    def header(self, name: str) -> str | None:
        hits = self.headers_all(name)
        return hits[-1] if hits else None

    def headers_all(self, name: str) -> list[str]:
        return [value for key, value in self.headers if key.lower() == name.lower()]

    def json(self) -> Any:
        return json.loads(self.body)


class FakeHTTP:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected POST {url}")
        return self.responses.pop(0)


class FakeRuntime:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def exec_script(
        self,
        code: str,
        context_globals: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> Any:
        self.calls.append({"code": code, "globals": context_globals, "timeout_ms": timeout_ms})
        if not self.results:
            raise AssertionError("unexpected exec_script")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeContext:
    def __init__(self, cookies: list[dict[str, Any]] | None = None) -> None:
        self._cookies = cookies or []

    async def cookies(self, *args: Any) -> list[dict[str, Any]]:
        return list(self._cookies)


class FakeSession:
    def __init__(self, cookies: list[dict[str, Any]] | None = None) -> None:
        self.context = FakeContext(cookies)
        self.navigated: list[str] = []

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 60_000) -> Any:
        self.navigated.append(url)
        return self


class FakeProvider:
    def __init__(self, session: FakeSession) -> None:
        self._session = session
        self.specs: list[Any] = []

    def session(self, spec: Any) -> Any:
        self.specs.append(spec)

        @asynccontextmanager
        async def cm() -> Any:
            yield self._session

        return cm()


def make_task(captcha_type: CaptchaType, **payload: Any) -> Task:
    base = {"websiteURL": PAGE_URL, "userAgent": UA}
    base.update(payload)
    return Task(id=new_task_id(), type=captcha_type, payload=base)


def solve_http(http: Any, *extra: Any, task: Task | None = None, **ctx_kwargs: Any) -> Any:
    ctx = SolverContext(http=http, **ctx_kwargs)
    task = task or make_task(CaptchaType.DATADOME_FULL, ddjskey=DDJSKEY, timestamp_ms=TIMESTAMP)
    return DataDomeSolver().solve_http(task, ctx)


# --- jspl / tags.js crypto ---------------------------------------------------


def test_xorshift_step_matches_the_reference_vector() -> None:
    # Reference xorshift32 stream: 1 -> 270369 -> 67634689.
    assert tags.mix_int(1) == 270369
    assert tags.mix_int(270369) == 67634689
    assert tags.mix_int(0) == 0
    assert tags.mix_int(2) == 540738


def test_custom_hash_is_the_djb2_variant() -> None:
    assert tags.custom_hash("") == tags.DD_HASH_FALLBACK
    assert tags.custom_hash("a") == 97
    assert tags.custom_hash("ab") == 3105
    assert tags.custom_hash("abc") == 96354


def test_alphabet_is_the_documented_custom_base64() -> None:
    assert tags.ALPHABET == "-_0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    assert len(set(tags.ALPHABET)) == 64
    assert [tags.encode6bits(v) for v in (0, 1, 2, 11, 12, 37, 38, 63)] == [
        "-",
        "_",
        "0",
        "9",
        "A",
        "Z",
        "a",
        "z",
    ]
    with pytest.raises(ValueError):
        tags.encode6bits(64)


@pytest.mark.parametrize("length", range(1, 11))
def test_encode_payload_round_trips_every_tail(length: int) -> None:
    data = bytes((i * 37 + 11) % 256 for i in range(length))
    encoded = tags.encode_payload(data, 1234)
    assert set(encoded) <= set(tags.ALPHABET)
    assert tags.decode_payload(encoded, 1234) == data


def test_decode_payload_rejects_foreign_characters() -> None:
    with pytest.raises(ValueError):
        tags.decode_payload("not+base64!", 0)


def test_jspl_is_deterministic_invertible_and_timestamp_scoped() -> None:
    signals = {"d": "x", "n": 42, "b": True, "u": "žluťoučký"}
    first = tags.build_jspl(signals, ddjskey=DDJSKEY, timestamp_ms=TIMESTAMP)
    assert first == tags.build_jspl(signals, ddjskey=DDJSKEY, timestamp_ms=TIMESTAMP)
    assert set(first) <= set(tags.ALPHABET)
    assert tags.decode_jspl(first, ddjskey=DDJSKEY, timestamp_ms=TIMESTAMP) == signals
    # Same signals, next request: the salt moves, the string must too.
    assert tags.build_jspl(signals, ddjskey=DDJSKEY, timestamp_ms=TIMESTAMP + 1) != first


def test_tags_body_carries_the_wire_fields() -> None:
    body = tags.build_tags_body(
        ddjskey=DDJSKEY,
        cid="cid-1",
        hsh="hsh-1",
        page_url=PAGE_URL,
        user_agent=UA,
        signals={"k": "v"},
        timestamp_ms=TIMESTAMP,
    )
    assert set(body) == {"jspl", "cid", "ddk", "hsh", "responsePage", "ua", "jsType"}
    assert body["ddk"] == DDJSKEY
    assert body["responsePage"] == PAGE_URL
    assert body["jsType"] == "ch"
    decoded = tags.decode_jspl(body["jspl"], ddjskey=DDJSKEY, timestamp_ms=TIMESTAMP)
    assert decoded == {"cid": "cid-1", "ddk": DDJSKEY, "hsh": "hsh-1", "url": PAGE_URL, "ua": UA, "k": "v"}


def test_datadome_from_body_reads_json_cookie_fields() -> None:
    assert tags.datadome_from_body(b'{"cookie": "datadome=abc==; Path=/"}') == {"datadome": "abc=="}
    assert tags.datadome_from_body(b'{"cookies": {"datadome": "xyz"}}') == {"datadome": "xyz"}
    assert tags.datadome_from_body(b"<html>") == {}


# --- tags.js HTTP path -------------------------------------------------------


async def test_tags_path_posts_jspl_and_returns_cookie() -> None:
    http = FakeHTTP(FakeResponse.recorded(fixture("tags_ok.json")))
    result = await solve_http(http)

    call = http.calls[0]
    assert call["url"] == endpoints.DATADOME_JS_ENDPOINT
    assert call["headers"]["User-Agent"] == UA
    assert call["headers"]["Referer"] == PAGE_URL
    assert call["json"]["ddk"] == DDJSKEY
    assert tags.decode_jspl(call["json"]["jspl"], ddjskey=DDJSKEY, timestamp_ms=TIMESTAMP)["ua"] == UA

    assert result.token == COOKIE_FIXTURE
    assert result.solve_mode is SolveMode.HTTP
    assert result.extra["cookies"] == {"datadome": COOKIE_FIXTURE}
    assert result.extra["userAgent"] == UA
    assert result.extra["domain"] == "example.com"
    assert result.extra["headers"] == {"User-Agent": UA}


async def test_tags_path_falls_back_to_json_body_cookie() -> None:
    headers = [["Content-Type", "application/json"]]
    http = FakeHTTP(FakeResponse('{"cookie": "datadome=body-cookie; Path=/"}', headers=headers))
    result = await solve_http(http)
    assert result.token == "body-cookie"
    assert result.extra["cookies"] == {"datadome": "body-cookie"}


async def test_tags_path_maps_http_error_to_sensor_error() -> None:
    http = FakeHTTP(FakeResponse("blocked", status=403))
    with pytest.raises(SensorError) as exc:
        await solve_http(http)
    assert exc.value.context["status"] == 403


async def test_tags_path_without_cookie_is_challenge_expired() -> None:
    http = FakeHTTP(FakeResponse("{}", headers=[["Content-Type", "application/json"]]))
    with pytest.raises(ChallengeExpiredError):
        await solve_http(http)


async def test_tags_path_needs_a_user_agent() -> None:
    task = Task(
        id=new_task_id(),
        type=CaptchaType.DATADOME_FULL,
        payload={"websiteURL": PAGE_URL, "ddjskey": DDJSKEY},
    )
    with pytest.raises(UnsolvableError):
        await solve_http(FakeHTTP(), task=task)


async def test_full_without_ddjskey_escalates_to_browser() -> None:
    task = make_task(CaptchaType.DATADOME_FULL)
    with pytest.raises(NeedsBrowserError) as exc:
        await solve_http(FakeHTTP(), task=task)
    assert exc.value.code == "ERR_NEEDS_BROWSER"


async def test_payload_cookie_short_circuits_the_network() -> None:
    http = FakeHTTP()
    task = make_task(CaptchaType.DATADOME_SLIDER, cookies="datadome=already-have; other=1")
    result = await solve_http(http, task=task)
    assert http.calls == []
    assert result.token == "already-have"
    assert result.extra["cookies"] == {"datadome": "already-have"}


async def test_missing_website_url_is_unsolvable() -> None:
    task = Task(id=new_task_id(), type=CaptchaType.DATADOME_FULL, payload={"ddjskey": DDJSKEY, "userAgent": UA})
    with pytest.raises(UnsolvableError):
        await solve_http(FakeHTTP(), task=task)


# --- Device Check / interstitial (js-runtime) --------------------------------


async def test_device_check_runs_the_challenge_script() -> None:
    runtime = FakeRuntime(fixture("interstitial_ok.json"))
    http = FakeHTTP()
    task = make_task(
        CaptchaType.DATADOME_SLIDER,
        challenge_js="window.dd=1;",
        ddjskey=DDJSKEY,
        hsh="hsh-1",
        cid="cid-1",
    )
    result = await solve_http(http, task=task, js_runtime=runtime)

    call = runtime.calls[0]
    assert call["code"] == "window.dd=1;"
    assert call["globals"] == {
        "ddjskey": DDJSKEY,
        "cid": "cid-1",
        "hsh": "hsh-1",
        "href": PAGE_URL,
        "userAgent": UA,
    }
    assert http.calls == []
    assert result.token == "js-cookie-value"
    assert result.extra["headers"] == {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) DataDome/1.0",
        "x-dd-b": "1.0.0",
    }
    assert result.extra["userAgent"] == "Mozilla/5.0 (X11; Linux x86_64) DataDome/1.0"


async def test_device_check_bare_cookie_string_is_accepted() -> None:
    runtime = FakeRuntime("plain-cookie")
    task = make_task(CaptchaType.DATADOME_SLIDER, challenge_js="1;")
    result = await solve_http(FakeHTTP(), task=task, js_runtime=runtime)
    assert result.token == "plain-cookie"


async def test_device_check_without_cookie_is_challenge_expired() -> None:
    runtime = FakeRuntime({})
    task = make_task(CaptchaType.DATADOME_SLIDER, challenge_js="1;")
    with pytest.raises(ChallengeExpiredError):
        await solve_http(FakeHTTP(), task=task, js_runtime=runtime)


async def test_device_check_runtime_failure_is_js_runtime_error() -> None:
    runtime = FakeRuntime(RuntimeError("boom"))
    task = make_task(CaptchaType.DATADOME_SLIDER, challenge_js="1;")
    with pytest.raises(JSRuntimeError):
        await solve_http(FakeHTTP(), task=task, js_runtime=runtime)


async def test_device_check_without_runtime_escalates() -> None:
    task = make_task(CaptchaType.DATADOME_SLIDER, challenge_js="1;")
    with pytest.raises(NeedsBrowserError):
        await solve_http(FakeHTTP(), task=task)


async def test_device_check_without_script_escalates() -> None:
    task = make_task(CaptchaType.DATADOME_SLIDER)
    with pytest.raises(NeedsBrowserError):
        await solve_http(FakeHTTP(), task=task, js_runtime=FakeRuntime())


# --- browser lane ------------------------------------------------------------


async def test_browser_lane_reads_the_cookie_from_the_jar() -> None:
    session = FakeSession([{"name": "datadome", "value": "browser-cookie", "domain": ".example.com"}])
    provider = FakeProvider(session)
    task = make_task(CaptchaType.DATADOME_FULL, ddjskey=DDJSKEY, timeout_s=0)

    result = await DataDomeSolver().solve_browser(task, SolverContext(browser=provider))

    assert session.navigated == [PAGE_URL]
    assert provider.specs[0].proxy is None
    assert result.token == "browser-cookie"
    assert result.solve_mode is SolveMode.BROWSER
    assert result.extra["cookies"] == {"datadome": "browser-cookie"}


async def test_browser_lane_without_cookie_is_challenge_expired() -> None:
    provider = FakeProvider(FakeSession())
    task = make_task(CaptchaType.DATADOME_FULL, ddjskey=DDJSKEY, timeout_s=0)
    with pytest.raises(ChallengeExpiredError):
        await DataDomeSolver().solve_browser(task, SolverContext(browser=provider))


async def test_browser_lane_without_provider_is_config_error() -> None:
    task = make_task(CaptchaType.DATADOME_FULL, ddjskey=DDJSKEY, timeout_s=0)
    with pytest.raises(ConfigError):
        await DataDomeSolver().solve_browser(task, SolverContext())


# --- answer projection -------------------------------------------------------


def test_cookie_engine_projection_emits_cookies_and_domains() -> None:
    result = SolverResult(
        token="proj-cookie",
        extra={"cookies": {"datadome": "proj-cookie"}, "userAgent": UA},
    )
    solution = build_solution(
        CaptchaType.DATADOME_FULL,
        {"token": result.token, "extra": result.extra, "solve_mode": "http"},
        site_url=PAGE_URL,
    )
    assert solution is not None
    assert solution["cookies"] == {"datadome": "proj-cookie"}
    assert solution["domains"] == {"example.com": {"cookies": {"datadome": "proj-cookie"}}}
    assert solution["userAgent"] == UA


def test_solver_info_declares_both_paths() -> None:
    info = DataDomeSolver.info
    assert info.types == (CaptchaType.DATADOME_FULL, CaptchaType.DATADOME_SLIDER)
    assert info.http is True
    assert info.browser is True
    assert info.needs_js_runtime is True
    assert info.experimental is True
