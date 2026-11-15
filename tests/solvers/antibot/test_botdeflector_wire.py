"""BotDeflector wire layer -- flow token, PoW challenge, step tokens, headers.

Pinned against the 2026-10 public capture: the flow JWT decides which steps run
and when it expires, /pow/get nests a challenge JWT (challenges + params +
sessionId), the verify body carries params with GRAPH_SEED, the fingerprint and
both JWTs, and /verify-flow answers with ChallengeFlowResult. A step token is
JSON or a bare string -- never an HTML page.
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
from captcha.solvers.antibot.botdeflector import payload, transport, wire

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


# --- challenge token, verify body, headers -----------------------------------


class _Resp:
    def __init__(self, status: int, body: object = b"") -> None:
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()


def challenge_jwt(*, ttl: int = 30, **overrides: Any) -> str:
    payload: dict[str, Any] = {
        "challenges": ["aa", "bb"],
        "params": dict(PARAMS),
        "challengeType": "inv",
        "sessionId": "sess-1",
        "exp": int(time.time()) + ttl,
    }
    payload.update(overrides)
    return jwt(payload)


def test_pow_get_answer_is_unwrapped_from_the_challenge_token() -> None:
    token = challenge_jwt()

    challenge = wire.pow_challenge({"challengeToken": token}, wire.decode_flow_token(jwt(claims())))

    assert challenge.token == token
    assert challenge.challenges == ("aa", "bb")
    assert challenge.params.graph_seed == "sess-1"
    assert challenge.session_id == "sess-1"
    assert challenge.payload["challengeType"] == "inv"
    assert challenge.graph_key == (14, 16, 8, "sess-1")


def test_an_unwrapped_pow_payload_still_works() -> None:
    challenge = wire.pow_challenge(
        {"challenges": ["aa"], "params": dict(PARAMS)}, wire.decode_flow_token(jwt(claims()))
    )

    assert challenge.token == ""
    assert challenge.params.graph_seed == "sess-1"


def test_a_spent_challenge_token_is_an_expired_challenge() -> None:
    with pytest.raises(ChallengeExpiredError):
        wire.pow_challenge({"challengeToken": challenge_jwt(ttl=-1)}, wire.decode_flow_token(jwt(claims())))


def test_a_challenge_token_that_is_not_a_jwt_is_a_sensor_error() -> None:
    with pytest.raises(SensorError):
        wire.pow_challenge({"challengeToken": "not-a-jwt"}, wire.decode_flow_token(jwt(claims())))


def test_pow_verify_body_carries_params_seed_time_and_both_tokens() -> None:
    flow = wire.decode_flow_token(jwt(claims()))
    challenge = wire.pow_challenge({"challengeToken": challenge_jwt()}, flow)
    solution = [{"challenge": "aa", "edgeIndices": [1, 2]}]

    body = wire.pow_verify_body(
        challenge, solution, "421dffd594fd3062abf1f8852b99448d", elapsed_s=0.15234, flow_token="f.g.h"
    )

    assert body == {
        "solution": solution,
        "params": {**PARAMS, "GRAPH_SEED": "sess-1"},
        "solution_fingerprint": "421dffd594fd3062abf1f8852b99448d",
        "time": "0.152",
        "challengeFlowToken": "f.g.h",
        "challengeToken": challenge.token,
    }


def test_pow_verify_body_never_emits_an_empty_challenge_token() -> None:
    flow = wire.decode_flow_token(jwt(claims()))
    challenge = wire.pow_challenge({"challenges": ["aa"], "params": dict(PARAMS)}, flow)

    body = wire.pow_verify_body(challenge, [], "0" * 32, elapsed_s=0.0, flow_token="f.g.h")

    assert "challengeToken" not in body
    assert body["time"] == "0.000"


def test_api_headers_carry_the_flow_token_and_the_page_origin() -> None:
    headers = transport.api_headers(
        "f.g.h",
        origin="https://example.queue-it.net",
        referer="https://example.queue-it.net/softblock/",
        user_agent="UA",
    )

    assert headers["x-tt-flow-token"] == "f.g.h"
    assert headers["origin"] == "https://example.queue-it.net"
    assert headers["referer"] == "https://example.queue-it.net/softblock/"
    assert headers["user-agent"] == "UA"
    assert headers["sec-fetch-site"] == "same-site"
    assert transport.api_headers("f.g.h", same_site=False)["sec-fetch-site"] == "cross-site"


def test_api_headers_omit_the_flow_token_when_it_is_not_owned() -> None:
    assert "x-tt-flow-token" not in transport.api_headers("")


def test_verify_flow_headers_join_the_step_tokens() -> None:
    headers = transport.verify_flow_headers("f.g.h", ["one", "two"], "0" * 32)

    assert headers["x-tt-ch-flow-token"] == "f.g.h"
    assert headers["x-tt-solution-tokens"] == "one,two"
    assert headers["x-tt-client-fingerprint"] == "0" * 32


def test_flow_result_token_reads_the_captured_key() -> None:
    token = "flow." + "a" * 40 + ".sig"

    assert wire.flow_result_token({"ChallengeFlowResult": token}) == token
    assert wire.flow_result_token({"challengeFlowResult": token}) == token
    assert wire.flow_result_token(json.dumps({"ChallengeFlowResult": token})) == token


def test_flow_result_token_refuses_pages_and_short_values() -> None:
    with pytest.raises(SensorError):
        wire.flow_result_token({"ChallengeFlowResult": "short"})
    with pytest.raises(SensorError):
        wire.flow_result_token({"ChallengeFlowResult": "<html>botDeflectorJwtToken</html>"})
    with pytest.raises(SensorError):
        wire.flow_result_token({"other": "x"})


def test_check_status_maps_a_spent_flow_token_to_expired() -> None:
    with pytest.raises(ChallengeExpiredError) as exc:
        transport.check_status(_Resp(400, {"code": "P033"}), what="pow/get")

    assert exc.value.context == {"status": 400, "code": "P033"}


def test_check_status_maps_a_broken_host_to_a_sensor_error() -> None:
    with pytest.raises(SensorError):
        transport.check_status(_Resp(503, b"upstream"), what="pow/get")
    transport.check_status(_Resp(200, b"{}"), what="pow/get")


def test_same_site_follows_the_registrable_domain() -> None:
    assert transport.same_site("https://example.queue-it.net/softblock/", "https://dual.challenge.queue-it.net")
    assert transport.same_site("https://botdeflector.eu/contact", "https://botdeflector.eu")
    assert not transport.same_site("https://shop.example.com/", "https://botdeflector.eu")
