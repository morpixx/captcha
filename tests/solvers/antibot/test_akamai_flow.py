"""Akamai classic sensor flow: the _abck tail decides, not the status code.

No network. The tenant script is real V8 running a synthetic bmak that posts one
sensor_data body, so the flow is exercised end to end: bootstrap, script, build,
POST, grade. The failure cases are the point -- an intermediate cookie, a 428 and
a transport verdict must all surface as retryable errors, never as a solution.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from captcha.adapters.jssandbox.miniracer import LocalRuntime
from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, ProxyError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.domain.task import new_task_id
from captcha.engines.registry import global_registry
from captcha.solvers import SolverContext
from captcha.solvers.antibot.akamai import abck
from captcha.solvers.antibot.akamai.solver import AkamaiSensorSolver

SITE = "https://site.test/login"
AKAM_URL = "https://site.test/akam/13/abcdef"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"

SATISFIED = "A1B2C3~0~deadbeef~0~"
INTERMEDIATE = "A1B2C3~0~deadbeef~0~-1~"
UNSATISFIED = "A1B2C3~0~deadbeef~-1~"

BMAK_JS = (
    """
(function () {
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '"""
    + AKAM_URL
    + """');
  xhr.send(JSON.stringify({sensor_data: 'SENSOR-BLOB'}));
})();
"""
)


# ---- fakes ------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int = 200, body: str = "", headers=None) -> None:
        self.status = status
        self.body = body.encode()
        self.headers = list(headers.items()) if isinstance(headers, dict) else list(headers or [])
        self.final_url = ""

    @property
    def text(self) -> str:
        return self.body.decode()

    def json(self):
        import json

        return json.loads(self.body or "{}")

    def header(self, name: str):
        found = None
        for key, value in self.headers:
            if key.lower() == name.lower():
                found = value
        return found

    def headers_all(self, name: str) -> list[str]:
        return [value for key, value in self.headers if key.lower() == name.lower()]


class FakeHTTP:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = {key: (value if isinstance(value, list) else [value]) for key, value in routes.items()}
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, url: str, **kwargs):
        return self._pick("GET", url, kwargs)

    async def post(self, url: str, **kwargs):
        return self._pick("POST", url, kwargs)

    def _pick(self, method: str, url: str, kwargs: dict):
        self.calls.append((method, url, kwargs))
        for prefix, queue in self.routes.items():
            if not url.startswith(prefix):
                continue
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return item
        raise AssertionError(f"unexpected {method} {url}")

    def posts(self) -> list[tuple[str, dict]]:
        return [(url, kwargs) for method, url, kwargs in self.calls if method == "POST"]


class FakeStore:
    def __init__(self, user_agent: str = UA) -> None:
        self.user_agent = user_agent

    def pick(self, filter=None):
        return SimpleNamespace(user_agent=self.user_agent)


def task(payload: dict | None = None, *, user_agent: str | None = UA) -> Task:
    return Task(
        id=new_task_id(),
        type=CaptchaType.AKAMAI_SENSOR,
        payload=payload if payload is not None else {"sensor_js": BMAK_JS},
        site_url=SITE,
        user_agent=user_agent,
    )


def ctx(http: FakeHTTP, runtime, *, store=None) -> SolverContext:
    return SolverContext(
        http=http,
        js_runtime=runtime,
        sensors=global_registry(),
        fingerprint_store=store or FakeStore(),
    )


@pytest.fixture
async def runtime():
    instance = LocalRuntime()
    try:
        yield instance
    finally:
        await instance.close()


def page(*cookies: tuple[str, str], status: int = 200, html: str = "") -> FakeResponse:
    headers = [("Set-Cookie", f"{name}={value}; Path=/") for name, value in cookies]
    return FakeResponse(status=status, body=html, headers=headers)


def tl(*cookies: tuple[str, str], status: int = 200, body: str = "") -> FakeResponse:
    headers = [("Set-Cookie", f"{name}={value}; Path=/") for name, value in cookies]
    return FakeResponse(status=status, body=body, headers=headers)


# ---- abck state machine -----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "state"),
    [
        (SATISFIED, abck.SATISFIED),
        (INTERMEDIATE, abck.INTERMEDIATE),
        (UNSATISFIED, abck.UNSATISFIED),
        ("opaque-without-markers", abck.UNKNOWN),
        ("", abck.UNKNOWN),
        (None, abck.UNKNOWN),
    ],
)
def test_abck_states(value, state):
    assert abck.read(value).state == state


