"""BotDeflector wire layer -- flow token, PoW payloads, step tokens, payload keys.

The HTTP flow in solver.py is still the not-implemented stub, so what is pinned
here is the contract every step of that flow will stand on: the flow JWT decides
which steps run and when it expires, a /pow/get answer is validated before any
graph build, and a step token is JSON or a bare string -- never an HTML page.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import pytest

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ChallengeExpiredError, SensorError, UnsolvableError
from captcha.domain.task import new_task_id
from captcha.engines.botdeflector.builder import DEFAULT_DOMAIN, QUEUE_IT_HOST
from captcha.solvers.antibot.botdeflector import payload, wire

PARAMS = {"NODES_BITS": 14, "EDGES_BITS": 16, "CYCLE_LENGTH": 8}


def jwt(claims: dict[str, Any]) -> str:
    def segment(value: dict[str, Any]) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{segment({'alg': 'none', 'typ': 'JWT'})}.{segment(claims)}.c2ln"


def claims(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "accountId": "acc-1",
        "siteKey": "site-1",
        "sessionId": "sess-1",
        "challengeFlow": [{"type": "inv", "complexity": 1}, {"type": "icon", "complexity": 1}],
        "exp": int(time.time()) + 60,
    }
    base.update(overrides)
    return base


def make_task(**kwargs: Any) -> Task:
    return Task(id=new_task_id(), type=CaptchaType.BOTDEFLECTOR, payload={}, **kwargs)


# --- flow token --------------------------------------------------------------


def test_flow_token_yields_steps_and_session() -> None:
    token = wire.decode_flow_token(jwt(claims()))

    assert token.steps == ("inv", "icon")
    assert token.session_id == "sess-1"
    assert token.account_id == "acc-1"
    assert token.site_key == "site-1"
    assert token.expires_in is not None and token.expires_in > 0


def test_flow_token_accepts_snake_case_claims() -> None:
    token = wire.decode_flow_token(
        jwt({"session_id": "s2", "site_key": "k2", "challenge_flow": ["pow"], "exp": int(time.time()) + 60})
    )

    assert token.steps == ("pow",)
    assert token.session_id == "s2"
    assert token.site_key == "k2"


def test_expired_flow_token_is_an_expired_challenge() -> None:
    with pytest.raises(ChallengeExpiredError):
        wire.decode_flow_token(jwt(claims(exp=int(time.time()) - 1)))


def test_malformed_flow_tokens_are_unsolvable() -> None:
    with pytest.raises(UnsolvableError):
        wire.decode_flow_token("not-a-jwt")
    with pytest.raises(UnsolvableError):
        wire.decode_flow_token("a.b.c")
    with pytest.raises(UnsolvableError):
        wire.decode_flow_token(jwt(claims(challengeFlow="inv")))


def test_implausibly_long_flows_are_refused() -> None:
    with pytest.raises(UnsolvableError):
        wire.decode_flow_token(jwt(claims(challengeFlow=["inv"] * (wire.MAX_FLOW_STEPS + 1))))


# --- pow/get -----------------------------------------------------------------


def test_pow_payload_reads_challenges_and_params() -> None:
    body = json.dumps({"challenges": ["aa", "bb"], "params": {**PARAMS, "GRAPH_SEED": "seed-1"}, "sessionId": "s9"})

    challenges, params = wire.pow_payload(body, wire.decode_flow_token(jwt(claims())))

    assert challenges == ["aa", "bb"]
    assert params.nodes == 1 << 14
    assert params.graph_seed == "seed-1"
    assert params.cycle_length == 8


def test_pow_payload_falls_back_to_the_flow_session_as_seed() -> None:
    body = {"challenges": ["aa"], "params": dict(PARAMS)}

    _, params = wire.pow_payload(body, wire.decode_flow_token(jwt(claims())))

    assert params.graph_seed == "sess-1"


def test_pow_payload_without_challenges_is_a_sensor_error() -> None:
    with pytest.raises(SensorError):
        wire.pow_payload({"params": {**PARAMS, "GRAPH_SEED": "s"}}, wire.decode_flow_token(jwt(claims())))
    with pytest.raises(SensorError):
        wire.pow_payload("not json", wire.decode_flow_token(jwt(claims())))


def test_solution_body_is_the_verify_shape() -> None:
    assert wire.solution_body([{"challenge": "aa", "edgeIndices": [1, 2]}]) == {
        "solution": [{"challenge": "aa", "edgeIndices": [1, 2]}]
    }


# --- step tokens -------------------------------------------------------------


def test_step_token_accepts_json_and_bare_bodies() -> None:
    token = "t" * 40
    assert wire.step_token({"token": token}, what="pow/verify") == token
    assert wire.step_token({"solutionToken": token}, what="pow/verify") == token
    assert wire.step_token(token, what="verify-flow") == token
    assert wire.step_token(json.dumps({"token": token}), what="verify-flow") == token


def test_step_token_refuses_pages_and_short_values() -> None:
    with pytest.raises(SensorError):
        wire.step_token({"token": "short"}, what="pow/verify")
    with pytest.raises(SensorError):
        wire.step_token("<html>nope</html>", what="pow/verify")
    with pytest.raises(SensorError):
        wire.step_token({}, what="pow/verify")
    with pytest.raises(SensorError):
        wire.step_token(b"", what="pow/verify")


# --- payload keys ------------------------------------------------------------


def test_flow_token_key_spellings() -> None:
    assert payload.flow_token({"botDeflectorJwtToken": "a.b.c"}) == "a.b.c"
    assert payload.flow_token({"botReflectorJwtToken": "d.e.f"}) == "d.e.f"
    assert payload.flow_token({"flowToken": " g.h.i "}) == "g.h.i"
    assert payload.flow_token({}) == ""


def test_domain_for_prefers_the_payload_then_queue_it_then_the_default() -> None:
    task = make_task(site_url="https://shop.queue-it.net/waiting")
    assert payload.domain_for(task, {"domain": "challenge.queue-it.net/path"}) == "https://challenge.queue-it.net"
    assert payload.domain_for(task, {}) == QUEUE_IT_HOST
    assert payload.domain_for(make_task(site_url="https://example.test/"), {}) == DEFAULT_DOMAIN


def test_website_url_requires_http() -> None:
    assert payload.website_url(make_task(site_url="https://example.test/page"), {}) == "https://example.test/page"
    assert payload.website_url(make_task(), {"websiteURL": "ftp://example.test"}) == ""


def test_headers_are_lowercased() -> None:
    assert payload.headers({"headers": {"X-Client": "1", "Bad": None}}) == {"x-client": "1"}
    assert payload.headers({"headers": "nope"}) == {}
