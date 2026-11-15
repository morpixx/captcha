"""Yidun: challenge parsing, the sealed check envelope, error translation.

The signer is injected, exactly as a worker would wire it; a solver without one
must escalate rather than post a half envelope.
"""

from __future__ import annotations

import asyncio

import pytest
from _vendors import HTTP, FakePage, FakeProvider, Garbage, Reply, run_http, stub_cv, task

from captcha.domain import CaptchaType, SolverResult
from captcha.domain.enums import SolveMode
from captcha.domain.errors import (
    CaptchaError,
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    UnsolvableError,
)
from captcha.solvers import SolverContext
from captcha.solvers.interactive.yidun import browser as yidun_browser
from captcha.solvers.interactive.yidun import endpoints as ep
from captcha.solvers.interactive.yidun import wire
from captcha.solvers.interactive.yidun.solver import YidunSolver

BG = "https://necaptcha.nosdn.127.net/bg.png"
FRONT = "https://necaptcha.nosdn.127.net/front.png"
PAGE = "https://shop.test/login"
VALIDATE = "8f2c4e1a9b7d4c3e8f2c4e1a9b7d4c3e"

GET_REPLY = {
    "error": 0,
    "msg": "ok",
    "data": {"token": "TOK1", "bg": [BG], "front": [FRONT], "type": 2, "zoneId": "Z1"},
}


def _payload(**extra):
    return {"captcha_id": "cap-1", "pageurl": PAGE, **extra}


def _http(**overrides):
    replies = {
        ep.GET_CONF: GET_REPLY,
        BG: Reply(body=b"bg"),
        FRONT: Reply(body=b"front"),
        ep.CHECK: {"result": True, "validate": VALIDATE},
    }
    replies.update(overrides)
    return HTTP(replies)


def _ctx(http, *, sealer=None):
    extra = {ep.SEALER_KEY: sealer} if sealer is not None else {}
    return SolverContext(http=http, extra=extra)


def _sealer(envelope):
    return {"data": "SEALED-DATA", "cb": "SEALED-CB"}


# ---- metadata ---------------------------------------------------------------


def test_metadata_declares_both_lanes():
    assert YidunSolver.info.types == (CaptchaType.YIDUN,)
    assert YidunSolver.info.browser is True
    assert YidunSolver.info.needs_js_runtime is True
    assert YidunSolver.info.experimental is True


def test_missing_captcha_id_is_unsolvable():
    with pytest.raises(UnsolvableError, match="captcha_id"):
        run_http(YidunSolver(), task(CaptchaType.YIDUN, {}), SolverContext())


def test_missing_http_client_is_config_error():
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), SolverContext())


# ---- wire codecs ------------------------------------------------------------


def test_parse_reply_unwraps_jsonp():
    assert wire.parse_reply('__wmjsonp({"result": true});') == {"result": True}


@pytest.mark.parametrize("text", ["<html>nope</html>", ""])
def test_parse_reply_rejects_garbage(text):
    with pytest.raises(CaptchaError, match="not JSON"):
        wire.parse_reply(text)


def test_parse_challenge_accepts_nested_data():
    challenge = wire.parse_challenge(GET_REPLY)
    assert challenge["token"] == "TOK1"
    assert challenge["bg"] == BG
    assert challenge["front"] == FRONT
    assert challenge["kind"] is CaptchaType.SLIDER


def test_parse_challenge_without_front_is_a_click_round():
    reply = {"data": {"token": "T", "bg": [BG], "type": 3}}
    challenge = wire.parse_challenge(reply)
    assert challenge["kind"] is CaptchaType.CLICK
    assert challenge["type"] == 3


def test_parse_challenge_requires_token_and_image():
    with pytest.raises(UnsolvableError, match="no challenge token"):
        wire.parse_challenge({"data": {"bg": [BG]}})
    with pytest.raises(UnsolvableError, match="no background image"):
        wire.parse_challenge({"data": {"token": "T"}})


def test_answer_plaintext_shapes_slider_and_click():
    slider = wire.answer_plaintext(SolverResult(offset=87.6), CaptchaType.SLIDER, started=0.0)
    assert slider["x"] == 88
    click = wire.answer_plaintext(SolverResult(coords=[(1.2, 3.7)]), CaptchaType.CLICK, started=0.0)
    assert click["p"] == [[1, 4]]
    with pytest.raises(UnsolvableError, match="no points"):
        wire.answer_plaintext(SolverResult(coords=[]), CaptchaType.CLICK, started=0.0)


def test_check_body_requires_both_halves():
    challenge = {"token": "T", "type": 2}
    body = wire.check_body(challenge, {"data": "D", "cb": "C"}, captcha_id="cap-1")
    assert body["cb"] == "C"
    assert body["runEnv"] == ep.RUN_ENV_WEB
    with pytest.raises(UnsolvableError, match="incomplete envelope"):
        wire.check_body(challenge, {"data": "D"}, captcha_id="cap-1")


# ---- HTTP flow --------------------------------------------------------------


def test_flow_returns_validate(monkeypatch):
    calls = stub_cv(monkeypatch, YidunSolver, offset=131.0)
    http = _http()

    result = run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(http, sealer=_sealer))

    assert result.token == VALIDATE
    assert result.solve_mode is SolveMode.HTTP
    assert calls[0][0] is CaptchaType.SLIDER

    get_params = http.calls[0][2]["params"]
    assert get_params["id"] == "cap-1"
    assert get_params["type"] == ep.TYPE_SLIDER

    body = http.by_method("POST")[0][2]["data"]
    assert body["id"] == "cap-1"
    assert body["token"] == "TOK1"
    assert body["data"] == "SEALED-DATA"
    assert body["cb"] == "SEALED-CB"