def test_only_the_satisfied_tail_counts():
    assert abck.read(SATISFIED).satisfied is True
    assert abck.read(INTERMEDIATE).satisfied is False
    assert abck.read(UNSATISFIED).satisfied is False


def test_state_summary_never_carries_the_value():
    summary = abck.read(SATISFIED).summary()

    assert SATISFIED not in str(summary)
    assert summary["state"] == abck.SATISFIED
    assert summary["length"] == len(SATISFIED)


def test_sec_cpt_marker():
    assert abck.sec_cpt_cleared("x~3~y") is True
    assert abck.sec_cpt_cleared("x~0~y") is False
    assert abck.sec_cpt_cleared(None) is False


# ---- happy paths ------------------------------------------------------------


async def test_a_satisfied_bootstrap_is_returned_without_a_post(runtime):
    http = FakeHTTP({SITE: page(("_abck", SATISFIED), ("bm_sz", "seed-1"))})

    result = await AkamaiSensorSolver().solve_http(task(), ctx(http, runtime))

    assert result.token == SATISFIED
    assert result.extra == {"cookies": {"_abck": SATISFIED, "bm_sz": "seed-1"}, "userAgent": UA}
    assert http.posts() == []


async def test_one_post_that_settles_the_cookie_solves(runtime):
    http = FakeHTTP(
        {
            SITE: page(("_abck", UNSATISFIED), ("bm_sz", "seed-1")),
            AKAM_URL: tl(("_abck", SATISFIED)),
        }
    )

    result = await AkamaiSensorSolver().solve_http(task(), ctx(http, runtime))

    assert result.token == SATISFIED
    url, kwargs = http.posts()[0]
    assert url == AKAM_URL
    assert kwargs["json"] == {"sensor_data": "SENSOR-BLOB"}
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["cookies"]["bm_sz"] == "seed-1"


async def test_intermediate_answer_triggers_another_post(runtime):
    http = FakeHTTP(
        {
            SITE: page(("_abck", UNSATISFIED)),
            AKAM_URL: [tl(("_abck", INTERMEDIATE)), tl(("_abck", SATISFIED))],
        }
    )

    result = await AkamaiSensorSolver().solve_http(task(), ctx(http, runtime))

    assert result.token == SATISFIED
    assert len(http.posts()) == 2


async def test_a_2x_tenant_gets_the_form_body(runtime):
    http = FakeHTTP({SITE: page(("_abck", UNSATISFIED)), AKAM_URL: tl(("_abck", SATISFIED))})

    await AkamaiSensorSolver().solve_http(task({"sensor_js": BMAK_JS, "abck_version": 2}), ctx(http, runtime))

    _, kwargs = http.posts()[0]
    assert kwargs["data"] == b"sensor_data=SENSOR-BLOB"
    assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


async def test_model_encoder_solves_without_a_script(runtime):
    http = FakeHTTP({SITE: page(("_abck", UNSATISFIED), ("bm_sz", "s")), AKAM_URL: tl(("_abck", SATISFIED))})

    await AkamaiSensorSolver().solve_http(task({"sensor_encoder": "model", "akam_url": AKAM_URL}), ctx(http, runtime))

    _, kwargs = http.posts()[0]
    assert kwargs["json"]["sensor_data"]


async def test_script_url_is_fetched_and_used(runtime):
    http = FakeHTTP(
        {
            SITE: page(("_abck", UNSATISFIED)),
            "https://site.test/akam/13/script": FakeResponse(200, BMAK_JS),
            AKAM_URL: tl(("_abck", SATISFIED)),
        }
    )

    await AkamaiSensorSolver().solve_http(
        task({"sensor_js_url": "https://site.test/akam/13/script"}), ctx(http, runtime)
    )

    assert any("script" in url for method, url, _ in http.calls if method == "GET")


