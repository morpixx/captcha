"""Kasada /tl flow: what the server returns is the only verdict.

The SDK VM runs for real (synthetic p.js posting bytes) and the transport is
faked, so the assertions are about protocol discipline: reload:true plus
x-kpsdk-ct, the cookie/header shape clients replay, and a retryable error for
every way that pair is missing.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from captcha.adapters.jssandbox.miniracer import LocalRuntime
from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.domain.task import new_task_id
from captcha.engines.kasada import pow
from captcha.engines.registry import global_registry
from captcha.solvers import SolverContext
from captcha.solvers.antibot.kasada import wire
from captcha.solvers.antibot.kasada.endpoints import KPSDK_PATH_MFC, KPSDK_PATH_TL
from captcha.solvers.antibot.kasada.solver import KasadaSolver

SITE = "https://site.test/login"
BASE = "https://site.test"
TL_URL = BASE + KPSDK_PATH_TL
MFC_URL = BASE + KPSDK_PATH_MFC
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"
VERSION = "j-1.2.797"
PJS = (
    """
(function () {
  var payload = new Uint8Array([9, 8, 7, 6]);
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '"""
    + TL_URL
    + """');
  xhr.setRequestHeader('x-kpsdk-ct', 'pre-ct');
  xhr.setRequestHeader('x-kpsdk-dt', 'pre-dt');
  xhr.send(payload);
})();
"""
)


class FakeResponse:
    def __init__(self, status=200, body="", headers=None) -> None:
        self.status = status
        self.body = body.encode()
        self.headers = list(headers.items()) if isinstance(headers, dict) else list(headers or [])

    @property
    def text(self) -> str:
        return self.body.decode()

    def header(self, name):
        found = None
        for key, value in self.headers:
            if key.lower() == name.lower():
                found = value
        return found

    def headers_all(self, name):
        return [value for key, value in self.headers if key.lower() == name.lower()]

    def json(self):
        return json.loads(self.body or "{}")


class FakeHTTP:
    def __init__(self, routes: dict) -> None:
        self.routes = {key: (value if isinstance(value, list) else [value]) for key, value in routes.items()}
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, url: str, **kwargs):
        return self._pick("GET", url, kwargs)

    async def post(self, url: str, **kwargs):
        return self._pick("POST", url, kwargs)

    def _pick(self, method: str, url: str, kwargs: dict):
        self.calls.append((method, url, kwargs))
        for prefix, queue in self.routes.items():
            if url.startswith(prefix):
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(item, Exception):
                    raise item
                return item
        raise AssertionError(f"unexpected {method} {url}")

    def posts(self):
        return [(url, kwargs) for method, url, kwargs in self.calls if method == "POST"]


class FakeStore:
    def pick(self, filter=None):
        return SimpleNamespace(user_agent=UA)


def task(payload: dict | None = None) -> Task:
    base = {"script": PJS, "kpsdk_v": VERSION}
    base.update(payload or {})
    return Task(id=new_task_id(), type=CaptchaType.KASADA, payload=base, site_url=SITE, user_agent=UA)


def ctx(http: FakeHTTP, runtime) -> SolverContext:
    return SolverContext(
        http=http,
        js_runtime=runtime,
        sensors=global_registry(),
        fingerprint_store=FakeStore(),
    )


@pytest.fixture
async def runtime():
    instance = LocalRuntime()
    try:
        yield instance
    finally:
        await instance.close()


def tl_ok(**headers) -> FakeResponse:
    payload = {"x-kpsdk-ct": "ct-1", "x-kpsdk-st": "1700000000", **headers}
    return FakeResponse(
        200,
        body='{"reload": true}',
        headers=[("Set-Cookie", "KP_UIDz=uid-1; Path=/"), *[(key, value) for key, value in payload.items()]],
    )


# ---- happy path -------------------------------------------------------------


async def test_a_tl_handshake_returns_the_cookie_and_header_shape(runtime):
    http = FakeHTTP(
        {
            SITE: FakeResponse(200, body="<html></html>"),
            TL_URL: tl_ok(),
            MFC_URL: FakeResponse(200, headers={"x-kpsdk-fc": "fc-1", "x-kpsdk-h": "h-1"}),
        }
    )

    result = await KasadaSolver().solve_http(task(), ctx(http, runtime))

    assert result.token == "ct-1"
    extra = result.extra
    assert extra["cookies"] == {"KP_UIDz": "uid-1"}
    assert extra["userAgent"] == UA
    assert extra["x-kpsdk-ct"] == "ct-1"
    assert extra["x-kpsdk-st"] == "1700000000"
    assert extra["x-kpsdk-fc"] == "fc-1"
    assert extra["x-kpsdk-h"] == "h-1"
    assert extra["x-kpsdk-v"] == VERSION
    assert extra["headers"] == {
        "x-kpsdk-ct": "ct-1",
        "x-kpsdk-st": "1700000000",
        "x-kpsdk-fc": "fc-1",
        "x-kpsdk-h": "h-1",
        "x-kpsdk-v": VERSION,
    }


async def test_the_tl_post_carries_the_vm_bytes_and_headers(runtime):
    http = FakeHTTP({SITE: FakeResponse(200), TL_URL: tl_ok(), MFC_URL: FakeResponse(200)})

    await KasadaSolver().solve_http(task(), ctx(http, runtime))

    url, kwargs = http.posts()[0]
    assert url == TL_URL
    assert kwargs["data"] == b"\x09\x08\x07\x06"
    assert kwargs["headers"]["Content-Type"] == "application/octet-stream"
    assert kwargs["headers"]["x-kpsdk-ct"] == "pre-ct"
    assert kwargs["headers"]["x-kpsdk-dt"] == "pre-dt"
    assert kwargs["headers"]["Origin"] == BASE