def test_sealer_receives_the_plaintext_answer(monkeypatch):
    stub_cv(monkeypatch, YidunSolver, offset=64.0)
    seen: list[dict] = []

    def sealer(envelope):
        seen.append(envelope)
        return {"data": "D", "cb": "C"}

    run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(_http(), sealer=sealer))

    assert seen[0]["plain"]["x"] == 64
    assert seen[0]["token"] == "TOK1"


def test_async_sealer_is_awaited(monkeypatch):
    stub_cv(monkeypatch, YidunSolver)

    async def sealer(envelope):
        return {"data": "AD", "cb": "AC"}

    result = run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(_http(), sealer=sealer))

    assert result.token == VALIDATE


def test_missing_sealer_escalates_before_check(monkeypatch):
    stub_cv(monkeypatch, YidunSolver)
    http = _http()

    with pytest.raises(NeedsBrowserError) as exc:
        run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(http))

    assert exc.value.context["artifact"] == "cb"
    assert http.by_method("POST") == []


def test_incomplete_sealer_output_is_unsolvable(monkeypatch):
    stub_cv(monkeypatch, YidunSolver)

    with pytest.raises(UnsolvableError, match="incomplete envelope"):
        run_http(
            YidunSolver(),
            task(CaptchaType.YIDUN, _payload()),
            _ctx(_http(), sealer=lambda envelope: {"data": "only-data"}),
        )


def test_non_object_sealer_output_is_unsolvable(monkeypatch):
    stub_cv(monkeypatch, YidunSolver)

    with pytest.raises(UnsolvableError, match="non-object"):
        run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(_http(), sealer=lambda envelope: "nope"))


# ---- vendor error translation ----------------------------------------------


def test_expired_round_is_challenge_expired(monkeypatch):
    stub_cv(monkeypatch, YidunSolver)
    http = _http(**{ep.CHECK: {"result": False, "msg": "token expired"}})

    with pytest.raises(ChallengeExpiredError):
        run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(http, sealer=_sealer))


def test_vendor_code_2306_is_challenge_expired(monkeypatch):
    stub_cv(monkeypatch, YidunSolver)
    http = _http(**{ep.CHECK: {"result": False, "code": "2306", "msg": "busy"}})

    with pytest.raises(ChallengeExpiredError):
        run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(http, sealer=_sealer))


def test_failed_check_without_validate_is_retryable(monkeypatch):
    stub_cv(monkeypatch, YidunSolver)
    http = _http(**{ep.CHECK: {"result": False, "msg": "answer wrong"}})

    with pytest.raises(CaptchaError, match="no validate"):
        run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(http, sealer=_sealer))


def test_validate_with_a_failing_result_is_not_success(monkeypatch):
    stub_cv(monkeypatch, YidunSolver)
    http = _http(**{ep.CHECK: {"result": False, "validate": VALIDATE}})

    with pytest.raises(CaptchaError, match="failing result"):
        run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(http, sealer=_sealer))


def test_garbage_get_reply_is_not_success(monkeypatch):
    stub_cv(monkeypatch, YidunSolver)
    http = _http(**{ep.GET_CONF: Garbage()})

    with pytest.raises(CaptchaError, match="not JSON"):
        run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(http, sealer=_sealer))


def test_unreported_xss_payload_is_not_a_token(monkeypatch):
    """JSONP with a JS preamble must not be silently navigated."""
    stub_cv(monkeypatch, YidunSolver)
    http = _http(**{ep.GET_CONF: Reply(text='alert(1);__wmjsonp({"data": {}});')})

    with pytest.raises(CaptchaError):
        run_http(YidunSolver(), task(CaptchaType.YIDUN, _payload()), _ctx(http, sealer=_sealer))


# ---- browser lane -----------------------------------------------------------


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(yidun_browser, "DEFAULT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(yidun_browser, "MIN_TIMEOUT_S", 0.05)
    monkeypatch.setattr(yidun_browser, "POLL_INTERVAL_S", 0.01)


def _run_browser(provider, t=None):
    ctx = SolverContext(browser=provider)
    return asyncio.run(YidunSolver().solve_browser(t or task(CaptchaType.YIDUN, _payload()), ctx))


def test_browser_lane_reads_the_validate():
    provider = FakeProvider(FakePage([{"token": VALIDATE, "host": "shop.test", "ua": "UA/1"}]))

    result = _run_browser(provider)

    assert result.token == VALIDATE
    assert result.solve_mode is SolveMode.BROWSER
    assert provider.sessions[0].navigated == [PAGE]
    assert provider.specs[0].label.startswith("yidun:")


def test_browser_lane_ignores_too_short_tokens():
    provider = FakeProvider(FakePage([{"token": "short", "challenge_shown": True}]))

    with pytest.raises(ChallengeExpiredError) as exc:
        _run_browser(provider)
    assert exc.value.context["reason"] == "challenge_shown"


def test_browser_lane_without_provider_is_config_error():
    with pytest.raises(ConfigError, match=r"ctx\.browser"):
        _run_browser(None)


def test_browser_lane_needs_a_page_url():
    with pytest.raises(UnsolvableError, match="page the widget sits on"):
        _run_browser(FakeProvider(FakePage([{"token": VALIDATE}])), task(CaptchaType.YIDUN, {"captcha_id": "1"}))


def test_browser_result_projects_to_token_and_user_agent():
    import msgspec

    from captcha.domain.solution import build_solution

    provider = FakeProvider(FakePage([{"token": VALIDATE, "ua": "UA/2"}]))
    result = _run_browser(provider)

    solution = build_solution(CaptchaType.YIDUN, msgspec.to_builtins(result))

    assert solution["token"] == VALIDATE
    assert solution["userAgent"] == "UA/2"
