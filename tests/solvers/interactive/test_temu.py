"""Unit tests for the Temu aegis solver. No network."""

from __future__ import annotations

import asyncio

import pytest
from vendor_fakes import FakeHTTP, Reply, builtins, make_task, stub_cv

from captcha.domain import CaptchaType, SolverResult
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.solvers import SolverContext
from captcha.solvers.interactive.temu import endpoints as ep
from captcha.solvers.interactive.temu.solver import TemuSolver

BG = "https://img.test/temu-bg.png"
PIECE = "https://img.test/temu-piece.png"

INIT = {
    "success": True,
    "result": {
        "captchaId": "C-1",
        "bgUrl": BG,
        "pieceUrl": PIECE,
        "challengeType": "slide",
    },
}
VERIFY = {
    "success": True,
    "result": {"token": "TEMU-TOKEN", "scene": "login"},
}
SET_COOKIE = [
    ("Set-Cookie", "session=abc123; Path=/; HttpOnly"),
    ("Set-Cookie", "risk=xyz; Path=/"),
]


def _replies(verify: object = VERIFY, *, verify_headers: list[tuple[str, str]] | None = None) -> dict[str, object]:
    return {
        ep.CAPTCHA_INIT: Reply(INIT),
        BG: Reply(body=b"bg"),
        PIECE: Reply(body=b"piece"),
        ep.CAPTCHA_VERIFY: Reply(verify, headers=verify_headers if verify_headers is not None else SET_COOKIE),
    }


def _task(payload: dict[str, object]) -> object:
    return make_task(
        CaptchaType.TEMU,
        payload,
        site_url="https://www.temu.com/goods.html",
        user_agent="UA/1",
    )


def _solve(payload: dict[str, object], http: FakeHTTP) -> SolverResult:
    return asyncio.run(TemuSolver().solve_http(_task(payload), SolverContext(http=http)))


def test_flow_returns_cookies_and_projects_cookie_solution(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_cv(monkeypatch, TemuSolver, SolverResult(offset=140.0))
    http = FakeHTTP(_replies())
    result = _solve({"anti_content": "AC-1", "scene": "login"}, http)

    assert result.token == "TEMU-TOKEN"
    assert result.extra["cookies"] == {"session": "abc123", "risk": "xyz"}
    assert result.extra["domain"] == "www.temu.com"
    assert calls[0][0] is CaptchaType.SLIDER

    init_call = next(call for call in http.calls if call[1] == ep.CAPTCHA_INIT)[2]
    assert init_call["headers"]["anti-content"] == "AC-1"
    assert init_call["headers"]["user-agent"] == "UA/1"

    verify_call = next(call for call in http.calls if call[1] == ep.CAPTCHA_VERIFY)[2]
    assert verify_call["json"]["captchaId"] == "C-1"
    assert verify_call["json"]["offset"] == 140
    assert len(verify_call["json"]["track"]) > 2

    solution = build_solution(CaptchaType.TEMU, builtins(result), site_url="https://www.temu.com/goods.html")
    assert solution is not None
    assert solution["cookies"] == {"session": "abc123", "risk": "xyz"}
    assert solution["domains"]["www.temu.com"]["cookies"]["session"] == "abc123"
    assert solution["userAgent"] == "UA/1"


def test_click_variant_returns_coordinates_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, TemuSolver, SolverResult(coords=[(12.0, 34.0)]))
    init = {
        "success": True,
        "data": {
            "captchaId": "C-2",
            "bgUrl": BG,
            "challengeType": "semantic_click",
            "prompt": "click the red cup",
        },
    }
    http = FakeHTTP(
        {
            ep.CAPTCHA_INIT: Reply(init),
            BG: Reply(body=b"bg"),
            ep.CAPTCHA_VERIFY: Reply({"success": True, "result": {"token": "T-2"}}),
        }
    )
    _solve({"anti_content": "AC-2", "pageurl": "https://www.temu.com/x"}, http)
    body = next(call for call in http.calls if call[1] == ep.CAPTCHA_VERIFY)[2]["json"]
    assert body["coords"] == [[12.0, 34.0]]
    assert "track" in body


def test_missing_anti_content_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="anti_content"):
        _solve({"pageurl": "https://www.temu.com/goods.html"}, FakeHTTP({}))


def test_missing_http_client_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        asyncio.run(TemuSolver().solve_http(_task({"anti_content": "AC"}), SolverContext()))


@pytest.mark.parametrize("status", [401, 403, 410, 429])
def test_stale_status_is_challenge_expired(status: int) -> None:
    http = FakeHTTP({ep.CAPTCHA_INIT: Reply(status=status, body=b"")})
    with pytest.raises(ChallengeExpiredError) as exc:
        _solve({"anti_content": "AC"}, http)
    assert exc.value.context["status"] == status


def test_expired_error_message_is_challenge_expired() -> None:
    http = FakeHTTP({ep.CAPTCHA_INIT: Reply({"success": False, "error_code": 50000, "error_msg": "captcha expired"})})
    with pytest.raises(ChallengeExpiredError):
        _solve({"anti_content": "AC"}, http)


def test_refusal_is_unsolvable() -> None:
    http = FakeHTTP({ep.CAPTCHA_INIT: Reply({"success": False, "error_code": 50000, "error_msg": "risk denied"})})
    with pytest.raises(UnsolvableError, match="refused"):
        _solve({"anti_content": "AC"}, http)


def test_missing_captcha_id_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, TemuSolver, SolverResult(offset=10.0))
    http = FakeHTTP({ep.CAPTCHA_INIT: Reply({"success": True, "result": {"bgUrl": BG}})})
    with pytest.raises(UnsolvableError, match="captcha id"):
        _solve({"anti_content": "AC"}, http)


def test_grid_variant_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, TemuSolver, SolverResult(offset=10.0))
    init = {"success": True, "data": {"captchaId": "C-3", "bgUrl": BG, "challengeType": "3x3_grid"}}
    http = FakeHTTP({ep.CAPTCHA_INIT: Reply(init)})
    with pytest.raises(UnsolvableError, match="grid"):
        _solve({"anti_content": "AC"}, http)


def test_verify_without_cookie_or_token_is_challenge_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, TemuSolver, SolverResult(offset=10.0))
    http = FakeHTTP(_replies({"success": True, "result": {}}, verify_headers=[]))
    with pytest.raises(ChallengeExpiredError, match="no session cookie"):
        _solve({"anti_content": "AC"}, http)
