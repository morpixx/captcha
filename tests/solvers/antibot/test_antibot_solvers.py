"""Antibot solver integration: the lane each engine takes and the shape it returns.

Five HTTP-first solvers were written on top of :mod:`captcha.engines.capture` and the
lane helpers in `solvers.antibot._runtime`. This module pins the contracts a worker
depends on, with every collaborator faked:

* **classification** -- no site key / no origin -> `UnsolvableError`; no script and
  no runtime -> `NeedsBrowserError` (one lane hop, not a terminal config error);
  a round that produced nothing -> `ChallengeExpiredError`; a rejected sensor POST
  -> `SensorError`;
* **answer shape** -- a token for Castle/CyberSiARA/Shape, a cookie jar for
  Imperva/PerimeterX, and the UA that minted it in `extra`;
* **both lanes** -- the HTTP path and the browser path of each solver, driven
  through fakes.

Nothing here touches a live site.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import pytest

from captcha.crypto import xor_cipher
from captcha.domain import SolveMode, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import (
    ChallengeExpiredError,
    NeedsBrowserError,
    SensorError,
    UnsolvableError,
)
from captcha.domain.task import new_task_id
from captcha.engines import get_builder, global_registry
from captcha.engines.perimeterx import codec as px_codec
from captcha.solvers import SolverContext
from captcha.solvers.antibot.castle.solver import CastleSolver
from captcha.solvers.antibot.cybersiara.solver import CyberSiARASolver
from captcha.solvers.antibot.imperva.solver import ImpervaSolver
from captcha.solvers.antibot.perimeterx.solver import PerimeterXSolver
from captcha.solvers.antibot.tspd.solver import TSPDSolver

PAGE_URL = "https://acme.example/login"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"
TOKEN = "tok_" + "a" * 24
CYBERSIARA_TOKEN = "mfp_" + "b" * 20
SHAPE_COOKIE = "TS01abcdef"


# --- fakes -------------------------------------------------------------------


class FakeResponse:
    """Duck-typed stand-in for captcha.adapters.transport.Response."""

    def __init__(self, *, status: int = 200, body: Any = b"", cookies: Any = ()) -> None:
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.headers = list(cookies)

    def headers_all(self, name: str) -> list[str]:
        return [value for value in self.headers] if name.lower() == "set-cookie" else []

    def header(self, name: str) -> str | None:
        hits = self.headers_all(name)
        return hits[-1] if hits else None


class FakeHTTP:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"kind": "post", "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected POST {url}")
        return self.responses.pop(0)

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"kind": "request", "method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected {method} {url}")
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


class FakePage:
    def __init__(self, report: dict[str, Any] | None = None) -> None:
        self.report = report or {"token": TOKEN, "cuid": "cuid-1", "error": None}
        self.scripts: list[Any] = []

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.scripts.append((script, arg))
        return self.report


class FakeSession:
    def __init__(self, jars: list[list[dict[str, Any]]] | None = None, page: FakePage | None = None) -> None:
        self.jars = jars or []
        self.page_obj = page or FakePage()
        self.navigated: list[str] = []

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 60_000) -> Any:
        self.navigated.append(url)
        return self

    async def cookies(self, url: str | None = None) -> list[dict[str, Any]]:
        return self.jars.pop(0) if self.jars else []

    async def page(self) -> FakePage:
        return self.page_obj


class FakeProvider:
    def __init__(self, session: FakeSession) -> None:
        self.session_obj = session
        self.specs: list[Any] = []

    def session(self, spec: Any) -> Any:
        self.specs.append(spec)

        @asynccontextmanager
        async def cm() -> Any:
            yield self.session_obj

        return cm()


class Fingerprint:
    user_agent = UA
    accept_language = "en-US,en;q=0.9"
    timezone = "Europe/Berlin"
    canvas_hash = "canvas-hash"
    webgl_vendor = "Google Inc."
    webgl_renderer = "ANGLE"
    audio_hash = "audio-hash"
    tags: ClassVar[dict[str, str]] = {"platform": "Win32"}


class FingerprintStore:
    def pick(self) -> Fingerprint:
        return Fingerprint()


def capture_json(
    *,
    cookies: dict[str, str] | None = None,
    storage: dict[str, str] | None = None,
    requests: list[dict[str, Any]] | None = None,
    errors: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "cookies": cookies or {},
            "storage": storage or {},
            "session": {},
            "requests": requests or [],
            "errors": errors or [],
        }
    )


def ob_blob(plaintext: str) -> str:
    import base64

    raw = xor_cipher(xor_cipher(plaintext.encode("latin-1"), b"50"), b"10")
    return base64.b64encode(raw).decode("ascii")


def make_task(captcha_type: CaptchaType, **payload: Any) -> Task:
    base: dict[str, Any] = {"websiteURL": PAGE_URL}
    base.update(payload)
    return Task(id=new_task_id(), type=captcha_type, payload=base, user_agent=None)


def http_ctx(*responses: FakeResponse, **kwargs: Any) -> SolverContext:
    return SolverContext(
        http=FakeHTTP(*responses),
        sensors=global_registry(),
        fingerprint_store=FingerprintStore(),
        **kwargs,
    )


# --- registration ------------------------------------------------------------


@pytest.mark.parametrize(
    ("solver", "engine"),
    [
        (CastleSolver, AntibotEngine.CASTLE),
        (CyberSiARASolver, AntibotEngine.CYBERSIARA),
        (ImpervaSolver, AntibotEngine.IMPERVA),
        (PerimeterXSolver, AntibotEngine.PERIMETERX),
        (TSPDSolver, AntibotEngine.TSPD),
    ],
)
def test_every_solver_declares_its_lanes(solver: Any, engine: AntibotEngine) -> None:
    info = solver.info
    assert info.http and info.browser and info.needs_js_runtime and info.experimental
    assert solver.engine is engine
    assert get_builder(engine).name == engine.value


# --- Castle ------------------------------------------------------------------


async def test_castle_returns_a_token_the_client_already_minted() -> None:
    task = make_task(CaptchaType.CASTLE, publishable_key="pk_live_1", castle_request_token=TOKEN)
    result = await CastleSolver().solve_http(task, SolverContext())
    assert result.token == TOKEN
    assert result.solve_mode is SolveMode.HTTP


async def test_castle_without_a_publishable_key_is_unsolvable() -> None:
    task = make_task(CaptchaType.CASTLE, castle_request_token=TOKEN)
    with pytest.raises(UnsolvableError):
        await CastleSolver().solve_http(task, SolverContext())


async def test_castle_without_the_sdk_source_escalates_to_the_browser_lane() -> None:
    task = make_task(CaptchaType.CASTLE, publishable_key="pk_live_1")
    with pytest.raises(NeedsBrowserError):
        await CastleSolver().solve_http(task, http_ctx(js_runtime=FakeRuntime()))


async def test_castle_reads_the_token_the_sdk_left_in_storage() -> None:
    runtime = FakeRuntime(capture_json(storage={"castle_request_token": TOKEN}))
    task = make_task(CaptchaType.CASTLE, publishable_key="pk_live_1", challenge_js="var castle = 1;")
    result = await CastleSolver().solve_http(task, http_ctx(js_runtime=runtime))
    assert result.token == TOKEN
    assert "castle_request_token" in runtime.calls[0]["code"]


async def test_castle_reports_no_token_as_needing_a_page() -> None:
    runtime = FakeRuntime(capture_json(errors=["castle: sdk not configured"]))
    task = make_task(CaptchaType.CASTLE, publishable_key="pk_live_1", challenge_js="var castle = 1;")
    with pytest.raises(NeedsBrowserError):
        await CastleSolver().solve_http(task, http_ctx(js_runtime=runtime))


async def test_castle_browser_lane_drives_the_sdk_and_returns_the_device_id() -> None:
    session = FakeSession(page=FakePage({"token": TOKEN, "cuid": "cuid-1", "error": None}))
    task = make_task(CaptchaType.CASTLE, publishable_key="pk_live_1")
    result = await CastleSolver().solve_browser(task, SolverContext(browser=FakeProvider(session)))
    assert session.navigated == [PAGE_URL]
    assert result.token == TOKEN
    assert result.extra["cuid"] == "cuid-1"
    assert result.solve_mode is SolveMode.BROWSER


async def test_castle_browser_lane_without_a_token_is_a_stale_challenge() -> None:
    session = FakeSession(page=FakePage({"token": "", "cuid": "", "error": "sdk-missing"}))
    task = make_task(CaptchaType.CASTLE, publishable_key="pk_live_1")
    with pytest.raises(ChallengeExpiredError):
        await CastleSolver().solve_browser(task, SolverContext(browser=FakeProvider(session)))


# --- CyberSiARA --------------------------------------------------------------


async def test_cybersiara_returns_a_supplied_master_url_token() -> None:
    task = make_task(CaptchaType.CYBERSIARA, sitekey="sk-1", master_url_token=CYBERSIARA_TOKEN)
    result = await CyberSiARASolver().solve_http(task, SolverContext())
    assert result.token == CYBERSIARA_TOKEN


async def test_cybersiara_without_a_site_key_is_unsolvable() -> None:
    task = make_task(CaptchaType.CYBERSIARA, master_url_token=CYBERSIARA_TOKEN)
    with pytest.raises(UnsolvableError):
        await CyberSiARASolver().solve_http(task, SolverContext())


async def test_cybersiara_without_the_widget_escalates_to_the_browser_lane() -> None:
    task = make_task(CaptchaType.CYBERSIARA, sitekey="sk-1")
    with pytest.raises(NeedsBrowserError):
        await CyberSiARASolver().solve_http(task, http_ctx(js_runtime=FakeRuntime()))


async def test_cybersiara_takes_the_token_the_widget_stored() -> None:
    runtime = FakeRuntime(capture_json(storage={"master_url_token": CYBERSIARA_TOKEN}))
    task = make_task(CaptchaType.CYBERSIARA, sitekey="sk-1", challenge_js="var widget = 1;")
    result = await CyberSiARASolver().solve_http(task, http_ctx(js_runtime=runtime))
    assert result.token == CYBERSIARA_TOKEN


async def test_cybersiara_replays_the_widget_verify_call_towards_the_site() -> None:
    runtime = FakeRuntime(capture_json(requests=[{"url": "/api/v1/verify/sk-1", "method": "POST", "body": "track=1"}]))
    http = FakeHTTP(FakeResponse(body={"MasterUrlToken": CYBERSIARA_TOKEN}))
    ctx = SolverContext(http=http, sensors=global_registry(), fingerprint_store=FingerprintStore(), js_runtime=runtime)
    task = make_task(CaptchaType.CYBERSIARA, sitekey="sk-1", challenge_js="var widget = 1;", origin=PAGE_URL)
    result = await CyberSiARASolver().solve_http(task, ctx)
    assert result.token == CYBERSIARA_TOKEN
    assert [call["url"] for call in http.calls] == ["https://acme.example/api/v1/verify/sk-1"]


async def test_cybersiara_refuses_to_replay_a_foreign_host() -> None:
    runtime = FakeRuntime(
        capture_json(
            requests=[{"url": "https://evil.example/collect", "method": "POST", "body": "x"}],
            errors=["token-missing"],
        )
    )
    http = FakeHTTP()
    ctx = SolverContext(http=http, sensors=global_registry(), fingerprint_store=FingerprintStore(), js_runtime=runtime)
    task = make_task(CaptchaType.CYBERSIARA, sitekey="sk-1", challenge_js="var widget = 1;")
    with pytest.raises(ChallengeExpiredError):
        await CyberSiARASolver().solve_http(task, ctx)
    assert http.calls == []


# --- Imperva -----------------------------------------------------------------


async def test_imperva_without_a_page_is_unsolvable() -> None:
    task = Task(id=new_task_id(), type=CaptchaType.IMPERVA, payload={})
    with pytest.raises(UnsolvableError):
        await ImpervaSolver().solve_http(task, SolverContext())


async def test_imperva_posts_the_envelope_and_returns_the_cookie_jar() -> None:
    http = FakeHTTP(FakeResponse(cookies=["reese84=new-token; Path=/", "incap_ses_8675309=abc; Path=/"]))
    ctx = SolverContext(http=http, sensors=global_registry(), fingerprint_store=FingerprintStore())
    task = make_task(CaptchaType.IMPERVA, origin="https://acme.example")
    result = await ImpervaSolver().solve_http(task, ctx)

    assert result.token == "new-token"
    assert result.extra["cookies"] == {"reese84": "new-token", "incap_ses_8675309": "abc"}
    assert result.extra["domain"] == "acme.example"
    call = http.calls[0]
    assert call["kind"] == "post"
    assert call["url"].startswith("https://acme.example/_Incapsula_Resource?SWKMTFSR=1&e=")
    assert set(call["json"]) == {"solution", "old_token", "error", "performance"}


async def test_imperva_rejected_envelope_is_a_sensor_error() -> None:
    http = FakeHTTP(FakeResponse(status=403))
    ctx = SolverContext(http=http, sensors=global_registry(), fingerprint_store=FingerprintStore())
    task = make_task(CaptchaType.IMPERVA, origin="https://acme.example")
    with pytest.raises(SensorError):
        await ImpervaSolver().solve_http(task, ctx)


async def test_imperva_script_path_reads_the_cookie_the_script_wrote() -> None:
    runtime = FakeRuntime(capture_json(cookies={"___utmvc": "utmvc-value"}))
    ctx = http_ctx(js_runtime=runtime)
    task = make_task(CaptchaType.IMPERVA, origin="https://acme.example", challenge_js="var reese = 1;")
    result = await ImpervaSolver().solve_http(task, ctx)
    assert result.token == "utmvc-value"
    assert result.extra["cookies"]["___utmvc"] == "utmvc-value"


async def test_imperva_browser_lane_returns_the_whole_cookie_family() -> None:
    session = FakeSession(
        [
            [{"name": "reese84", "value": "browser-token"}],
            [{"name": "reese84", "value": "browser-token"}, {"name": "incap_ses_1", "value": "abc"}],
        ]
    )
    task = make_task(CaptchaType.IMPERVA, websiteURL=PAGE_URL)
    result = await ImpervaSolver().solve_browser(task, SolverContext(browser=FakeProvider(session)))
    # the browser lane navigates the *origin*: the sensor POST lives there and the
    # jar is polled, not scraped. Documented in the solver docstring.
    assert session.navigated == ["https://acme.example"]
    assert result.token == "browser-token"
    assert result.extra["cookies"] == {"reese84": "browser-token", "incap_ses_1": "abc"}
    assert result.solve_mode is SolveMode.BROWSER


# --- PerimeterX --------------------------------------------------------------


async def test_perimeterx_without_an_app_id_is_unsolvable() -> None:
    task = make_task(CaptchaType.PERIMETERX)
    with pytest.raises(UnsolvableError):
        await PerimeterXSolver().solve_http(task, SolverContext())


async def test_perimeterx_collector_bakes_the_ob_cookies() -> None:
    blob = ob_blob("_pxhd|verdict-value~~~~pxcts|ts-1")
    http = FakeHTTP(FakeResponse(body={"ob": blob}))
    ctx = SolverContext(http=http, sensors=global_registry(), fingerprint_store=FingerprintStore())
    task = make_task(CaptchaType.PERIMETERX, app_id="PXabc123")
    result = await PerimeterXSolver().solve_http(task, ctx)

    assert result.token == "verdict-value"
    assert result.extra["cookies"] == {"_pxhd": "verdict-value", "pxcts": "ts-1"}
    call = http.calls[0]
    assert call["url"] == "https://collector-PXabc123.px-cloud.net/api/v2/collector"
    assert "application/x-www-form-urlencoded" in call["headers"]["Content-Type"]
    assert "pc=" in call["data"] and "payload=" in call["data"]


async def test_perimeterx_rejected_sensor_body_is_a_sensor_error() -> None:
    http = FakeHTTP(FakeResponse(status=403))
    ctx = SolverContext(http=http, sensors=global_registry(), fingerprint_store=FingerprintStore())
    task = make_task(CaptchaType.PERIMETERX, app_id="PXabc123")
    with pytest.raises(SensorError):
        await PerimeterXSolver().solve_http(task, ctx)


async def test_perimeterx_press_path_reads_the_bundle_cookie() -> None:
    runtime = FakeRuntime(capture_json(cookies={"_pxhd": "press-verdict"}))
    ctx = http_ctx(js_runtime=runtime)
    task = make_task(CaptchaType.PERIMETERX, app_id="PXabc123", challenge_js="var press = 1;")
    result = await PerimeterXSolver().solve_http(task, ctx)
    assert result.token == "press-verdict"


async def test_perimeterx_collector_override_is_respected() -> None:
    blob = ob_blob("_px|sensor-value")
    http = FakeHTTP(FakeResponse(body={"ob": blob}))
    ctx = SolverContext(http=http, sensors=global_registry(), fingerprint_store=FingerprintStore())
    task = make_task(
        CaptchaType.PERIMETERX, app_id="PXabc123", collector_url="https://collector.internal.test/api/v2/collector"
    )
    result = await PerimeterXSolver().solve_http(task, ctx)
    assert http.calls[0]["url"] == "https://collector.internal.test/api/v2/collector"
    assert result.token == "sensor-value"


async def test_perimeterx_browser_lane_prefers_the_verdict_cookie() -> None:
    # the wait ends on the first _px* cookie; the verdict must win over the visitor id
    one_jar = [{"name": "_pxvid", "value": "v1"}, {"name": "_pxhd", "value": "browser-verdict"}]
    session = FakeSession([one_jar, one_jar])
    task = make_task(CaptchaType.PERIMETERX, app_id="PXabc123")
    result = await PerimeterXSolver().solve_browser(task, SolverContext(browser=FakeProvider(session)))
    assert result.token == "browser-verdict"
    assert result.extra["cookies"] == {"_pxvid": "v1", "_pxhd": "browser-verdict"}
    assert result.solve_mode is SolveMode.BROWSER


def test_perimeterx_collector_envelope_has_the_codec_fields() -> None:
    ctx = SolverContext(sensors=global_registry(), fingerprint_store=FingerprintStore())
    builder, build_ctx = PerimeterXSolver().build_sensor(ctx, {"websiteURL": PAGE_URL, "app_id": "PXabc123"})
    assert builder.name == "perimeterx"
    assert build_ctx.site_url == PAGE_URL
    assert build_ctx.engine is AntibotEngine.PERIMETERX


# --- F5 Shape ----------------------------------------------------------------


async def test_tspd_without_the_shape_script_escalates_to_the_browser_lane() -> None:
    task = make_task(CaptchaType.TSPD)
    with pytest.raises(NeedsBrowserError):
        await TSPDSolver().solve_http(task, SolverContext())


async def test_tspd_without_a_page_is_unsolvable() -> None:
    task = Task(id=new_task_id(), type=CaptchaType.TSPD, payload={})
    with pytest.raises(UnsolvableError):
        await TSPDSolver().solve_http(task, SolverContext())


async def test_tspd_returns_every_ts_cookie_the_vm_wrote() -> None:
    runtime = FakeRuntime(capture_json(cookies={SHAPE_COOKIE: "shape-value", "unrelated": "x"}))
    task = make_task(CaptchaType.TSPD, challenge_js="var shape = 1;")
    result = await TSPDSolver().solve_http(task, http_ctx(js_runtime=runtime))
    assert result.token == "shape-value"
    assert result.extra["cookies"] == {SHAPE_COOKIE: "shape-value"}
    assert result.extra["userAgent"] is None


async def test_tspd_round_without_a_ts_cookie_is_a_stale_challenge() -> None:
    runtime = FakeRuntime(capture_json(cookies={"unrelated": "x"}, errors=["vm: no cookie"]))
    task = make_task(CaptchaType.TSPD, challenge_js="var shape = 1;")
    with pytest.raises(ChallengeExpiredError):
        await TSPDSolver().solve_http(task, http_ctx(js_runtime=runtime))


async def test_tspd_browser_lane_returns_every_ts_cookie() -> None:
    jar = [{"name": "TSPD_101", "value": "bigip-value"}, {"name": "TS01abc", "value": "asm-value"}]
    session = FakeSession([jar, jar])
    task = make_task(CaptchaType.TSPD)
    result = await TSPDSolver().solve_browser(task, SolverContext(browser=FakeProvider(session)))
    assert result.token == "bigip-value"
    assert result.extra["cookies"] == {"TSPD_101": "bigip-value", "TS01abc": "asm-value"}
    assert result.solve_mode is SolveMode.BROWSER


def test_perimeterx_payload_decodes_back_to_the_signals_sent() -> None:
    ctx = SolverContext(sensors=global_registry(), fingerprint_store=FingerprintStore())
    builder, build_ctx = PerimeterXSolver().build_sensor(
        ctx, {"websiteURL": PAGE_URL, "app_id": "PXabc123", "behavior": {"mouse": [{"x": 1}]}}
    )
    assert builder.name == "perimeterx"
    assert build_ctx.behavior == {"mouse": [{"x": 1}]}
    assert px_codec.decode_payload(px_codec.encode_payload({"PX101": UA})) == {"PX101": UA}
