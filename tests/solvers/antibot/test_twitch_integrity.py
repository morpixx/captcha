"""Twitch Integrity solver tests -- recorded shapes and fakes, no network.

Pins the three things that matter for this type:

* the header envelope and the KPSDK header merge (the p.js output wins over
  the caller's defaults);
* the token envelope (v4.public + 64-byte signature) and the is_bad_bot
  verdict, because returning a flagged token looks like a solve and pays
  for a failure;
* the error mapping: 401 -> SensorError, 410/empty -> ChallengeExpiredError,
  missing runtime/device -> ConfigError/SensorError, no bootstrap -> browser.
"""

from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from captcha.domain import CaptchaType, SolveMode, Task
from captcha.domain.errors import (
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    SensorError,
)
from captcha.domain.solution import build_solution
from captcha.domain.task import new_task_id
from captcha.engines.twitch.builder import INTEGRITY_URL, TwitchIntegrityBuilder
from captcha.solvers import SolverContext
from captcha.solvers.antibot.twitch_integrity import wire
from captcha.solvers.antibot.twitch_integrity.solver import TwitchIntegritySolver

PAGE = "https://www.twitch.tv/somechannel"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
DEVICE = "device-1"
EXPIRATION = 1_700_000_600_000
KPSDK_HEADERS = {"x-kpsdk-ct": "ct-1", "x-kpsdk-cd": "cd-1", "x-kpsdk-v": "j-1.1.0"}


def make_token(claims: dict[str, Any] | None = None, *, signature: bytes | None = None) -> str:
    payload = json.dumps(claims if claims is not None else {"is_bad_bot": "false", "sub": "u1"}).encode()
    blob = payload + (signature if signature is not None else b"\x00" * 64)
    return "v4.public." + base64.urlsafe_b64encode(blob).decode().rstrip("=")


# --- fakes -------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body: Any = b"", *, status: int = 200, headers: Any = ()) -> None:
        self.status = status
        self.body = body if isinstance(body, (bytes, str)) else json.dumps(body).encode("utf-8")
        self.headers = list(headers)
        self.elapsed_ms = 0.0
        self.final_url = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace") if isinstance(self.body, bytes) else self.body

    def json(self) -> Any:
        return json.loads(self.body)


class FakeHTTP:
    def __init__(self, *posts: FakeResponse, gets: list[FakeResponse] | None = None) -> None:
        self.posts = list(posts)
        self.gets = list(gets or [])
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        if not self.posts:
            raise AssertionError(f"unexpected POST {url}")
        return self.posts.pop(0)

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        if not self.gets:
            raise AssertionError(f"unexpected GET {url}")
        return self.gets.pop(0)


class FakeRuntime:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def exec_script(self, code: str, context_globals: dict[str, Any] | None = None, *, timeout_ms: int | None = None) -> Any:
        self.calls.append({"code": code, "globals": context_globals, "timeout_ms": timeout_ms})
        if not self.results:
            raise AssertionError("unexpected exec_script")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeSensors:
    def __init__(self, builder: Any) -> None:
        self.builder = builder

    def get(self, engine: Any) -> Any:
        return self.builder


class FakeSession:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.navigated: list[str] = []
        self.scripts: list[str] = []

    async def goto(self, url: str, **kwargs: Any) -> Any:
        self.navigated.append(url)
        return self

    async def evaluate(self, script: str) -> Any:
        self.scripts.append(script)
        return self.answer


class FakeProvider:
    def __init__(self, session: FakeSession) -> None:
        self.session_obj = session
        self.specs: list[Any] = []

    @asynccontextmanager
    async def session(self, spec: Any) -> Any:
        self.specs.append(spec)
        yield self.session_obj


def make_task(**payload: Any) -> Task:
    base: dict[str, Any] = {
        "device_id": DEVICE,
        "client_session_id": "session-1",
        "client_version": "version-1",
        "userAgent": UA,
        "websiteURL": PAGE,
        "challenge_js": "window.kpsdk = 1;",
    }
    base.update(payload)
    return Task(id=new_task_id(), type=CaptchaType.TWITCH_INTEGRITY, payload=base)


def make_ctx(http: Any, runtime: Any = None, **kwargs: Any) -> SolverContext:
    return SolverContext(
        http=http,
        js_runtime=runtime,
        sensors=FakeSensors(TwitchIntegrityBuilder()),
        **kwargs,
    )


def captured(**headers: str) -> str:
    return json.dumps({"error": None, "request": {"url": INTEGRITY_URL, "method": "POST", "headers": {**KPSDK_HEADERS, **headers}}, "warnings": []})


# --- happy path --------------------------------------------------------------


