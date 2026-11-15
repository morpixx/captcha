"""atbCaptcha: challenge shape, the sealed answer, and both lanes.

Slide/click geometry is trivial here; what the tests pin down is that the answer
is never posted unsealed (the vendor reads that as a wrong answer), that the
sealer contract is honoured for sync/async/dict producers, and that the browser
lane reports "challenge still up" instead of a fabricated verification.
"""

from __future__ import annotations

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
from captcha.solvers.interactive.atbcaptcha import browser as atb_browser
from captcha.solvers.interactive.atbcaptcha import endpoints as ep
from captcha.solvers.interactive.atbcaptcha import wire
from captcha.solvers.interactive.atbcaptcha.solver import AtbCaptchaSolver

PIC = "https://cap.test/pic.png"
BLOCK = "https://cap.test/block.png"
PAGE = "https://shop.test/login"
CHALLENGE = {"data": {"token": "TOK1", "pic": PIC, "blockPic": BLOCK}}
CHECK_OK = {"success": True, "data": {"captchaVerification": "CV-1"}}


def _payload(**extra):
    return {"captcha_id": "CID1", "pageurl": PAGE, **extra}


def _http(**overrides):
    replies = {
        ep.GET_CONF: CHALLENGE,
        PIC: Reply(body=b"pic"),
        BLOCK: Reply(body=b"block"),
        ep.VERIFY: CHECK_OK,
    }
    replies.update(overrides)
    return HTTP(replies)


def _ctx(http, *, sealed="SEALED-DATA"):
    extra = {"atb_sealer": lambda envelope: sealed} if sealed is not None else {}
    return SolverContext(http=http, extra=extra)


# ---- metadata ---------------------------------------------------------------


def test_metadata_declares_both_lanes():
    assert AtbCaptchaSolver.info.types == (CaptchaType.ATBCAPTCHA,)
    assert AtbCaptchaSolver.info.http is True
    assert AtbCaptchaSolver.info.browser is True
    assert AtbCaptchaSolver.info.experimental is True


# ---- payload / configuration ------------------------------------------------


def test_missing_captcha_id_is_unsolvable():
    with pytest.raises(UnsolvableError, match="captcha_id"):
        run_http(AtbCaptchaSolver(), task(CaptchaType.ATBCAPTCHA, {}), SolverContext())


def test_missing_http_client_is_config_error():
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        run_http(AtbCaptchaSolver(), task(CaptchaType.ATBCAPTCHA, _payload()), SolverContext())


# ---- wire codecs ------------------------------------------------------------


def test_parse_reply_accepts_json_and_rejects_garbage():
    assert wire.parse_reply('{"data": {}}') == {"data": {}}
    with pytest.raises(CaptchaError, match="not JSON"):
        wire.parse_reply("<html>blocked</html>")
    with pytest.raises(CaptchaError, match="JSON object"):
        wire.parse_reply("[1, 2]")


def test_parse_challenge_folds_nested_data_and_picks_the_kind():
    challenge = wire.parse_challenge(CHALLENGE)

    assert challenge["token"] == "TOK1"
    assert challenge["pic"] == PIC
    assert challenge["block"] == BLOCK
    assert challenge["kind"] is CaptchaType.SLIDER


def test_parse_challenge_without_a_block_is_a_click_round():
    challenge = wire.parse_challenge({"token": "TOK", "pic": PIC})
    assert challenge["block"] is None
    assert challenge["kind"] is CaptchaType.CLICK


def test_parse_challenge_requires_token_and_picture():
    with pytest.raises(UnsolvableError, match="token/pic"):
        wire.parse_challenge({"pic": PIC})


def test_answer_plaintext_shapes_both_kinds():
    slider = wire.answer_plaintext(SolverResult(offset=88.6), CaptchaType.SLIDER, started=0.0)
    assert slider["x"] == 89
    assert isinstance(slider["t"], int)

    click = wire.answer_plaintext(SolverResult(coords=[(1.4, 2.6)]), CaptchaType.CLICK, started=0.0)
    assert click["pts"] == [[1, 3]]


