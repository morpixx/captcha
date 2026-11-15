"""Unit tests for the Alibaba/Aliyun solver: JSONP path and 2.0 browser lane."""

from __future__ import annotations

import asyncio
import json

import pytest
from vendor_fakes import FakeBrowser, FakeHTTP, FakePage, FakeSession, Reply, builtins, make_task, stub_cv

from captcha.domain import CaptchaType, SolverResult
from captcha.domain.errors import (
    CaptchaError,
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    UnsolvableError,
)
from captcha.domain.solution import build_solution
from captcha.solvers import SolverContext
from captcha.solvers.interactive.alibaba import browser as alibaba_browser
from captcha.solvers.interactive.alibaba import endpoints as ep
from captcha.solvers.interactive.alibaba import wire as aw
from captcha.solvers.interactive.alibaba.solver import AlibabaSolver

BG = "https://img.test/ali-bg.png"
FRONT = "https://img.test/ali-front.png"
PAGE = "https://shop.test/login"

GET_OK = (
    'cb({"result":{"code":100,"msg":"success","data":{'
    '"csessionid":"S-1","sig":"SIG-1","token":"T-1","scene":"SC-1",'
    f'"bg":"{BG}","front":"{FRONT}","type":"slide"'
    "}}})"
)
ANALYZE_OK = 'cb({"result":{"code":100,"msg":"success","data":{"nc_sid":"SID-1","nc_token":"NCT-1"}}})'


def _replies(get_text: str = GET_OK, analyze_text: str = ANALYZE_OK) -> dict[str, object]:
    return {
        ep.UMID_ENDPOINT: Reply({"tn": "UMID-1"}),
        ep.NC_GET: Reply(text=get_text),
        BG: Reply(body=b"bg"),
        FRONT: Reply(body=b"front"),
        ep.NC_ANALYZE: Reply(text=analyze_text),
    }


def _task(payload: dict[str, object]) -> object:
    return make_task(CaptchaType.ALIBABA, payload, site_url=PAGE, user_agent="UA/1")


def _solve(payload: dict[str, object], http: FakeHTTP) -> SolverResult:
    return asyncio.run(AlibabaSolver().solve_http(_task(payload), SolverContext(http=http)))


def test_jsonp_flow_returns_sid_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_cv(monkeypatch, AlibabaSolver, SolverResult(offset=88.0))
    http = FakeHTTP(_replies())
    result = _solve({"appkey": "APP-1", "scene": "SC-1"}, http)

    assert result.token == "NCT-1"
    assert result.extra["data"] == {"nc_sid": "SID-1", "nc_token": "NCT-1"}
    assert calls[0][0] is CaptchaType.SLIDER

    umid_call = next(call for call in http.calls if call[1] == ep.UMID_ENDPOINT)[2]
    assert umid_call["json"]["scene"] == "SC-1"
    get_call = next(call for call in http.calls if call[1] == ep.NC_GET)[2]
    assert get_call["params"]["umidToken"] == "UMID-1"
    assert get_call["params"]["appkey"] == "APP-1"
    assert get_call["params"]["scene"] == "SC-1"

    analyze_call = next(call for call in http.calls if call[1] == ep.NC_ANALYZE)[2]
    form = analyze_call["data"]
    assert form["csessionid"] == "S-1"
    assert form["sig"] == "SIG-1"
    assert form["token"] == "T-1"
    assert form["appkey"] == "APP-1"
    assert json.loads(form["answer"]) == {"x": 88}
    assert len(json.loads(form["w"])) > 2

    solution = build_solution(CaptchaType.ALIBABA, builtins(result))
    assert solution is not None
    assert solution["data"]["nc_sid"] == "SID-1"
    assert solution["data"]["nc_token"] == "NCT-1"


def test_rotate_variant_uses_the_rotate_primitive(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_cv(monkeypatch, AlibabaSolver, SolverResult(angle=42.0))
    get_text = GET_OK.replace('"type":"slide"', '"type":"rotate"').replace(',"front":"' + FRONT + '"', "")
    http = FakeHTTP(_replies(get_text=get_text))
    _solve({"appkey": "APP-1", "scene": "SC-1", "umid_token": "U"}, http)
    assert calls[0][0] is CaptchaType.ROTATE
    form = next(call for call in http.calls if call[1] == ep.NC_ANALYZE)[2]["data"]
    assert json.loads(form["answer"]) == {"angle": 42.0}


def test_click_variant_without_target_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, AlibabaSolver, SolverResult(coords=[(1.0, 2.0)]))
    get_text = (
        'cb({"result":{"code":100,"data":{"csessionid":"S","sig":"G","token":"T","scene":"SC",'
        f'"bg":"{BG}","type":"click"'
        "}}})"
    )
    http = FakeHTTP({ep.NC_GET: Reply(text=get_text), BG: Reply(body=b"bg")})
    with pytest.raises(UnsolvableError, match="target text"):
        _solve({"appkey": "APP-1", "scene": "SC-1", "umid_token": "U"}, http)


