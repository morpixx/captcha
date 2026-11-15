"""Unit tests for the Hunt solver: server API and widget browser lane."""

from __future__ import annotations

import asyncio

import pytest
from vendor_fakes import (
    FakeBrowser,
    FakeHTTP,
    FakePage,
    FakeSession,
    Reply,
    builtins,
    make_task,
    stub_cv,
)

from captcha.domain import CaptchaType, SolverResult
from captcha.domain.errors import (
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    UnsolvableError,
)
from captcha.domain.solution import build_solution
from captcha.solvers import SolverContext
from captcha.solvers.interactive.hunt import browser as hunt_browser
from captcha.solvers.interactive.hunt import endpoints as ep
from captcha.solvers.interactive.hunt.solver import HuntSolver

HOST = "https://hunt.test"
IMG = "https://img.test/hunt.png"

INIT = {"data": {"huntId": "H-1", "imageUrl": IMG, "prompt": "click the red car"}}
VERIFY = {"data": {"token": "HUNT-TOKEN"}}


def _replies(verify: object = VERIFY) -> dict[str, object]:
    return {
        HOST + ep.PATH_INIT: Reply(INIT),
        IMG: Reply(body=b"image"),
        HOST + ep.PATH_VERIFY: Reply(verify),
    }


def _task(payload: dict[str, object]) -> object:
    return make_task(
        CaptchaType.HUNT,
        payload,
        site_url="https://shop.test/f",
        user_agent="UA/1",
    )


def _solve(payload: dict[str, object], http: FakeHTTP) -> SolverResult:
    return asyncio.run(HuntSolver().solve_http(_task(payload), SolverContext(http=http)))


def test_server_api_flow_returns_nested_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_cv(monkeypatch, HuntSolver, SolverResult(coords=[(11.0, 22.0)]))
    http = FakeHTTP(_replies())
    result = _solve({"host": HOST, "session_jwt": "JWT-1"}, http)

    assert result.token == "HUNT-TOKEN"
    assert result.extra["data"] == {"token": "HUNT-TOKEN"}
    assert calls[0][0] is CaptchaType.CLICK
    assert calls[0][1]["target"] == "click the red car"

    init_call = next(call for call in http.calls if call[1] == HOST + ep.PATH_INIT)[2]
    assert init_call["headers"]["authorization"] == "Bearer JWT-1"
    verify_call = next(call for call in http.calls if call[1] == HOST + ep.PATH_VERIFY)[2]
    assert verify_call["json"]["huntId"] == "H-1"
    assert verify_call["json"]["coords"] == [[11.0, 22.0]]
    assert verify_call["json"]["track"]

    solution = build_solution(CaptchaType.HUNT, builtins(result))
    assert solution is not None
    assert solution["data"]["token"] == "HUNT-TOKEN"


def test_host_without_scheme_defaults_to_https(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, HuntSolver, SolverResult(coords=[(1.0, 2.0)]))
    http = FakeHTTP(
        {
            "https://hunt.test/api/hunt/init": Reply(INIT),
            IMG: Reply(body=b"image"),
            "https://hunt.test/api/hunt/verify": Reply(VERIFY),
        }
    )
    result = _solve({"host": "hunt.test", "session_jwt": "JWT-1"}, http)
    assert result.token == "HUNT-TOKEN"


def test_widget_mode_without_jwt_asks_for_browser() -> None:
    with pytest.raises(NeedsBrowserError):
        _solve({"widget_url": "https://hunt.test/widget?hash=1"}, FakeHTTP({}))


def test_no_jwt_and_no_widget_is_unsolvable() -> None:
    task = make_task(CaptchaType.HUNT, {"host": HOST})
    with pytest.raises(UnsolvableError, match="session_jwt"):
        asyncio.run(HuntSolver().solve_http(task, SolverContext(http=FakeHTTP({}))))


def test_jwt_without_host_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="host"):
        _solve({"session_jwt": "JWT"}, FakeHTTP({}))


def test_missing_http_client_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        asyncio.run(
            HuntSolver().solve_http(_task({"host": HOST, "session_jwt": "JWT"}), SolverContext())
        )


