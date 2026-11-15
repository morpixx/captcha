"""Tencent TCaptcha: wire codecs, HTTP flow, error translation, browser lane.

Every reply here is a canned body. What is asserted is the request the solver
builds (show fallback, PoW fields, echoed show fields), the ticket projection,
and that a missing TDC fingerprint escalates instead of being posted empty.
"""

from __future__ import annotations

import pytest
from _vendors import HTTP, FakePage, FakeProvider, Garbage, Reply, run_http, stub_cv, task

from captcha.domain import CaptchaType
from captcha.domain.enums import SolveMode
from captcha.domain.errors import (
    CaptchaError,
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    SensorError,
    UnsolvableError,
)
from captcha.solvers import SolverContext
from captcha.solvers.interactive.tencent import browser as tencent_browser
from captcha.solvers.interactive.tencent import endpoints as ep
from captcha.solvers.interactive.tencent import wire
from captcha.solvers.interactive.tencent.solver import TencentSolver

BG = "https://img.test/bg.png"
PIECE = "https://img.test/piece.png"
PAGE = "https://shop.test/checkout"

PREHANDLE = {"sess": "SESS1", "subsid": 1, "tdc_path": "tdc.js"}
SHOW = {
    "cdnPic1": BG,
    "cdnPic2": PIECE,
    "pow_prefix": "abc",
    "pow_difficulty": "10",
    "vsig": "VSIG",
    "eks": "EKS",
}
VERIFY_OK = {"ret": 0, "errorCode": "0", "ticket": "t03ABC", "randstr": "@RAND"}


def _payload(**extra):
    return {"aid": "123456789", "site_url": PAGE, "pageurl": PAGE, **extra}


def _http(**overrides):
    replies = {
        ep.PREHANDLE: PREHANDLE,
        ep.SHOW_NEW: SHOW,
        BG: Reply(body=b"bg"),
        PIECE: Reply(body=b"piece"),
        ep.VERIFY_NEW: VERIFY_OK,
    }
    replies.update(overrides)
    return HTTP(replies)


def _ctx(http, *, collect="COLLECT"):
    extra = {"tencent_tdc": lambda payload: collect} if collect is not None else {}
    return SolverContext(http=http, extra=extra)


# ---- metadata ---------------------------------------------------------------


def test_metadata_declares_both_lanes():
    assert TencentSolver.info.types == (CaptchaType.TENCENT,)
    assert TencentSolver.info.http is True
    assert TencentSolver.info.browser is True
    assert TencentSolver.info.experimental is True


# ---- payload / configuration ------------------------------------------------


def test_missing_aid_is_unsolvable():
    with pytest.raises(UnsolvableError, match="aid"):
        run_http(TencentSolver(), task(CaptchaType.TENCENT, {}), SolverContext())


def test_missing_http_client_is_config_error():
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), SolverContext())


# ---- wire codecs ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        (' JSONP_17({"a": 2}); ', {"a": 2}),
        ('  {"a": 3}  ', {"a": 3}),
    ],
)
def test_parse_reply_accepts_json_and_jsonp(text, expected):
    assert wire.parse_reply(text) == expected


@pytest.mark.parametrize("text", ["<html>denied</html>", ""])
def test_parse_reply_rejects_garbage(text):
    with pytest.raises(CaptchaError) as exc:
        wire.parse_reply(text)
    assert "not JSON" in str(exc.value)


def test_parse_reply_rejects_non_objects():
    with pytest.raises(CaptchaError, match="JSON object"):
        wire.parse_reply("[1, 2]")


@pytest.mark.parametrize(("difficulty", "zeros"), [("10000", 4), ("100000", 5), ("10", 1), ("1", 0), ("", 0), ("x", 0)])
def test_pow_zeros_follow_the_zero_count(difficulty, zeros):
    assert wire.pow_zeros(difficulty) == zeros


def test_solve_pow_returns_the_counter_and_spent_time():
    answer, calc_ms = wire.solve_pow("abc", "10")
    assert answer.isdigit()
    assert calc_ms >= 0
    import hashlib

    assert hashlib.sha256(f"abc{answer}".encode()).hexdigest().startswith("0")


def test_solve_pow_without_difficulty_is_free():
    assert wire.solve_pow("abc", "1") == ("0", 0)