def test_v2_widget_without_appkey_scene_asks_for_browser() -> None:
    with pytest.raises(NeedsBrowserError):
        _solve({"pageurl": PAGE}, FakeHTTP({}))


def test_no_appkey_scene_and_no_page_is_unsolvable() -> None:
    task = make_task(CaptchaType.ALIBABA, {})
    with pytest.raises(UnsolvableError, match="appkey"):
        asyncio.run(AlibabaSolver().solve_http(task, SolverContext(http=FakeHTTP({}))))


def test_missing_http_client_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        asyncio.run(AlibabaSolver().solve_http(_task({"appkey": "A", "scene": "S"}), SolverContext()))


@pytest.mark.parametrize("status", [401, 403, 410, 429])
def test_stale_status_is_challenge_expired(status: int) -> None:
    http = FakeHTTP({ep.NC_GET: Reply(status=status, body=b"")})
    with pytest.raises(ChallengeExpiredError):
        _solve({"appkey": "A", "scene": "S", "umid_token": "U"}, http)


def test_expired_result_code_is_challenge_expired() -> None:
    http = FakeHTTP({ep.NC_GET: Reply(text='cb({"result":{"code":400,"msg":"challenge expired"}})')})
    with pytest.raises(ChallengeExpiredError):
        _solve({"appkey": "A", "scene": "S", "umid_token": "U"}, http)


def test_non_100_result_code_is_unsolvable() -> None:
    http = FakeHTTP({ep.NC_GET: Reply(text='cb({"result":{"code":500,"msg":"busy"}})')})
    with pytest.raises(UnsolvableError, match="refused"):
        _solve({"appkey": "A", "scene": "S", "umid_token": "U"}, http)


def test_analyze_without_result_values_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, AlibabaSolver, SolverResult(offset=10.0))
    http = FakeHTTP(_replies(analyze_text='cb({"result":{"code":100,"data":{}}})'))
    with pytest.raises(UnsolvableError, match="neither"):
        _solve({"appkey": "A", "scene": "S", "umid_token": "U"}, http)


def test_jsonp_parser_accepts_bare_json_and_rejects_garbage() -> None:
    assert aw.jsonp_object('{"result": {"code": 100}}', "x")["result"]["code"] == 100
    with pytest.raises(CaptchaError):
        aw.jsonp_object("no json here", "x")
    with pytest.raises(CaptchaError):
        aw.jsonp_object("[1, 2]", "x")


# ---- Captcha 2.0 browser lane ----------------------------------------------


async def test_browser_lane_reads_captcha_verify_param(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alibaba_browser, "POLL_INTERVAL_S", 0)
    page = FakePage(
        [
            {
                "param": "",
                "sceneId": "SC-1",
                "host": "shop.test",
                "ua": "UA/9",
                "widget": True,
            },
            {
                "param": "PARAM-1234567890",
                "sceneId": "SC-1",
                "host": "shop.test",
                "ua": "UA/9",
                "widget": True,
            },
        ]
    )
    browser = FakeBrowser(FakeSession(page))
    task = make_task(CaptchaType.ALIBABA, {"pageurl": PAGE})
    result = await AlibabaSolver().solve_browser(task, SolverContext(browser=browser))

    assert result.token == "PARAM-1234567890"
    assert result.extra["data"]["CaptchaVerifyParam"] == "PARAM-1234567890"
    assert result.extra["data"]["sceneId"] == "SC-1"
    assert browser.specs[0].label.startswith("alibaba:")
    solution = build_solution(CaptchaType.ALIBABA, builtins(result))
    assert solution["data"]["CaptchaVerifyParam"] == "PARAM-1234567890"


async def test_browser_lane_times_out_without_param(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alibaba_browser, "_timeout_s", lambda task: 0.0)
    browser = FakeBrowser(FakeSession(FakePage([{"param": "", "widget": True}])))
    task = make_task(CaptchaType.ALIBABA, {"pageurl": PAGE})
    with pytest.raises(ChallengeExpiredError, match="CaptchaVerifyParam"):
        await AlibabaSolver().solve_browser(task, SolverContext(browser=browser))


async def test_browser_lane_needs_a_provider() -> None:
    task = make_task(CaptchaType.ALIBABA, {"pageurl": PAGE})
    with pytest.raises(ConfigError, match=r"ctx\.browser"):
        await AlibabaSolver().solve_browser(task, SolverContext())


async def test_browser_lane_needs_a_page() -> None:
    browser = FakeBrowser(FakeSession(FakePage([{"param": "x"}])))
    task = make_task(CaptchaType.ALIBABA, {})
    with pytest.raises(UnsolvableError, match="page"):
        await AlibabaSolver().solve_browser(task, SolverContext(browser=browser))
