"""BotDeflector solver -- the invisible flow against recorded wire shapes.

Every request and response here follows the 2026-10 public capture
(resolvrlabs/botdeflector-solver): a flow JWT with two inv steps, /pow/get
wrapped in a challengeToken JWT, /pow/verify taking the Cuckoo Cycle answers
plus the browser fields, /verify-flow taking three headers and answering with
the ChallengeFlowResult. The PoW answers are the pinned cross-language vector,
so a green test means the graph, DFS order and body assembly all still agree.

No network: the transport is a router over recorded bodies.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from unittest.mock import patch

import pytest

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, SensorError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.domain.task import new_task_id
from captcha.engines.botdeflector import pow as pow_mod
from captcha.engines.botdeflector.builder import QUEUE_IT_HOST, BotDeflectorBuilder
from captcha.solvers import SolverContext
from captcha.solvers.antibot.botdeflector.solver import BotDeflectorSolver

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/155.0.0.0 Safari/537.36"
SESSION = "botdeflector-pow-fixture-v1"
PARAMS = {"NODES_BITS": 14, "EDGES_BITS": 16, "CYCLE_LENGTH": 8}
CHALLENGES = [
    "1bbcad29320f20847a515cc854063cda0ddaea20cdcff45eb6b5e0e9ee15fbd4",
    "042896dc1966b8a6214e5383aba5b8b931cfa049d17aafa37eb8a77c859b95da",
    "dba0082ec68e72d47fa926dcc91fb9b00961ceb4342554a0c2aa567144a5a6d3",
]
EDGES = [
    [1916, 9296, 32052, 28331, 59430, 48251, 26106, 54087],
    [3500, 3969, 21508, 12185, 13737, 20663, 32303, 44139],
    [5814, 6465, 4652, 3891, 38592, 50674, 1006, 28273],
]
VISITOR = "421dffd594fd3062abf1f8852b99448d"
STEP_ONE = "step-one." + "a" * 32 + ".sig"
STEP_TWO = "step-two." + "b" * 32 + ".sig"
FLOW_RESULT = "flow-result." + "c" * 32 + ".sig"


# --- wire fixtures -----------------------------------------------------------


def jwt(claims: dict[str, Any]) -> str:
    def segment(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{segment({'alg': 'HS256', 'typ': 'JWT'})}.{segment(claims)}.c2ln"


def flow_token(steps: list[str] | None = None, ttl: int = 60) -> str:
    kinds = ["inv"] * 2 if steps is None else steps
    flow = [{"type": kind, "complexity": 1} for kind in kinds]
    return jwt(
        {
            "accountId": "example",
            "siteKey": "demo",
            "challengeFlow": flow,
            "sessionId": SESSION,
            "iat": int(time.time()),
            "exp": int(time.time()) + ttl,
        }
    )


def challenge_token(*, ttl: int = 30) -> str:
    return jwt(
        {
            "challenges": CHALLENGES,
            "params": dict(PARAMS),
            "challengeType": "inv",
            "sessionId": SESSION,
            "it": int(time.time() * 1000),
            "exp": int(time.time()) + ttl,
        }
    )


def pow_get_answer(*, ttl: int = 30) -> dict[str, Any]:
    return {"challengeToken": challenge_token(ttl=ttl)}


def step_answer(token: str) -> dict[str, Any]:
    return {"token": token}


# --- fakes -------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload: Any = b"", *, status: int = 200, set_cookie: str | None = None) -> None:
        if isinstance(payload, (bytes, str)):
            self.body: bytes = payload.encode() if isinstance(payload, str) else payload
        else:
            self.body = json.dumps(payload).encode()
        self.status = status
        self.headers = [("content-type", "application/json")]
        if set_cookie:
            self.headers.append(("set-cookie", set_cookie))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def headers_all(self, name: str) -> list[str]:
        return [value for key, value in self.headers if key.lower() == name.lower()]


class FakeHTTP:
    """Routes by path; a list means one answer per call, in order."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = {path: list(value) if isinstance(value, list) else [value] for path, value in routes.items()}
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._dispatch("GET", url, kwargs)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._dispatch("POST", url, kwargs)

    def _dispatch(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
        path = path.split("?")[0]
        sent = dict(kwargs)
        if sent.get("cookies") is not None:
            # snapshot: the solver mutates its jar after every response
            sent["cookies"] = dict(sent["cookies"])
        self.calls.append({"method": method, "url": url, "path": path, **sent})
        answers = self.routes.get(path)
        if not answers:
            raise AssertionError(f"unexpected {method} {url}")
        return answers.pop(0) if len(answers) > 1 else answers[0]


class FakeSensors:
    def __init__(self, builder: Any) -> None:
        self.builder = builder

    def get(self, engine: Any) -> Any:
        return self.builder


def make_task(**payload: Any) -> Task:
    base: dict[str, Any] = {"flowToken": flow_token(), "userAgent": UA}
    base.update(payload)
    return Task(
        id=new_task_id(),
        type=CaptchaType.BOTDEFLECTOR,
        payload=base,
        site_url="https://botdeflector.eu/contact",
        user_agent=UA,
    )


def make_ctx(http: Any, *, sensors: Any = None) -> SolverContext:
    return SolverContext(http=http, sensors=sensors or FakeSensors(BotDeflectorBuilder()))


def happy_http() -> FakeHTTP:
    return FakeHTTP(
        {
            "/pow/get": [pow_get_answer(), pow_get_answer()],
            "/pow/verify": [step_answer(STEP_ONE), step_answer(STEP_TWO)],
            "/verify-flow": [{"ChallengeFlowResult": FLOW_RESULT}],
        }
    )


# --- the flow ----------------------------------------------------------------


async def test_the_invisible_flow_runs_in_order_and_returns_the_flow_result() -> None:
    http = happy_http()
    task = make_task()

    result = await BotDeflectorSolver().solve_http(task, make_ctx(http))

    assert result.token == FLOW_RESULT
    assert result.extra is not None and result.extra["userAgent"] == UA
    assert result.latency_ms >= 0.0
    assert [call["method"] for call in http.calls] == ["GET", "POST", "GET", "POST", "GET"]
    assert [call["url"] for call in http.calls] == [
        "https://botdeflector.eu/pow/get",
        "https://botdeflector.eu/pow/verify",
        "https://botdeflector.eu/pow/get",
        "https://botdeflector.eu/pow/verify",
        "https://botdeflector.eu/verify-flow",
    ]


async def test_the_2captcha_solution_is_token_plus_user_agent() -> None:
    http = happy_http()

    result = await BotDeflectorSolver().solve_http(make_task(), make_ctx(http))

    solution = build_solution(CaptchaType.BOTDEFLECTOR, {"token": result.token, "extra": result.extra})
    assert solution is not None
    assert solution["token"] == FLOW_RESULT
    assert solution["userAgent"] == UA


async def test_pow_get_carries_the_flow_token_and_browser_origin() -> None:
    http = happy_http()
    task = make_task()
    raw = task.payload["flowToken"]

    await BotDeflectorSolver().solve_http(task, make_ctx(http))

    headers = http.calls[0]["headers"]
    assert headers["x-tt-flow-token"] == raw
    assert headers["origin"] == "https://botdeflector.eu"
    assert headers["referer"] == "https://botdeflector.eu/contact"
    assert headers["user-agent"] == UA
    assert headers["content-type"] == "application/json"
    assert headers["sec-fetch-site"] == "same-site"


async def test_pow_verify_body_is_the_recorded_browser_shape() -> None:
    challenge = challenge_token()
    http = FakeHTTP(
        {
            "/pow/get": [{"challengeToken": challenge}, {"challengeToken": challenge}],
            "/pow/verify": [step_answer(STEP_ONE), step_answer(STEP_TWO)],
            "/verify-flow": [{"ChallengeFlowResult": FLOW_RESULT}],
        }
    )
    task = make_task()

    await BotDeflectorSolver().solve_http(task, make_ctx(http))

    body = http.calls[1]["json"]
    raw = task.payload["flowToken"]
    assert [solution["edgeIndices"] for solution in body["solution"]] == EDGES
    assert [solution["challenge"] for solution in body["solution"]] == CHALLENGES
    assert body["params"] == {**PARAMS, "GRAPH_SEED": SESSION}
    assert body["challengeToken"] == challenge
    assert body["challengeFlowToken"] == raw
    assert len(body["solution_fingerprint"]) == 32
    assert isinstance(body["time"], str) and len(body["time"].split(".")[1]) == 3
    assert "x-tt-flow-token" not in http.calls[1]["headers"]


async def test_verify_flow_carries_the_three_headers_and_the_joined_steps() -> None:
    http = happy_http()
    task = make_task()

    await BotDeflectorSolver().solve_http(task, make_ctx(http))

    verify = http.calls[4]
    headers = verify["headers"]
    assert headers["x-tt-ch-flow-token"] == task.payload["flowToken"]
    assert headers["x-tt-solution-tokens"] == f"{STEP_ONE},{STEP_TWO}"
    assert headers["x-tt-client-fingerprint"] == http.calls[1]["json"]["solution_fingerprint"]


async def test_one_graph_is_built_for_every_step_of_the_flow() -> None:
    http = happy_http()

    with patch.object(pow_mod, "build_graph", wraps=pow_mod.build_graph) as build:
        await BotDeflectorSolver().solve_http(make_task(), make_ctx(http))

    assert build.call_count == 1


async def test_a_queue_it_page_uses_the_queue_it_host() -> None:
    http = happy_http()
    task = Task(
        id=new_task_id(),
        type=CaptchaType.BOTDEFLECTOR,
        payload={"flowToken": flow_token(steps=["inv"]), "userAgent": UA},
        site_url="https://example.queue-it.net/softblock/?c=example",
        user_agent=UA,
    )

    await BotDeflectorSolver().solve_http(task, make_ctx(http))

    assert http.calls[0]["url"] == QUEUE_IT_HOST + "/pow/get"
    assert http.calls[0]["headers"]["origin"] == "https://example.queue-it.net"


async def test_a_payload_domain_wins_over_the_default() -> None:
    http = happy_http()
    task = make_task(domain="challenge.queue-it.net")

    await BotDeflectorSolver().solve_http(task, make_ctx(http))

    assert http.calls[0]["url"] == "https://challenge.queue-it.net/pow/get"


async def test_a_pre_solved_flow_result_short_circuits() -> None:
    http = FakeHTTP({})
    result = await BotDeflectorSolver().solve_http(make_task(tt_solutions=FLOW_RESULT), make_ctx(http))

    assert result.token == FLOW_RESULT
    assert result.extra == {"userAgent": UA}
    assert http.calls == []


async def test_a_cookie_set_by_the_challenge_host_is_returned() -> None:
    http = FakeHTTP(
        {
            "/pow/get": [FakeResponse(pow_get_answer(), set_cookie="Queue-it-visitorsession=abc; Path=/")],
            "/pow/verify": [step_answer(STEP_ONE)],
            "/verify-flow": [FakeResponse({"ChallengeFlowResult": FLOW_RESULT}, set_cookie="visitor=xyz")],
        }
    )

    result = await BotDeflectorSolver().solve_http(make_task(flowToken=flow_token(steps=["inv"])), make_ctx(http))

    assert result.extra is not None
    assert result.extra["cookies"] == {"Queue-it-visitorsession": "abc", "visitor": "xyz"}
    assert http.calls[1]["cookies"] == {"Queue-it-visitorsession": "abc"}


# --- the engine builder is the fingerprint owner -----------------------------


async def test_the_visitor_id_is_the_builders_and_is_stable() -> None:
    http = happy_http()

    await BotDeflectorSolver().solve_http(make_task(), make_ctx(http))

    visitor = http.calls[1]["json"]["solution_fingerprint"]
    assert visitor == http.calls[4]["headers"]["x-tt-client-fingerprint"]
    assert len(visitor) == 32


async def test_an_explicit_visitor_id_from_the_client_wins() -> None:
    http = happy_http()
    task = make_task(fingerprint={"visitorId": VISITOR})

    await BotDeflectorSolver().solve_http(task, make_ctx(http))

    assert http.calls[1]["json"]["solution_fingerprint"] == VISITOR


# --- error mapping -----------------------------------------------------------


async def test_no_flow_token_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError):
        await BotDeflectorSolver().solve_http(make_task(flowToken=None), make_ctx(FakeHTTP({})))