@pytest.mark.parametrize("status", [401, 403, 410, 429])
def test_stale_status_is_challenge_expired(status: int) -> None:
    http = FakeHTTP({HOST + ep.PATH_INIT: Reply(status=status, body=b"")})
    with pytest.raises(ChallengeExpiredError):
        _solve({"host": HOST, "session_jwt": "JWT"}, http)


def test_api_expiry_message_is_challenge_expired() -> None:
    http = FakeHTTP({HOST + ep.PATH_INIT: Reply({"success": False, "code": "x", "message": "token expired"})})
    with pytest.raises(ChallengeExpiredError):
        _solve({"host": HOST, "session_jwt": "JWT"}, http)


def test_init_without_image_is_unsolvable() -> None:
    http = FakeHTTP({HOST + ep.PATH_INIT: Reply({"data": {"huntId": "H-2", "prompt": "click"}})})
    with pytest.raises(UnsolvableError, match="no challenge image"):
        _solve({"host": HOST, "session_jwt": "JWT"}, http)


def test_verify_without_token_is_challenge_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, HuntSolver, SolverResult(coords=[(1.0, 2.0)]))
    http = FakeHTTP(_replies({"data": {}}))
    with pytest.raises(ChallengeExpiredError, match="no token"):
        _solve({"host": HOST, "session_jwt": "JWT"}, http)


# ---- browser lane -----------------------------------------------------------


async def test_browser_lane_reads_the_widget_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hunt_browser, "POLL_INTERVAL_S", 0)
    page = FakePage(
        [
            {"token": "", "host": "hunt.test", "ua": "UA/9", "challenge_shown": True},
            {"token": "HUNT-WIDGET-TOKEN", "host": "hunt.test", "ua": "UA/9", "challenge_shown": False},
        ]
    )
    browser = FakeBrowser(FakeSession(page))
    task = make_task(CaptchaType.HUNT, {"widget_url": "https://hunt.test/widget?hash=1"})
    result = await HuntSolver().solve_browser(task, SolverContext(browser=browser))

    assert result.token == "HUNT-WIDGET-TOKEN"
    assert result.solve_mode.value == "browser"
    assert result.extra["data"] == {"token": "HUNT-WIDGET-TOKEN"}
    assert result.extra["userAgent"] == "UA/9"
    assert browser.specs[0].label.startswith("hunt:")
    solution = build_solution(CaptchaType.HUNT, builtins(result))
    assert solution["data"]["token"] == "HUNT-WIDGET-TOKEN"


async def test_browser_lane_times_out_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hunt_browser, "_timeout_s", lambda task: 0.0)
    page = FakePage([{"token": "", "host": "hunt.test", "challenge_shown": True}])
    browser = FakeBrowser(FakeSession(page))
    task = make_task(CaptchaType.HUNT, {"pageurl": "https://hunt.test/play"})
    with pytest.raises(ChallengeExpiredError, match="no token"):
        await HuntSolver().solve_browser(task, SolverContext(browser=browser))


async def test_browser_lane_needs_a_provider() -> None:
    task = make_task(CaptchaType.HUNT, {"pageurl": "https://hunt.test/play"})
    with pytest.raises(ConfigError, match=r"ctx\.browser"):
        await HuntSolver().solve_browser(task, SolverContext())


async def test_browser_lane_rejects_non_http_widget_url() -> None:
    browser = FakeBrowser(FakeSession(FakePage([{"token": "x"}])))
    task = make_task(CaptchaType.HUNT, {"widget_url": "javascript:alert(1)"})
    with pytest.raises(UnsolvableError, match="http"):
        await HuntSolver().solve_browser(task, SolverContext(browser=browser))


async def test_browser_lane_rejects_short_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hunt_browser, "POLL_INTERVAL_S", 0)
    browser = FakeBrowser(FakeSession(FakePage([{"token": "abc", "host": "hunt.test"}])))
    task = make_task(CaptchaType.HUNT, {"pageurl": "https://hunt.test/play"})
    with pytest.raises(UnsolvableError, match="token shape"):
        await HuntSolver().solve_browser(task, SolverContext(browser=browser))