def test_solve_pow_gives_up_with_a_sensor_error():
    with pytest.raises(SensorError, match="did not converge"):
        wire.solve_pow("abc", "1" + "0" * 12, max_iterations=5)


def test_parse_round_needs_a_session():
    with pytest.raises(UnsolvableError, match="sess"):
        wire.parse_round({"cdnPic1": BG})


def test_parse_round_merges_show_into_prehandle():
    round_ = wire.parse_round(PREHANDLE, SHOW)
    assert round_.sess == "SESS1"
    assert round_.pic1 == BG
    assert round_.has_piece is True
    assert round_.echo["vsig"] == "VSIG"


def test_format_ans_slider_and_click():
    from captcha.domain import SolverResult

    assert wire.format_ans(SolverResult(offset=119.6), CaptchaType.SLIDER) == "120,0"
    assert wire.format_ans(SolverResult(coords=[(1.4, 2.6), (3.0, 4.0)]), CaptchaType.CLICK) == "1,3|3,4"
    with pytest.raises(UnsolvableError, match="no points"):
        wire.format_ans(SolverResult(coords=[]), CaptchaType.CLICK)


# ---- HTTP flow --------------------------------------------------------------


def test_flow_falls_back_to_show_and_posts_the_poW(monkeypatch):
    calls = stub_cv(monkeypatch, TencentSolver, offset=118.0)
    http = _http()

    result = run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(http))

    assert result.token == "t03ABC"
    assert result.extra["randstr"] == "@RAND"
    assert result.solve_mode is SolveMode.HTTP
    assert calls[0][0] is CaptchaType.SLIDER
    assert calls[0][1]["bg"] == b"bg"

    show_call = next(call for call in http.calls if call[1] == ep.SHOW_NEW)[2]
    assert show_call["params"]["sess"] == "SESS1"

    body = http.by_method("POST")[0][2]["data"]
    assert body["sess"] == "SESS1"
    assert body["ans"] == "118,0"
    assert body["collect"] == "COLLECT"
    assert body["pow_answer"].isdigit()
    assert body["vsig"] == "VSIG"
    assert body["eks"] == "EKS"


def test_flow_skips_show_when_prehandle_already_has_images(monkeypatch):
    stub_cv(monkeypatch, TencentSolver)
    prehandle = {**PREHANDLE, **SHOW}
    http = _http(**{ep.PREHANDLE: prehandle})

    run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(http))

    assert not any(call[1] == ep.SHOW_NEW for call in http.calls)


def test_click_round_uses_the_click_answer(monkeypatch):
    stub_cv(monkeypatch, TencentSolver, coords=[(11.0, 12.0), (13.0, 14.0), (15.0, 16.0)])
    prehandle = {"sess": "SESS1", "cdnPic1": BG, "pow_difficulty": "1"}

    result = run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(_http(**{ep.PREHANDLE: prehandle})))

    assert result.extra["cv_kind"] == CaptchaType.CLICK.value


def test_missing_tdc_producer_escalates_before_the_verify_post(monkeypatch):
    stub_cv(monkeypatch, TencentSolver)
    http = _http()

    with pytest.raises(NeedsBrowserError) as exc:
        run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(http, collect=None))

    assert exc.value.context["artifact"] == "collect"
    assert http.by_method("POST") == []


def test_empty_producer_result_is_a_sensor_error(monkeypatch):
    stub_cv(monkeypatch, TencentSolver)

    with pytest.raises(SensorError, match="empty collect"):
        run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(_http(), collect=""))


def test_awaitable_producer_and_dict_result_are_accepted(monkeypatch):
    stub_cv(monkeypatch, TencentSolver)

    async def producer(payload):
        return {"collect": "ASYNC-COLLECT"}

    http = _http()
    result = run_http(
        TencentSolver(),
        task(CaptchaType.TENCENT, _payload()),
        SolverContext(http=http, extra={"tencent_tdc": producer}),
    )

    assert result.token == "t03ABC"
    assert http.by_method("POST")[0][2]["data"]["collect"] == "ASYNC-COLLECT"


# ---- vendor error translation ----------------------------------------------


