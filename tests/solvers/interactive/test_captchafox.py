"""CaptchaFox: encoded wire, CV answer shapes, proof escalation, browser lane.

No network. The vendor calls are replayed from canned JSON and the CV delegate
is stubbed. The body encoder is checked against its own decoder (the byte
layout was cross-checked with Node's zlib when the module was written); the
verify-body assertions pin the widget's own `kind=position`/`kind=taps`
answer shapes.
"""

from __future__ import annotations

import base64
from typing import Any

import msgspec
import pytest
from tests.solvers.interactive._fakes import HTTP, FakePage, FakeProvider, Garbage, Reply, make_task

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import (
    CaptchaError,
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    UnsolvableError,
)
from captcha.domain.solution import build_solution
from captcha.solvers import SolverContext
from captcha.solvers.interactive.captchafox import endpoints as ep
from captcha.solvers.interactive.captchafox import wire
from captcha.solvers.interactive.captchafox.solver import CaptchaFoxSolver

SITE_URL = "https://shop.test/login?next=/a"
SITEKEY = "sk_test_KEY"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"
TOKEN = "7828075fb55ecbd9146745ee6f2bec475b88076d36e23050f1fb28359ffca15d"
BG = base64.b64encode(b"bg-bytes").decode()
PLAYER = base64.b64encode(b"piece-bytes").decode()


def _task(payload: dict[str, Any] | None = None, **kwargs: Any) -> Task:
    return make_task(CaptchaType.CAPTCHAFOX, payload, site_url=SITE_URL, sitekey=SITEKEY, **kwargs)


def _config() -> dict[str, Any]:
    return {"h": "H-1", "theme": {"primary": "#fff"}}


def _proof_payload(**extra: Any) -> dict[str, Any]:
    return {"proof": {"cs": "CS-1", "k": "K-1"}, **extra}


def _http(*, challenge: Any = None, verify: Any = None, config: Any = None) -> HTTP:
    return HTTP(
        {
            wire.config_url(SITEKEY, SITE_URL): _config() if config is None else config,
            wire.challenge_url(SITEKEY): challenge or {"solved": True, "token": TOKEN},
            ep.VERIFY: verify or {"solved": True, "token": TOKEN},
        }
    )


def _stub_cv(monkeypatch: pytest.MonkeyPatch, result: SolverResult) -> list[tuple[CaptchaType, dict[str, Any]]]:
    calls: list[tuple[CaptchaType, dict[str, Any]]] = []

    async def fake(self: Any, kind: CaptchaType, task: Task, ctx: SolverContext, payload: dict[str, Any]):
        calls.append((kind, payload))
        return result

    monkeypatch.setattr(CaptchaFoxSolver, "_solve_cv", fake)
    return calls


def _posted_body(http: HTTP, url: str) -> dict[str, Any]:
    return wire.decode_body(http.call("POST", url)["data"])


# ---- metadata ---------------------------------------------------------------


def test_metadata() -> None:
    assert CaptchaFoxSolver.info.types == (CaptchaType.CAPTCHAFOX,)
    assert CaptchaFoxSolver.info.http is True
    assert CaptchaFoxSolver.info.browser is True
    assert CaptchaFoxSolver.info.experimental is True


# ---- wire -------------------------------------------------------------------


def test_body_roundtrip() -> None:
    body = {"lng": "en", "h": "H", "cs": "C", "host": "shop.test", "k": {"v": 2, "proof": "P"}}
    assert wire.decode_body(wire.encode_body(body)) == body


def test_body_has_the_widget_header() -> None:
    import gzip

    encoded = wire.encode_body({"a": 1})
    assert encoded[:2] == b"\x01\x04"
    raw = bytes(value ^ (index + 4) for index, value in enumerate(encoded[2:]))
    assert gzip.decompress(raw) == b'{"a":1}'


