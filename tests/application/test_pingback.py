"""Pingback payload and delivery tests.

A callback carries exactly the body a client would read from `getTaskResult`:
the per-type solution keys come from `build_solution`, so these assertions pin
the projected shape rather than whatever the solver happened to return. The
delivery tests pin the contract that matters operationally — a failing callback
is retried and then swallowed.
"""

from __future__ import annotations

import json

import httpx
import respx

from captcha.adapters.pingback import PingbackSender
from captcha.application.pingback import pingback_payload
from captcha.domain.enums import CaptchaType, TaskStatus
from captcha.domain.task import SolverResult

URL = "https://client.example.com/2captcha-callback"


def test_ready_payload_uses_canonical_token_solution() -> None:
    result = SolverResult(token="T", extra={"userAgent": "UA"})
    payload = pingback_payload(
        TaskStatus.READY,
        result,
        None,
        CaptchaType.RECAPTCHA_V2,
        "https://shop.example.com/checkout",
    )
    assert payload == {
        "errorId": 0,
        "status": "ready",
        "solution": {"gRecaptchaResponse": "T", "userAgent": "UA"},
    }


def test_failed_payload_carries_the_error_envelope() -> None:
    payload = pingback_payload(
        TaskStatus.FAILED,
        None,
        {"code": "ERR_UNSOLVABLE", "message": "no answer"},
        CaptchaType.RECAPTCHA_V2,
        None,
    )
    assert payload == {
        "errorId": 0,
        "status": "failed",
        "errorCode": "ERR_UNSOLVABLE",
        "errorDescription": "no answer",
    }


def test_unknown_type_passes_the_stored_result_through() -> None:
    payload = pingback_payload("ready", {"token": "T"}, None, "retired_type", None)
    assert payload == {"errorId": 0, "status": "ready", "solution": {"token": "T"}}


async def test_send_retries_a_500_then_succeeds() -> None:
    payload = pingback_payload(TaskStatus.READY, SolverResult(token="T"), None, CaptchaType.TURNSTILE, None)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(side_effect=[httpx.Response(500), httpx.Response(200)])
        sender = PingbackSender(timeout=1.0, max_attempts=3, backoff_sec=0.0)
        try:
            assert await sender.send(URL, payload) is True
        finally:
            await sender.aclose()

    assert route.call_count == 2
    request = route.calls[-1].request
    assert request.headers["content-type"].startswith("application/json")
    assert json.loads(request.content) == payload


async def test_send_swallows_exhausted_retries() -> None:
    payload = pingback_payload(TaskStatus.READY, SolverResult(token="T"), None, CaptchaType.TURNSTILE, None)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(503))
        sender = PingbackSender(timeout=1.0, max_attempts=2, backoff_sec=0.0)
        try:
            assert await sender.send(URL, payload) is False
        finally:
            await sender.aclose()

    assert route.call_count == 2


async def test_send_swallows_connection_errors() -> None:
    payload = pingback_payload(TaskStatus.READY, SolverResult(token="T"), None, CaptchaType.TURNSTILE, None)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(side_effect=httpx.ConnectError("no route"))
        sender = PingbackSender(timeout=1.0, max_attempts=2, backoff_sec=0.0)
        try:
            assert await sender.send(URL, payload) is False
        finally:
            await sender.aclose()

    assert route.call_count == 2
