"""Akamai SBSD: JSON challenge flow, graded by the cookie and nothing else.

Same rule as the classic solver: 200 is a request, not an answer. The body comes
from payload mode (the client ran the page script) or from the builder's section
model; both are only accepted once _abck settles.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from captcha.adapters.jssandbox.miniracer import LocalRuntime
from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ChallengeExpiredError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.domain.task import new_task_id
from captcha.engines.registry import global_registry
from captcha.solvers import SolverContext
from captcha.solvers.antibot.akamai.sbsd import AkamaiSBSDSolver

SITE = "https://site.test/login"
SBSD_URL = "https://site.test/sec/cp_challenge/sbsd/abc"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"
SATISFIED = "A1B2C3~0~deadbeef~0~"
UNSATISFIED = "A1B2C3~0~deadbeef~-1~"
BODY = {"version": "3", "sensor_data": "client-blob", "ts": 1700000000000}


def _response(status=200, body="", headers=None) -> object:
    class FakeResponse:
        def __init__(self) -> None:
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
            import json

            return json.loads(self.body or "{}")

    return FakeResponse()


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
    return Task(
        id=new_task_id(),
        type=CaptchaType.AKAMAI_SBSD,
        payload={"sbsd_body": BODY} if payload is None else payload,
        site_url=SITE,
        user_agent=UA,
    )


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


async def test_client_body_is_posted_to_the_client_endpoint(runtime):
    http = FakeHTTP(
        {
            SITE: _response(200, headers=[("Set-Cookie", "_abck=" + UNSATISFIED)]),
            SBSD_URL: _response(200, headers=[("Set-Cookie", "_abck=" + SATISFIED + "; Path=/")]),
        }
    )

    result = await AkamaiSBSDSolver().solve_http(task({"sbsd_body": BODY, "sbsd_url": SBSD_URL}), ctx(http, runtime))

    url, kwargs = http.posts()[0]
    assert url == SBSD_URL
    assert kwargs["json"] == BODY
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert result.token == SATISFIED


async def test_sbsd_cookie_is_carried_into_the_answer(runtime):
    http = FakeHTTP(
        {
            SITE: _response(200),
            SBSD_URL: _response(
                200,
                headers=[
                    ("Set-Cookie", "_abck=" + SATISFIED + "; Path=/"),
                    ("Set-Cookie", "sbsd=token-1; Path=/"),
                ],
            ),
        }
    )

    result = await AkamaiSBSDSolver().solve_http(task({"sbsd_body": BODY, "sbsd_url": SBSD_URL}), ctx(http, runtime))

    assert result.extra["cookies"]["sbsd"] == "token-1"
    assert result.extra["userAgent"] == UA


async def test_an_unsettled_cookie_is_not_a_solution(runtime):
    http = FakeHTTP({SITE: _response(200), SBSD_URL: _response(200)})

    with pytest.raises(ChallengeExpiredError) as exc:
        await AkamaiSBSDSolver().solve_http(task({"sbsd_body": BODY, "sbsd_url": SBSD_URL}), ctx(http, runtime))

    assert exc.value.context["state"] == "unknown"
    assert exc.value.context["status"] == 200
    assert exc.value.context["sbsd_cookie"] is False


async def test_428_reports_the_sec_cpt_layer(runtime):
    http = FakeHTTP({SITE: _response(200), SBSD_URL: _response(428)})

    with pytest.raises(ChallengeExpiredError) as exc:
        await AkamaiSBSDSolver().solve_http(task({"sbsd_body": BODY, "sbsd_url": SBSD_URL}), ctx(http, runtime))

    assert "sec-cpt" in exc.value.message


async def test_an_already_satisfied_bootstrap_is_returned(runtime):
    http = FakeHTTP({SITE: _response(200, headers=[("Set-Cookie", "_abck=" + SATISFIED)])})

    result = await AkamaiSBSDSolver().solve_http(task({"sbsd_body": BODY}), ctx(http, runtime))

    assert result.token == SATISFIED
    assert http.posts() == []


async def test_the_path_is_discovered_in_the_page(runtime):
    html = '<script src="/sec/cp_challenge/sbsd/v2"></script>'
    http = FakeHTTP(
        {
            SITE: _response(200, body=html, headers=[("Set-Cookie", "_abck=" + UNSATISFIED)]),
            "https://site.test/sec/cp_challenge/sbsd": _response(200, headers=[("Set-Cookie", "_abck=" + SATISFIED)]),
        }
    )

    result = await AkamaiSBSDSolver().solve_http(task({"sbsd_body": BODY}), ctx(http, runtime))

    assert http.posts()[0][0] == "https://site.test/sec/cp_challenge/sbsd/v2"
    assert result.token == SATISFIED


async def test_the_documented_path_is_the_last_resort(runtime):
    http = FakeHTTP(
        {
            SITE: _response(200, body="<html></html>", headers=[("Set-Cookie", "_abck=" + UNSATISFIED)]),
            "https://site.test/sec/cp_challenge/sbsd": _response(200, headers=[("Set-Cookie", "_abck=" + SATISFIED)]),
        }
    )

    await AkamaiSBSDSolver().solve_http(task({"sbsd_body": BODY}), ctx(http, runtime))

    assert http.posts()[0][0] == "https://site.test/sec/cp_challenge/sbsd"


async def test_an_unreadable_page_without_an_endpoint_is_unsolvable(runtime):
    http = FakeHTTP({SITE: ChallengeExpiredError("antibot verdict: HTTP 429")})

    with pytest.raises(UnsolvableError):
        await AkamaiSBSDSolver().solve_http(task({"sbsd_body": BODY}), ctx(http, runtime))


async def test_the_model_body_is_used_when_the_client_sent_none(runtime):
    http = FakeHTTP(
        {
            SITE: _response(200),
            SBSD_URL: _response(200, headers=[("Set-Cookie", "_abck=" + SATISFIED)]),
        }
    )

    await AkamaiSBSDSolver().solve_http(task({"sbsd_url": SBSD_URL}), ctx(http, runtime))

    body = http.posts()[0][1]["json"]
    assert body["sensor_data"].startswith("device=ua=")


async def test_the_solution_shape_is_a_cookie_answer(runtime):
    http = FakeHTTP(
        {
            SITE: _response(200),
            SBSD_URL: _response(
                200,
                headers=[
                    ("Set-Cookie", "_abck=" + SATISFIED + "; Path=/"),
                    ("Set-Cookie", "sbsd=t; Path=/"),
                ],
            ),
        }
    )

    result = await AkamaiSBSDSolver().solve_http(task({"sbsd_body": BODY, "sbsd_url": SBSD_URL}), ctx(http, runtime))
    solution = build_solution(
        CaptchaType.AKAMAI_SBSD,
        {"token": result.token, "extra": result.extra},
        site_url=SITE,
    )

    assert solution["domains"]["site.test"]["cookies"] == {"_abck": SATISFIED, "sbsd": "t"}
    assert solution["userAgent"] == UA