def test_answer_shapes() -> None:
    slide = wire.answer_from_cv("slide", SolverResult(offset=100.0), {"w": 500})
    assert slide == {"kind": "position", "position": 50.0}
    taps = wire.answer_from_cv("select", SolverResult(coords=[(10.0, 20.0), (400.0, 300.0)]), {"w": 320, "h": 240})
    assert taps == {"kind": "taps", "taps": [{"x": 10.0, "y": 20.0}, {"x": 320.0, "y": 240.0}]}
    assert wire.answer_from_cv("attest", SolverResult(), {}) == {}


def test_answer_without_geometry_is_retryable() -> None:
    with pytest.raises(ChallengeExpiredError, match="no points"):
        wire.answer_from_cv("select", SolverResult(), {})


# ---- payload validation -----------------------------------------------------


async def test_missing_sitekey_is_unsolvable() -> None:
    task = make_task(CaptchaType.CAPTCHAFOX, {}, site_url=SITE_URL)
    with pytest.raises(UnsolvableError, match="sitekey"):
        await CaptchaFoxSolver().solve_http(task, SolverContext(http=HTTP({})))


async def test_missing_url_is_unsolvable() -> None:
    task = make_task(CaptchaType.CAPTCHAFOX, {}, sitekey=SITEKEY)
    with pytest.raises(UnsolvableError, match="websiteURL"):
        await CaptchaFoxSolver().solve_http(task, SolverContext(http=HTTP({})))


async def test_missing_http_client_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        await CaptchaFoxSolver().solve_http(_task(), SolverContext())


# ---- HTTP lane --------------------------------------------------------------


async def test_config_only_escalates_when_proof_is_missing() -> None:
    http = _http()
    with pytest.raises(NeedsBrowserError, match="__cf_wapi") as exc:
        await CaptchaFoxSolver().solve_http(_task(), SolverContext(http=http))
    assert exc.value.context["sitekey"] == SITEKEY
    assert http.calls == [("GET", wire.config_url(SITEKEY, SITE_URL), {"headers": wire.headers()})]


async def test_challenge_solved_directly_skips_cv() -> None:
    http = _http()
    result = await CaptchaFoxSolver().solve_http(_task(_proof_payload()), SolverContext(http=http))
    assert result.token == TOKEN
    assert result.extra == {}


async def test_slide_flow_uses_position_and_widget_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_cv(monkeypatch, SolverResult(offset=100.0))
    http = _http(
        challenge={
            "type": "slide",
            "challenge": {"bg": BG, "player": PLAYER, "w": 500},
            "j": {"x": 1},
            "ttl": 60,
        }
    )
    result = await CaptchaFoxSolver().solve_http(_task(_proof_payload()), SolverContext(http=http))

    assert result.token == TOKEN
    assert calls[0][0] is CaptchaType.SLIDER
    assert calls[0][1]["bg"] == BG
    assert calls[0][1]["piece"] == PLAYER

    challenge_call = http.call("POST", wire.challenge_url(SITEKEY))
    assert challenge_call["headers"]["Content-Type"] == "text/plain"
    assert challenge_call["headers"]["X-Pulse"] == ep.X_PULSE
    body = _posted_body(http, wire.challenge_url(SITEKEY))
    assert body["cs"] == "CS-1"
    assert body["k"] == {"v": 2, "proof": "K-1"}
    assert body["h"] == "H-1"
    assert body["host"] == "shop.test"
    assert body["lng"] == "en"

    verify = _posted_body(http, ep.VERIFY)
    assert verify["sk"] == SITEKEY
    assert verify["kind"] == "position"
    assert verify["position"] == 50.0
    assert verify["type"] == "slide"
    assert verify["cs"] == "CS-1"
    assert verify["mv"] == []
    assert "k" not in verify or verify["k"] == {"v": 2, "proof": "K-1"}


async def test_select_flow_sends_taps(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, SolverResult(coords=[(12.0, 34.0)]))
    http = _http(
        challenge={
            "type": "select",
            "challenge": {"bg": BG, "w": 300, "h": 200, "prompt": "crosswalk"},
        }
    )
    await CaptchaFoxSolver().solve_http(_task(_proof_payload()), SolverContext(http=http))

    verify = _posted_body(http, ep.VERIFY)
    assert verify["kind"] == "taps"
    assert verify["taps"] == [{"x": 12.0, "y": 34.0}]
    assert verify["type"] == "select"


