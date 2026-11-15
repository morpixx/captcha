"""Unit tests for the Basilisk click solver. No network."""

from __future__ import annotations

import asyncio

import pytest
from vendor_fakes import FakeHTTP, Reply, builtins, make_task, stub_cv

from captcha.domain import CaptchaType, SolverResult
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.solvers import SolverContext
from captcha.solvers.interactive.basilisk import endpoints as ep
from captcha.solvers.interactive.basilisk.solver import BasiliskSolver

IMG = "https://img.test/basilisk-grid.png"
CHALLENGE = {"data": {"challengeId": "B-1", "image": IMG, "target_text": "click the sponsor logo"}}
RESPONSE = "f" * 32
VERIFY = {"data": {"captcha_response": RESPONSE}}


def _replies(verify: object = VERIFY) -> dict[str, object]:
    return {
        ep.CHALLENGE: Reply(CHALLENGE),
        IMG: Reply(body=b"grid"),
        ep.VERIFY: Reply(verify),
    }


def _task(payload: dict[str, object]) -> object:
    return make_task(
        CaptchaType.BASILISK,
        payload,
        site_url="https://shop.test/register",
        user_agent="UA/1",
    )


def _solve(payload: dict[str, object], http: FakeHTTP) -> SolverResult:
    return asyncio.run(BasiliskSolver().solve_http(_task(payload), SolverContext(http=http)))


def test_flow_returns_captcha_response_and_header(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_cv(monkeypatch, BasiliskSolver, SolverResult(coords=[(30.0, 40.0)]))
    http = FakeHTTP(_replies())
    result = _solve({"sitekey": "SITE-1", "origin": "https://shop.test/register"}, http)

    assert result.token == RESPONSE
    assert calls[0][0] is CaptchaType.CLICK
    assert calls[0][1]["target"] == "click the sponsor logo"

    verify_call = next(call for call in http.calls if call[1] == ep.VERIFY)[2]
    body = verify_call["json"]
    assert body["challengeId"] == "B-1"
    assert body["siteKey"] == "SITE-1"
    assert body["coords"] == [[30.0, 40.0]]
    assert body["track"]
    assert body["dwell"] >= 0

    solution = build_solution(CaptchaType.BASILISK, builtins(result))
    assert solution is not None
    assert solution["data"]["captcha_response"] == RESPONSE
    assert solution["headers"]["User-Agent"] == "UA/1"


def test_missing_sitekey_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="sitekey"):
        _solve({}, FakeHTTP({}))


def test_missing_http_client_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        asyncio.run(BasiliskSolver().solve_http(_task({"sitekey": "S"}), SolverContext()))


@pytest.mark.parametrize("status", [401, 403, 410, 429])
def test_stale_status_is_challenge_expired(status: int) -> None:
    http = FakeHTTP({ep.CHALLENGE: Reply(status=status, body=b"")})
    with pytest.raises(ChallengeExpiredError):
        _solve({"sitekey": "SITE-1"}, http)


def test_refusal_is_unsolvable() -> None:
    http = FakeHTTP({ep.CHALLENGE: Reply({"success": False, "code": "E1", "message": "bad origin"})})
    with pytest.raises(UnsolvableError, match="refused"):
        _solve({"sitekey": "SITE-1"}, http)


def test_challenge_without_target_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, BasiliskSolver, SolverResult(coords=[(1.0, 2.0)]))
    http = FakeHTTP({ep.CHALLENGE: Reply({"data": {"challengeId": "B-2", "image": IMG}})})
    with pytest.raises(UnsolvableError, match="target text"):
        _solve({"sitekey": "SITE-1"}, http)


def test_verify_without_response_is_challenge_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, BasiliskSolver, SolverResult(coords=[(1.0, 2.0)]))
    http = FakeHTTP(_replies({"data": {}}))
    with pytest.raises(ChallengeExpiredError, match="captcha_response"):
        _solve({"sitekey": "SITE-1"}, http)
