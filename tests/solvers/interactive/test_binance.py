"""Unit tests for the Binance bCAPTCHA2 solver. No network, no live site."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from vendor_fakes import FakeHTTP, Reply, builtins, make_task, stub_cv

from captcha.crypto import aes_cbc_decrypt
from captcha.domain import CaptchaType, SolverResult
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.solvers import SolverContext
from captcha.solvers.interactive.binance import endpoints as ep
from captcha.solvers.interactive.binance import wire as bw
from captcha.solvers.interactive.binance.solver import BinanceSolver

SIG = "a" * 64
SALT = "b" * 16
BG = "https://bin.bnbstatic.com/bapi/captcha/bg.png"
PIECE = "https://bin.bnbstatic.com/bapi/captcha/piece.png"

CHALLENGE = {
    "code": "000000",
    "success": True,
    "data": {
        "sig": SIG,
        "salt": SALT,
        "validateId": "V-1",
        "captchaType": "SLIDER",
        "tag": "tag-1",
        "path2": "/bapi/captcha/bg.png",
        "path1": "/bapi/captcha/piece.png",
        "pieceWidth": 60,
    },
}

VALIDATED = {
    "code": "000000",
    "success": True,
    "data": {"token": "captcha#TOKEN-1", "sessionId": "S-1"},
}


def _replies(validate: object = VALIDATED) -> dict[str, object]:
    return {
        ep.GET_CAPTCHA: Reply(CHALLENGE),
        BG: Reply(body=b"background"),
        PIECE: Reply(body=b"piece"),
        ep.VALIDATE_CAPTCHA: Reply(validate),
    }


def _solve(payload: dict[str, object], http: FakeHTTP) -> SolverResult:
    task = make_task(CaptchaType.BINANCE, payload, site_url="https://www.binance.com/en/login")
    return asyncio.run(BinanceSolver().solve_http(task, SolverContext(http=http)))


def test_flow_encodes_track_and_returns_prefixed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_cv(
        monkeypatch,
        BinanceSolver,
        SolverResult(offset=123.0, extra={"trajectory": [[1.0, 2.0, 5], [120.0, 4.0, 90]]}),
    )
    http = FakeHTTP(_replies())
    result = _solve({"bizScene": "LOGIN"}, http)

    assert result.token == "captcha#TOKEN-1"
    assert result.extra["validateId"] == "V-1"
    assert result.extra["sessionId"] == "S-1"
    assert calls[0][0] is CaptchaType.SLIDER
    assert calls[0][1]["piece_width"] == 60
    assert calls[0][1]["bg"] == base64.b64encode(b"background").decode()

    posted = next(call for call in http.calls if call[0] == "POST" and call[1] == ep.VALIDATE_CAPTCHA)[2]
    body = posted["json"]
    assert body["securityCheckResponseValidateId"] == "V-1"
    assert body["validateId"] == "V-1"
    assert body["bizScene"] == "LOGIN"
    assert body["captchaType"] == "SLIDER"
    assert body["sig"] == SIG

    key, iv = bw.derive_keys(SIG, SALT)
    blob = json.loads(aes_cbc_decrypt(key, iv, base64.b64decode(body["data"])))
    assert blob["offset"] == 123
    assert blob["track"] == [[1.0, 2.0, 5], [120.0, 4.0, 90]]
    assert blob["bizScene"] == "LOGIN"

    solution = build_solution(CaptchaType.BINANCE, builtins(result))
    assert solution is not None
    assert solution["token"] == "captcha#TOKEN-1"


def test_prebuilt_data_is_used_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, BinanceSolver, SolverResult(offset=90.0))
    http = FakeHTTP(_replies())
    _solve({"bizScene": "LOGIN", "data": "PREBUILT-BLOB"}, http)
    posted = next(call for call in http.calls if call[0] == "POST" and call[1] == ep.VALIDATE_CAPTCHA)[2]
    assert posted["json"]["data"] == "PREBUILT-BLOB"


def test_headers_carry_fingerprint_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, BinanceSolver, SolverResult(offset=90.0))
    http = FakeHTTP(_replies())
    _solve(
        {
            "bizScene": "LOGIN",
            "bnc_uuid": "uuid-1",
            "csrftoken": "csrf-1",
            "device_info": "device-1",
            "captcha_sdk_version": "1.2.3",
            "x_captcha_se": "se-1",
            "user_agent": "UA/1",
        },
        http,
    )
    get_call = next(call for call in http.calls if call[0] == "POST" and call[1] == ep.GET_CAPTCHA)[2]
    headers = get_call["headers"]
    assert headers["bnc-uuid"] == "uuid-1"
    assert headers["csrftoken"] == "csrf-1"
    assert headers["device-info"] == "device-1"
    assert headers["captcha-sdk-version"] == "1.2.3"
    assert headers["x-captcha-se"] == "se-1"
    assert headers["user-agent"] == "UA/1"
    assert headers["referer"] == "https://www.binance.com/en/login"


def test_missing_biz_scene_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="bizScene"):
        _solve({}, FakeHTTP({}))


def test_missing_http_client_is_config_error() -> None:
    task = make_task(CaptchaType.BINANCE, {"bizScene": "LOGIN"})
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        asyncio.run(BinanceSolver().solve_http(task, SolverContext()))


@pytest.mark.parametrize("status", [401, 403, 410, 429])
def test_stale_http_status_is_challenge_expired(status: int) -> None:
    http = FakeHTTP({ep.GET_CAPTCHA: Reply(status=status, body=b"")})
    with pytest.raises(ChallengeExpiredError) as exc:
        _solve({"bizScene": "LOGIN"}, http)
    assert exc.value.context["status"] == status


def test_api_expiry_message_is_challenge_expired() -> None:
    http = FakeHTTP({ep.GET_CAPTCHA: Reply({"success": False, "code": "1001", "message": "captcha expired"})})
    with pytest.raises(ChallengeExpiredError):
        _solve({"bizScene": "LOGIN"}, http)


def test_api_refusal_is_unsolvable() -> None:
    http = FakeHTTP({ep.GET_CAPTCHA: Reply({"success": False, "code": "1002", "message": "risk rejection"})})
    with pytest.raises(UnsolvableError, match="refused"):
        _solve({"bizScene": "LOGIN"}, http)


def test_box_upgrade_is_unsolvable_not_a_slider(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, BinanceSolver, SolverResult(offset=90.0))
    box = {"success": True, "data": {"sig": SIG, "validateId": "V-2", "captchaType": "BOX", "path2": "/bg.png"}}
    http = FakeHTTP({ep.GET_CAPTCHA: Reply(box)})
    with pytest.raises(UnsolvableError, match="upgraded"):
        _solve({"bizScene": "LOGIN"}, http)


def test_missing_piece_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, BinanceSolver, SolverResult(offset=90.0))
    one_image = {
        "success": True,
        "data": {"sig": SIG, "validateId": "V-3", "captchaType": "SLIDER", "path2": "/bapi/captcha/bg.png"},
    }
    http = FakeHTTP({ep.GET_CAPTCHA: Reply(one_image), BG: Reply(body=b"bg")})
    with pytest.raises(UnsolvableError, match="one image"):
        _solve({"bizScene": "LOGIN"}, http)


def test_validate_without_token_is_challenge_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, BinanceSolver, SolverResult(offset=90.0))
    http = FakeHTTP(_replies({"success": True, "data": {}}))
    with pytest.raises(ChallengeExpiredError, match="no token"):
        _solve({"bizScene": "LOGIN"}, http)


def test_validate_expiry_envelope_is_challenge_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, BinanceSolver, SolverResult(offset=90.0))
    http = FakeHTTP(_replies({"success": False, "code": "9", "message": "challenge was consumed"}))
    with pytest.raises(ChallengeExpiredError):
        _solve({"bizScene": "LOGIN"}, http)


def test_short_sig_uses_deterministic_fallback_keys() -> None:
    key_a, iv_a = bw.derive_keys("short", "salt")
    key_b, iv_b = bw.derive_keys("short", "salt")
    assert (key_a, iv_a) == (key_b, iv_b)
    assert len(key_a) == len(iv_a) == 16


def test_data_uri_images_skip_http(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_cv(monkeypatch, BinanceSolver, SolverResult(offset=90.0))
    challenge = {
        "success": True,
        "data": {
            "sig": SIG,
            "validateId": "V-4",
            "captchaType": "SLIDER",
            "bgLink": "data:image/png;base64," + base64.b64encode(b"bg").decode(),
            "puzzleLink": "data:image/png;base64," + base64.b64encode(b"piece").decode(),
        },
    }
    http = FakeHTTP({ep.GET_CAPTCHA: Reply(challenge), ep.VALIDATE_CAPTCHA: Reply(VALIDATED)})
    result = _solve({"bizScene": "LOGIN"}, http)
    assert result.token == "captcha#TOKEN-1"
    assert [call[1] for call in http.calls].count(BG) == 0