async def test_the_solution_shape_is_a_cookie_answer(runtime):
    http = FakeHTTP({SITE: FakeResponse(200), TL_URL: tl_ok(), MFC_URL: FakeResponse(200)})

    result = await KasadaSolver().solve_http(task(), ctx(http, runtime))
    solution = build_solution(CaptchaType.KASADA, {"token": result.token, "extra": result.extra}, site_url=SITE)

    assert solution["cookies"] == {"KP_UIDz": "uid-1"}
    assert solution["domains"] == {"site.test": {"cookies": {"KP_UIDz": "uid-1"}}}
    assert solution["userAgent"] == UA
    assert solution["x-kpsdk-ct"] == "ct-1"


async def test_cd_is_forwarded_only_when_the_payload_brought_answers(runtime):
    http = FakeHTTP({SITE: FakeResponse(200), TL_URL: tl_ok(), MFC_URL: FakeResponse(200)})
    payload = {
        pow.EXTRA_ST: "1700000000",
        pow.EXTRA_ANSWERS: [4, 5],
        pow.EXTRA_WORK_TIME: "7",
    }

    result = await KasadaSolver().solve_http(task(payload), ctx(http, runtime))

    decoded = json.loads(base64.b64decode(result.extra["x-kpsdk-cd"]))
    assert decoded["answers"] == [4, 5]
    assert decoded["st"] == 1700000000


async def test_no_answers_means_no_cd_header(runtime):
    http = FakeHTTP({SITE: FakeResponse(200), TL_URL: tl_ok(), MFC_URL: FakeResponse(200)})

    result = await KasadaSolver().solve_http(task(), ctx(http, runtime))

    assert "x-kpsdk-cd" not in result.extra


async def test_a_failing_mfc_degrades_but_does_not_fail(runtime):
    http = FakeHTTP(
        {
            SITE: FakeResponse(200),
            TL_URL: tl_ok(),
            MFC_URL: ChallengeExpiredError("antibot verdict: HTTP 403"),
        }
    )

    result = await KasadaSolver().solve_http(task(), ctx(http, runtime))

    assert result.token == "ct-1"
    assert result.extra["mfc"] == "ERR_CHALLENGE_EXPIRED"
    assert "x-kpsdk-h" not in result.extra


# ---- failure mapping --------------------------------------------------------


async def test_a_200_without_reload_is_retryable(runtime):
    http = FakeHTTP({SITE: FakeResponse(200), TL_URL: FakeResponse(200, body='{"reload": false}')})

    with pytest.raises(ChallengeExpiredError) as exc:
        await KasadaSolver().solve_http(task(), ctx(http, runtime))

    assert "reload" in exc.value.message


async def test_a_200_without_ct_is_retryable(runtime):
    http = FakeHTTP({SITE: FakeResponse(200), TL_URL: FakeResponse(200, body='{"reload": true}')})

    with pytest.raises(ChallengeExpiredError) as exc:
        await KasadaSolver().solve_http(task(), ctx(http, runtime))

    assert "x-kpsdk-ct" in exc.value.message


async def test_a_429_from_tl_is_retryable(runtime):
    http = FakeHTTP({SITE: FakeResponse(200), TL_URL: FakeResponse(429)})

    with pytest.raises(ChallengeExpiredError) as exc:
        await KasadaSolver().solve_http(task(), ctx(http, runtime))

    assert exc.value.context["status"] == 429


async def test_a_non_200_from_tl_is_retryable(runtime):
    http = FakeHTTP({SITE: FakeResponse(200), TL_URL: FakeResponse(500)})

    with pytest.raises(ChallengeExpiredError) as exc:
        await KasadaSolver().solve_http(task(), ctx(http, runtime))

    assert exc.value.context["status"] == 500


async def test_a_transport_verdict_on_tl_is_retryable(runtime):
    http = FakeHTTP({SITE: FakeResponse(200), TL_URL: ChallengeExpiredError("antibot verdict: HTTP 403")})

    with pytest.raises(ChallengeExpiredError) as exc:
        await KasadaSolver().solve_http(task(), ctx(http, runtime))

    assert "transport" in exc.value.message


async def test_a_missing_script_is_unsolvable(runtime):
    http = FakeHTTP(
        {
            SITE: ChallengeExpiredError("antibot verdict: HTTP 429"),
            BASE + "/149e9513-01fa-4fb0-aad4-566afd725d1b": FakeResponse(404),
        }
    )

    with pytest.raises(UnsolvableError):
        await KasadaSolver().solve_http(task({"script": ""}), ctx(http, runtime))


async def test_a_missing_runtime_is_a_config_error():
    http = FakeHTTP({SITE: FakeResponse(200)})

    with pytest.raises(ConfigError):
        await KasadaSolver().solve_http(task(), ctx(http, None))


# ---- discovery helpers ------------------------------------------------------


def test_script_path_is_found_in_a_challenge_page():
    html = '<script src="/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/ips.js?timestamp=1"></script>'

    assert wire.script_path(html).endswith("/ips.js?timestamp=1")
    assert (
        wire.script_url(html, SITE)
        == BASE + "/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/ips.js?timestamp=1"
    )


def test_a_cross_host_script_is_not_accepted():
    html = '<script src="https://evil.test/149e9513-01fa-4fb0-aad4-566afd725d1b/x/ips.js"></script>'

    assert wire.script_url(html, SITE) is None


def test_version_hint_is_optional():
    assert wire.version_hint("var v='j-1.2.797';") == "j-1.2.797"
    assert wire.version_hint("nothing here") is None