async def test_classic_site_key_without_a_flow_token_is_a_sensor_error() -> None:
    task = make_task(flowToken=None, siteKey="demo", accountId="example")

    with pytest.raises(SensorError) as exc:
        await BotDeflectorSolver().solve_http(task, make_ctx(FakeHTTP({})))

    assert "FILL" in exc.value.context


async def test_an_expired_flow_token_never_hits_the_wire() -> None:
    http = FakeHTTP({})
    task = make_task(flowToken=flow_token(ttl=-5))

    with pytest.raises(ChallengeExpiredError):
        await BotDeflectorSolver().solve_http(task, make_ctx(http))
    assert http.calls == []


async def test_an_empty_challenge_flow_is_unsolvable() -> None:
    http = FakeHTTP({})
    task = make_task(flowToken=flow_token(steps=[]))

    with pytest.raises(UnsolvableError):
        await BotDeflectorSolver().solve_http(task, make_ctx(http))
    assert http.calls == []


async def test_an_icon_step_is_refused_by_the_http_lane() -> None:
    http = FakeHTTP({})
    task = make_task(flowToken=flow_token(steps=["inv", "icon"]))

    with pytest.raises(UnsolvableError) as exc:
        await BotDeflectorSolver().solve_http(task, make_ctx(http))

    assert exc.value.context["unsupported"] == ["icon"]
    assert http.calls == []