def test_click_answer_without_points_is_unsolvable():
    with pytest.raises(UnsolvableError, match="no points"):
        wire.answer_plaintext(SolverResult(coords=[]), CaptchaType.CLICK, started=0.0)


def test_check_body_refuses_an_empty_seal():
    with pytest.raises(UnsolvableError, match="empty data"):
        wire.check_body("CID1", "TOK", "", type_=ep.TYPE_SLIDER)


def test_parse_check_returns_the_verification():
    assert wire.parse_check(CHECK_OK) == "CV-1"


def test_expired_round_is_challenge_expired():
    with pytest.raises(ChallengeExpiredError):
        wire.parse_check({"success": False, "msg": "token expired"})


def test_verification_with_success_false_is_not_success():
    with pytest.raises(CaptchaError, match="success=false"):
        wire.parse_check({"success": False, "data": {"captchaVerification": "CV"}})


def test_missing_verification_is_retryable():
    with pytest.raises(CaptchaError, match="no captchaVerification"):
        wire.parse_check({"success": True, "data": {}})


# ---- HTTP flow --------------------------------------------------------------


def test_flow_posts_the_sealed_answer_and_returns_the_verification(monkeypatch):
    calls = stub_cv(monkeypatch, AtbCaptchaSolver, offset=101.2)
    http = _http()

    result = run_http(AtbCaptchaSolver(), task(CaptchaType.ATBCAPTCHA, _payload()), _ctx(http))

    assert result.token == "CV-1"
    assert result.solve_mode is SolveMode.HTTP
    assert result.extra["captcha_id"] == "CID1"
    assert calls[0][0] is CaptchaType.SLIDER
    assert calls[0][1] == {"bg": b"pic", "piece": b"block"}

    params = http.calls[0][2]["params"]
    assert params["captchaId"] == "CID1"
    assert params["type"] == ep.TYPE_SLIDER

    body = http.by_method("POST")[0][2]["json"]
    assert body["captchaId"] == "CID1"
    assert body["token"] == "TOK1"
    assert body["data"] == "SEALED-DATA"
    assert body["type"] == ep.TYPE_SLIDER


def test_the_plaintext_answer_never_reaches_the_wire(monkeypatch):
    """Posting the geometry instead of the seal is the failure this guards."""
    captured = {}
    stub_cv(monkeypatch, AtbCaptchaSolver, offset=42.0)
    http = _http()

    def sealer(envelope):
        captured.update(envelope)
        return "SEALED"

    ctx = SolverContext(http=http, extra={"atb_sealer": sealer})
    run_http(AtbCaptchaSolver(), task(CaptchaType.ATBCAPTCHA, _payload()), ctx)

    assert captured["plain"] == {"x": 42, "t": captured["plain"]["t"]}
    body = http.by_method("POST")[0][2]["json"]
    assert body["data"] == "SEALED"
    assert "42" not in body["data"]


def test_missing_sealer_escalates_before_the_check_post(monkeypatch):
    stub_cv(monkeypatch, AtbCaptchaSolver)
    http = _http()

    with pytest.raises(NeedsBrowserError) as exc:
        run_http(AtbCaptchaSolver(), task(CaptchaType.ATBCAPTCHA, _payload()), _ctx(http, sealed=None))

    assert exc.value.context["artifact"] == "data"
    assert http.by_method("POST") == []


def test_async_dict_sealer_is_accepted(monkeypatch):
    stub_cv(monkeypatch, AtbCaptchaSolver)
    http = _http()

    async def sealer(envelope):
        return {"data": "ASYNC-SEAL"}

    ctx = SolverContext(http=http, extra={"atb_sealer": sealer})
    run_http(AtbCaptchaSolver(), task(CaptchaType.ATBCAPTCHA, _payload()), ctx)

    assert http.by_method("POST")[0][2]["json"]["data"] == "ASYNC-SEAL"