async def test_the_solution_is_a_cookie_answer(runtime):
    http = FakeHTTP({SITE: page(("_abck", SATISFIED), ("bm_sz", "seed-1"))})

    result = await AkamaiSensorSolver().solve_http(task(), ctx(http, runtime))
    solution = build_solution(
        CaptchaType.AKAMAI_SENSOR,
        {"token": result.token, "extra": result.extra},
        site_url=SITE,
    )

    assert solution["cookies"] == {"_abck": SATISFIED, "bm_sz": "seed-1"}
    assert solution["domains"] == {"site.test": {"cookies": {"_abck": SATISFIED, "bm_sz": "seed-1"}}}
    assert solution["userAgent"] == UA


# ---- failure mapping --------------------------------------------------------


async def test_never_settling_is_retryable_and_reports_the_marker(runtime):
    http = FakeHTTP({SITE: page(("_abck", UNSATISFIED)), AKAM_URL: tl(("_abck", INTERMEDIATE))})

    with pytest.raises(ChallengeExpiredError) as exc:
        await AkamaiSensorSolver().solve_http(task(), ctx(http, runtime))

    assert exc.value.context["state"] == abck.INTERMEDIATE
    assert exc.value.context["posts"] == 3
    assert len(http.posts()) == 3


async def test_428_is_the_sec_cpt_layer(runtime):
    http = FakeHTTP(
        {
            SITE: page(("_abck", UNSATISFIED)),
            AKAM_URL: FakeResponse(428, headers={"Set-Cookie": "_abck=" + UNSATISFIED}),
        }
    )

    with pytest.raises(ChallengeExpiredError) as exc:
        await AkamaiSensorSolver().solve_http(task(), ctx(http, runtime))

    assert "sec-cpt" in exc.value.message
    assert exc.value.context["sec_cpt_cleared"] is False


async def test_428_with_a_cleared_sec_cpt_cookie_says_so(runtime):
    http = FakeHTTP(
        {
            SITE: page(("_abck", UNSATISFIED)),
            AKAM_URL: FakeResponse(428, headers={"Set-Cookie": "sec_cpt=abc~3~"}),
        }
    )

    with pytest.raises(ChallengeExpiredError) as exc:
        await AkamaiSensorSolver().solve_http(task(), ctx(http, runtime))

    assert exc.value.context["sec_cpt_cleared"] is True


async def test_a_transport_verdict_on_the_post_is_retryable(runtime):
    http = FakeHTTP(
        {
            SITE: page(("_abck", UNSATISFIED)),
            AKAM_URL: ChallengeExpiredError("antibot verdict: HTTP 403"),
        }
    )

    with pytest.raises(ChallengeExpiredError) as exc:
        await AkamaiSensorSolver().solve_http(task(), ctx(http, runtime))

    assert exc.value.context["reason"] == "ERR_CHALLENGE_EXPIRED"


async def test_proxy_errors_bubble_unchanged(runtime):
    http = FakeHTTP({SITE: ProxyError("tunnel")})

    with pytest.raises(ProxyError):
        await AkamaiSensorSolver().solve_http(task(), ctx(http, runtime))


async def test_a_missing_script_is_unsolvable(runtime):
    http = FakeHTTP({SITE: page(("_abck", UNSATISFIED), html="<html>no script here</html>")})

    with pytest.raises(UnsolvableError):
        await AkamaiSensorSolver().solve_http(task({}), ctx(http, runtime))


async def test_a_missing_runtime_is_a_config_error():
    http = FakeHTTP({SITE: page(("_abck", UNSATISFIED))})

    with pytest.raises(ConfigError):
        await AkamaiSensorSolver().solve_http(task(), ctx(http, None))


async def test_a_missing_user_agent_is_a_config_error(runtime):
    http = FakeHTTP({SITE: page(("_abck", UNSATISFIED))})

    with pytest.raises(ConfigError):
        await AkamaiSensorSolver().solve_http(task(user_agent=None), ctx(http, runtime, store=FakeStore(user_agent="")))


async def test_a_missing_http_client_is_a_config_error(runtime):
    with pytest.raises(ConfigError):
        await AkamaiSensorSolver().solve_http(task(), SolverContext(js_runtime=runtime))


async def test_a_page_that_cannot_be_read_still_fails_with_a_reason(runtime):
    http = FakeHTTP({SITE: ChallengeExpiredError("antibot verdict: HTTP 403")})

    with pytest.raises(UnsolvableError):
        await AkamaiSensorSolver().solve_http(task({}), ctx(http, runtime))