@pytest.mark.parametrize("code", ["9", "12"])
def test_expired_session_is_challenge_expired(monkeypatch, code):
    stub_cv(monkeypatch, TencentSolver)
    http = _http(**{ep.VERIFY_NEW: {"errorCode": code, "errorMessage": "sess expired"}})

    with pytest.raises(ChallengeExpiredError) as exc:
        run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(http))
    assert exc.value.context["errorCode"] == code


def test_rejected_collect_is_a_sensor_error(monkeypatch):
    stub_cv(monkeypatch, TencentSolver)
    http = _http(**{ep.VERIFY_NEW: {"errorCode": "7"}})

    with pytest.raises(SensorError):
        run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(http))


def test_ticket_with_a_non_zero_code_is_not_success(monkeypatch):
    stub_cv(monkeypatch, TencentSolver)
    http = _http(**{ep.VERIFY_NEW: {"errorCode": "5", "ticket": "t03BAD", "errorMessage": "answer wrong"}})

    with pytest.raises(CaptchaError, match="non-zero errorCode"):
        run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(http))


def test_undecodable_verify_reply_is_not_success(monkeypatch):
    stub_cv(monkeypatch, TencentSolver)
    http = _http(**{ep.VERIFY_NEW: Reply(text="<html>blocked</html>")})

    with pytest.raises(CaptchaError, match="not JSON"):
        run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(http))


def test_show_without_an_image_is_unsolvable(monkeypatch):
    stub_cv(monkeypatch, TencentSolver)
    http = _http(**{ep.SHOW_NEW: {"pow_prefix": "x"}})

    with pytest.raises(UnsolvableError, match="no challenge image"):
        run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(http))


def test_garbage_prehandle_reply_is_not_success(monkeypatch):
    stub_cv(monkeypatch, TencentSolver)
    http = _http(**{ep.PREHANDLE: Garbage()})

    with pytest.raises(CaptchaError, match="not JSON"):
        run_http(TencentSolver(), task(CaptchaType.TENCENT, _payload()), _ctx(http))


# ---- browser lane -----------------------------------------------------------


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(tencent_browser, "DEFAULT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(tencent_browser, "MIN_TIMEOUT_S", 0.05)
    monkeypatch.setattr(tencent_browser, "POLL_INTERVAL_S", 0.01)


def _run_browser(provider, t=None):
    import asyncio

    ctx = SolverContext(browser=provider)
    return asyncio.run(TencentSolver().solve_browser(t or task(CaptchaType.TENCENT, _payload()), ctx))


def test_browser_lane_reads_the_ticket_and_randstr():
    provider = FakeProvider(FakePage([{"ticket": "t03WEB", "randstr": "@WEB", "host": "shop.test", "ua": "UA/9"}]))

    result = _run_browser(provider)

    assert result.token == "t03WEB"
    assert result.extra["randstr"] == "@WEB"
    assert result.solve_mode is SolveMode.BROWSER
    assert provider.sessions[0].navigated == [PAGE]
    assert provider.specs[0].label.startswith("tencent:")


def test_browser_lane_waits_for_the_widget():
    provider = FakeProvider(FakePage([{"ticket": "", "challenge_shown": True}, {"ticket": "t03LATE"}]))

    result = _run_browser(provider)

    assert result.token == "t03LATE"
    assert provider.page.evaluate_calls == 2


def test_visible_challenge_without_a_ticket_is_challenge_expired():
    provider = FakeProvider(FakePage([{"ticket": "", "challenge_shown": True}]))

    with pytest.raises(ChallengeExpiredError) as exc:
        _run_browser(provider)
    assert exc.value.context["reason"] == "challenge_shown"


def test_browser_lane_without_provider_is_config_error():
    with pytest.raises(ConfigError, match=r"ctx\.browser"):
        _run_browser(None)


def test_browser_lane_needs_a_page_url():
    t = task(CaptchaType.TENCENT, {"aid": "1"})
    with pytest.raises(UnsolvableError, match="page the widget sits on"):
        _run_browser(FakeProvider(FakePage([{"ticket": "x"}])), t)


def test_browser_lane_rejects_non_http_page_urls():
    t = task(CaptchaType.TENCENT, {"aid": "1", "pageurl": "file:///etc/passwd"})
    with pytest.raises(UnsolvableError, match="http"):
        _run_browser(FakeProvider(FakePage([{"ticket": "x"}])), t)