async def test_http_path_posts_merged_headers_and_returns_the_token() -> None:
    token = make_token()
    http = FakeHTTP(FakeResponse({"token": token, "expiration": EXPIRATION}))
    runtime = FakeRuntime(captured(**{"x-kpsdk-ct": "ct-live"}))

    result = await TwitchIntegritySolver().solve_http(make_task(), make_ctx(http, runtime))

    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == INTEGRITY_URL
    assert call["headers"]["x-kpsdk-ct"] == "ct-live"
    assert call["headers"]["x-kpsdk-cd"] == "cd-1"
    assert call["headers"]["x-device-id"] == DEVICE
    assert call["headers"]["client-id"] == "kimne78kx3ncx6brgo4mv6wki5h1ko"
    assert call["headers"]["client-session-id"] == "session-1"
    assert call["headers"]["client-version"] == "version-1"

    assert result.token == token
    assert result.solve_mode is SolveMode.HTTP
    assert result.extra == {"userAgent": UA, "deviceId": DEVICE, "expiration": EXPIRATION}
    assert result.latency_ms >= 0.0

    solution = build_solution(
        CaptchaType.TWITCH_INTEGRITY,
        {"token": result.token, "extra": result.extra},
        site_url=PAGE,
    )
    assert solution is not None
    assert solution["token"] == token
    assert solution["userAgent"] == UA


async def test_driver_timeout_is_passed_through() -> None:
    token = make_token()
    runtime = FakeRuntime(captured())
    http = FakeHTTP(FakeResponse({"token": token, "expiration": EXPIRATION}))
    task = make_task(js_timeout_ms=1234)

    await TwitchIntegritySolver().solve_http(task, make_ctx(http, runtime))

    assert runtime.calls[0]["timeout_ms"] == 1234


async def test_inlined_bootstrap_is_not_fetched() -> None:
    token = make_token()
    http = FakeHTTP(FakeResponse({"token": token, "expiration": EXPIRATION}))
    runtime = FakeRuntime(captured())

    await TwitchIntegritySolver().solve_http(make_task(), make_ctx(http, runtime))

    assert [call["method"] for call in http.calls] == ["POST"]
    assert "window.kpsdk = 1;" in runtime.calls[0]["code"]


async def test_bootstrap_is_fetched_when_the_client_did_not_inline_it() -> None:
    token = make_token()
    http = FakeHTTP(
        FakeResponse(body=b'{"token": "%s", "expiration": %d}' % (token.encode(), EXPIRATION)),
        gets=[FakeResponse(b"window.kpsdk = 2;")],
    )
    runtime = FakeRuntime(captured())

    await TwitchIntegritySolver().solve_http(make_task(challenge_js=None), make_ctx(http, runtime))

    assert http.calls[0]["method"] == "GET"
    assert http.calls[0]["url"].startswith("https://k.twitchcdn.net/")
    assert "window.kpsdk = 2;" in runtime.calls[0]["code"]


async def test_precomputed_token_short_circuits_the_network() -> None:
    token = make_token()
    http = FakeHTTP()
    result = await TwitchIntegritySolver().solve_http(
        make_task(client_integrity=token),
        make_ctx(http),
    )
    assert http.calls == []
    assert result.token == token


# --- error mapping -----------------------------------------------------------


async def test_401_is_a_sensor_error() -> None:
    http = FakeHTTP(FakeResponse("nope", status=401))
    with pytest.raises(SensorError) as exc:
        await TwitchIntegritySolver().solve_http(make_task(), make_ctx(http, FakeRuntime(captured())))
    assert exc.value.context["status"] == 401


async def test_410_is_an_expired_challenge() -> None:
    http = FakeHTTP(FakeResponse("gone", status=410))
    with pytest.raises(ChallengeExpiredError):
        await TwitchIntegritySolver().solve_http(make_task(), make_ctx(http, FakeRuntime(captured())))


async def test_500_is_a_sensor_error() -> None:
    http = FakeHTTP(FakeResponse("boom", status=503))
    with pytest.raises(SensorError):
        await TwitchIntegritySolver().solve_http(make_task(), make_ctx(http, FakeRuntime(captured())))


async def test_empty_mint_is_an_expired_challenge() -> None:
    http = FakeHTTP(FakeResponse({}))
    with pytest.raises(ChallengeExpiredError):
        await TwitchIntegritySolver().solve_http(make_task(), make_ctx(http, FakeRuntime(captured())))


async def test_driver_error_is_a_sensor_error() -> None:
    http = FakeHTTP()
    runtime = FakeRuntime(json.dumps({"error": "configure:boom", "request": None}))
    with pytest.raises(SensorError) as exc:
        await TwitchIntegritySolver().solve_http(make_task(), make_ctx(http, runtime))
    assert "KPSDK" in exc.value.message