def test_empty_sealer_output_is_unsolvable(monkeypatch):
    stub_cv(monkeypatch, AtbCaptchaSolver)

    with pytest.raises(UnsolvableError, match="empty data"):
        run_http(AtbCaptchaSolver(), task(CaptchaType.ATBCAPTCHA, _payload()), _ctx(_http(), sealed=""))


def test_click_round_uses_the_click_answer(monkeypatch):
    calls = stub_cv(monkeypatch, AtbCaptchaSolver, coords=[(5.0, 6.0)])
    http = _http(**{ep.GET_CONF: {"token": "TOK", "pic": PIC}})

    result = run_http(AtbCaptchaSolver(), task(CaptchaType.ATBCAPTCHA, _payload()), _ctx(http))

    assert result.extra["cv_kind"] == CaptchaType.CLICK.value
    assert calls[0][0] is CaptchaType.CLICK
    assert calls[0][1] == {"bg": b"pic"}
    assert not any(call[1] == BLOCK for call in http.calls)


def test_garbage_challenge_is_not_success(monkeypatch):
    stub_cv(monkeypatch, AtbCaptchaSolver)

    with pytest.raises(CaptchaError, match="not JSON"):
        run_http(AtbCaptchaSolver(), task(CaptchaType.ATBCAPTCHA, _payload()), _ctx(_http(**{ep.GET_CONF: Garbage()})))


def test_rejected_check_is_not_success(monkeypatch):
    stub_cv(monkeypatch, AtbCaptchaSolver)
    http = _http(**{ep.VERIFY: {"success": False, "code": "4001", "msg": "wrong answer"}})

    with pytest.raises(CaptchaError, match="no captchaVerification"):
        run_http(AtbCaptchaSolver(), task(CaptchaType.ATBCAPTCHA, _payload()), _ctx(http))


# ---- browser lane -----------------------------------------------------------


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(atb_browser, "DEFAULT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(atb_browser, "MIN_TIMEOUT_S", 0.05)
    monkeypatch.setattr(atb_browser, "POLL_INTERVAL_S", 0.01)


def _run_browser(provider, t=None):
    import asyncio

    ctx = SolverContext(browser=provider)
    return asyncio.run(AtbCaptchaSolver().solve_browser(t or task(CaptchaType.ATBCAPTCHA, _payload()), ctx))


def test_browser_lane_reads_the_verification():
    provider = FakeProvider(FakePage([{"token": "CV-WEB-1234567890", "host": "shop.test", "ua": "UA/1"}]))

    result = _run_browser(provider)

    assert result.token == "CV-WEB-1234567890"
    assert result.solve_mode is SolveMode.BROWSER
    assert result.extra["path"] == "browser"
    assert provider.sessions[0].navigated == [PAGE]
    assert provider.specs[0].label.startswith("atbcaptcha:")


def test_visible_challenge_without_a_verification_is_challenge_expired():
    provider = FakeProvider(FakePage([{"token": "", "challenge_shown": True}]))

    with pytest.raises(ChallengeExpiredError) as exc:
        _run_browser(provider)
    assert exc.value.context["reason"] == "challenge_shown"


def test_short_string_is_not_accepted_as_a_verification():
    provider = FakeProvider(FakePage([{"token": "short"}]))

    with pytest.raises(ChallengeExpiredError):
        _run_browser(provider)


def test_browser_lane_without_provider_is_config_error():
    with pytest.raises(ConfigError, match=r"ctx\.browser"):
        _run_browser(None)


def test_browser_lane_needs_a_page_url():
    t = task(CaptchaType.ATBCAPTCHA, {"captcha_id": "CID1"})
    with pytest.raises(UnsolvableError, match="page the widget sits on"):
        _run_browser(FakeProvider(FakePage([{"token": "x"}])), t)


def test_browser_lane_rejects_non_http_page_urls():
    t = task(CaptchaType.ATBCAPTCHA, {"captcha_id": "CID1", "pageurl": "file:///etc/passwd"})
    with pytest.raises(UnsolvableError, match="http"):
        _run_browser(FakeProvider(FakePage([{"token": "x"}])), t)