async def test_a_spent_flow_token_on_pow_get_is_an_expired_challenge() -> None:
    http = FakeHTTP({"/pow/get": [FakeResponse({"code": "P033"}, status=400)]})

    with pytest.raises(ChallengeExpiredError) as exc:
        await BotDeflectorSolver().solve_http(make_task(), make_ctx(http))

    assert exc.value.context["code"] == "P033"


async def test_a_broken_challenge_host_is_a_sensor_error() -> None:
    http = FakeHTTP({"/pow/get": [FakeResponse({"error": "boom"}, status=503)]})

    with pytest.raises(SensorError):
        await BotDeflectorSolver().solve_http(make_task(), make_ctx(http))


async def test_pow_get_without_challenges_is_a_sensor_error() -> None:
    http = FakeHTTP({"/pow/get": [FakeResponse({"challengeToken": jwt({"sessionId": SESSION})})]})

    with pytest.raises(SensorError):
        await BotDeflectorSolver().solve_http(make_task(), make_ctx(http))


async def test_pow_verify_without_a_step_token_is_a_sensor_error() -> None:
    http = FakeHTTP(
        {
            "/pow/get": [pow_get_answer()],
            "/pow/verify": [FakeResponse({"token": "short"})],
        }
    )

    with pytest.raises(SensorError):
        await BotDeflectorSolver().solve_http(make_task(flowToken=flow_token(steps=["inv"])), make_ctx(http))


async def test_verify_flow_returning_a_page_is_a_sensor_error() -> None:
    http = FakeHTTP(
        {
            "/pow/get": [pow_get_answer()],
            "/pow/verify": [step_answer(STEP_ONE)],
            "/verify-flow": [FakeResponse("<html>botDeflectorJwtToken</html>")],
        }
    )

    with pytest.raises(SensorError):
        await BotDeflectorSolver().solve_http(make_task(flowToken=flow_token(steps=["inv"])), make_ctx(http))


async def test_a_worker_without_http_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        await BotDeflectorSolver().solve_http(make_task(), SolverContext(sensors=FakeSensors(BotDeflectorBuilder())))


async def test_a_worker_without_the_sensor_registry_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        await BotDeflectorSolver().solve_http(make_task(), SolverContext(http=FakeHTTP({})))