async def test_attest_flow_needs_no_cv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_cv(monkeypatch, SolverResult())
    http = _http(challenge={"type": "attest", "challenge": {}})
    await CaptchaFoxSolver().solve_http(_task(_proof_payload()), SolverContext(http=http))
    assert calls == []
    assert _posted_body(http, ep.VERIFY)["type"] == "attest"


async def test_audio_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, SolverResult())
    http = _http(challenge={"type": "audio", "challenge": {"audio": "u"}})
    with pytest.raises(CaptchaError, match="audio"):
        await CaptchaFoxSolver().solve_http(_task(_proof_payload()), SolverContext(http=http))


async def test_failed_challenge_is_retryable() -> None:
    http = _http(challenge={"failed": True})
    with pytest.raises(ChallengeExpiredError, match="session failed"):
        await CaptchaFoxSolver().solve_http(_task(_proof_payload()), SolverContext(http=http))


async def test_verify_without_token_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_cv(monkeypatch, SolverResult(offset=10.0))
    http = _http(
        challenge={"type": "slide", "challenge": {"bg": BG, "player": PLAYER}},
        verify={"type": "select", "challenge": {}},
    )
    with pytest.raises(ChallengeExpiredError, match="another challenge"):
        await CaptchaFoxSolver().solve_http(_task(_proof_payload()), SolverContext(http=http))


async def test_non_json_reply_is_retryable() -> None:
    http = HTTP({wire.config_url(SITEKEY, SITE_URL): Garbage()})
    with pytest.raises(ChallengeExpiredError, match="non-JSON"):
        await CaptchaFoxSolver().solve_http(_task(), SolverContext(http=http))


async def test_config_error_status_is_retryable() -> None:
    http = HTTP({wire.config_url(SITEKEY, SITE_URL): Reply(status=503)})
    with pytest.raises(ChallengeExpiredError, match="HTTP 503"):
        await CaptchaFoxSolver().solve_http(_task(), SolverContext(http=http))


async def test_solution_projects_to_token_and_ua() -> None:
    http = _http(challenge={"solved": True, "token": TOKEN})
    task = _task(_proof_payload(), user_agent=UA)
    result = await CaptchaFoxSolver().solve_http(task, SolverContext(http=http))
    assert build_solution(CaptchaType.CAPTCHAFOX, msgspec.structs.asdict(result)) == {
        "token": TOKEN,
        "userAgent": UA,
    }


# ---- browser lane -----------------------------------------------------------


async def _solve_browser(page: FakePage, *, task: Task | None = None, provider: FakeProvider | None = None):
    return await CaptchaFoxSolver().solve_browser(
        task or _task({"websiteURL": SITE_URL}),
        SolverContext(browser=provider or FakeProvider(page), extra={"timeout_s": 0.1}),
    )


async def test_browser_returns_widget_token_and_page_ua() -> None:
    page = FakePage({"cf-captcha-response": TOKEN, "location.hostname": "shop.test"}, ua=UA)
    provider = FakeProvider(page)
    result = await _solve_browser(page, provider=provider)

    assert result.token == TOKEN
    assert result.extra == {"userAgent": UA}
    assert result.solve_mode.value == "browser"
    assert provider.sessions[0].gotos[0][0] == SITE_URL
    assert provider.specs[0].label.startswith("captchafox:captchafox:")


async def test_browser_missing_token_is_retryable() -> None:
    page = FakePage({}, ua=UA)
    with pytest.raises(ChallengeExpiredError, match="no token"):
        await _solve_browser(page)
    assert page.clicks  # the widget got a nudge before giving up


async def test_browser_wrong_origin_is_unsolvable() -> None:
    page = FakePage({"cf-captcha-response": TOKEN, "location.hostname": "evil.test"}, ua=UA)
    with pytest.raises(UnsolvableError, match="origin"):
        await _solve_browser(page)