async def test_flagged_token_is_refused() -> None:
    token = make_token({"is_bad_bot": "true"})
    http = FakeHTTP(FakeResponse({"token": token, "expiration": EXPIRATION}))
    with pytest.raises(SensorError) as exc:
        await TwitchIntegritySolver().solve_http(make_task(), make_ctx(http, FakeRuntime(captured())))
    assert exc.value.message.endswith("bot traffic")


async def test_missing_device_id_is_a_sensor_error() -> None:
    http = FakeHTTP()
    with pytest.raises(SensorError):
        await TwitchIntegritySolver().solve_http(make_task(device_id=None), make_ctx(http, FakeRuntime(captured())))


async def test_missing_runtime_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        await TwitchIntegritySolver().solve_http(make_task(), make_ctx(FakeHTTP()))


async def test_anonymous_bootstrap_answer_escalates_to_the_browser() -> None:
    http = FakeHTTP(gets=[FakeResponse(b"")])
    with pytest.raises(NeedsBrowserError) as exc:
        await TwitchIntegritySolver().solve_http(make_task(challenge_js=None), make_ctx(http, FakeRuntime()))
    assert exc.value.code == "ERR_NEEDS_BROWSER"


# --- browser lane ------------------------------------------------------------


async def test_browser_lane_mints_from_the_loaded_page() -> None:
    token = make_token()
    session = FakeSession(json.dumps({"token": token, "expiration": EXPIRATION}))
    provider = FakeProvider(session)

    result = await TwitchIntegritySolver().solve_browser(make_task(), SolverContext(browser=provider))

    assert session.navigated == [PAGE]
    assert "credentials" in session.scripts[0]
    assert token in session.scripts[0] or INTEGRITY_URL in session.scripts[0]
    assert result.token == token
    assert result.solve_mode is SolveMode.BROWSER
    assert result.extra["expiration"] == EXPIRATION
    assert provider.specs[0].proxy is None


async def test_browser_lane_without_provider_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        await TwitchIntegritySolver().solve_browser(make_task(), SolverContext())


async def test_browser_lane_without_a_token_is_an_expired_challenge() -> None:
    provider = FakeProvider(FakeSession(json.dumps({})))
    with pytest.raises(ChallengeExpiredError):
        await TwitchIntegritySolver().solve_browser(make_task(), SolverContext(browser=provider))


# --- wire helpers ------------------------------------------------------------


def test_decode_claims_reads_the_payload_behind_the_signature() -> None:
    token = make_token({"is_bad_bot": "false", "nested": {"k": 1}})
    assert wire.decode_claims(token) == {"is_bad_bot": "false", "nested": {"k": 1}}


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"is_bad_bot": "false"}, False),
        ({"is_bad_bot": "FALSE"}, False),
        ({"is_bad_bot": False}, False),
        ({"is_bad_bot": "true"}, True),
        ({"is_bad_bot": "1"}, True),
        ({"is_bad_bot": True}, True),
        ({}, False),
    ],
)
def test_flagged_understands_the_claim_shapes(claims: dict[str, Any], expected: bool) -> None:
    assert wire.flagged(claims) is expected


def test_bad_envelopes_are_rejected() -> None:
    with pytest.raises(SensorError):
        wire.decode_claims("not-a-token")
    with pytest.raises(SensorError):
        wire.decode_claims("v4.public." + base64.urlsafe_b64encode(b"short").decode())
    with pytest.raises(SensorError):
        wire.decode_claims("v4.public." + base64.urlsafe_b64encode(b"x" * 80).decode())


def test_capture_headers_accepts_a_dict_and_refuses_driver_errors() -> None:
    assert wire.capture_headers({"request": {"headers": {"x-kpsdk-ct": "1"}}}) == {"x-kpsdk-ct": "1"}
    assert wire.capture_headers({"headers": {"X-Kpsdk-CT": "1"}}) == {"x-kpsdk-ct": "1"}
    assert wire.capture_headers({}) == {}
    with pytest.raises(SensorError):
        wire.capture_headers({"error": "boom"})


def test_merge_headers_prefers_the_captured_values() -> None:
    merged = wire.merge_headers({"x-kpsdk-ct": "base", "client-id": "c"}, {"x-kpsdk-ct": "captured"})
    assert merged == {"x-kpsdk-ct": "captured", "client-id": "c"}


def test_solver_info_declares_both_lanes() -> None:
    info = TwitchIntegritySolver.info
    assert info.types == (CaptchaType.TWITCH_INTEGRITY,)
    assert info.http is True
    assert info.browser is True
    assert info.needs_js_runtime is True
