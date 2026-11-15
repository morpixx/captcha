"""CutCaptcha: challenge parsing, the pointer track, and verify translation."""

from __future__ import annotations

import json

import numpy as np
import pytest
from _vendors import HTTP, Garbage, Reply, run_http, stub_cv, task

from captcha.domain import CaptchaType
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.solvers import SolverContext
from captcha.solvers.interactive.cutcaptcha import endpoints as ep
from captcha.solvers.interactive.cutcaptcha import wire
from captcha.solvers.interactive.cutcaptcha.solver import CutCaptchaSolver

BG = "https://cdn.test/bg.jpg"
PIECE = "https://cdn.test/piece.png"
PAGE = "https://shop.test/signup"
CHALLENGE = {"cid": "CID1", "bgUrl": BG, "pieceUrl": PIECE}


def _payload(**extra):
    return {"apikey": "APIKEY1", "pageurl": PAGE, **extra}


def _http(**overrides):
    replies = {
        ep.GET_CHALLENGE: CHALLENGE,
        BG: Reply(body=b"bg"),
        PIECE: Reply(body=b"piece"),
        ep.VERIFY: {"token": "T0KEN"},
    }
    replies.update(overrides)
    return HTTP(replies)


def _ctx(http):
    return SolverContext(http=http)


def test_metadata_is_http_only():
    assert CutCaptchaSolver.info.types == (CaptchaType.CUTCAPTCHA,)
    assert CutCaptchaSolver.info.http is True
    assert CutCaptchaSolver.info.browser is False


def test_missing_apikey_is_unsolvable():
    with pytest.raises(UnsolvableError, match="apikey"):
        run_http(CutCaptchaSolver(), task(CaptchaType.CUTCAPTCHA, {}), SolverContext())


def test_missing_http_client_is_config_error():
    with pytest.raises(ConfigError, match=r"ctx\.http"):
        run_http(CutCaptchaSolver(), task(CaptchaType.CUTCAPTCHA, _payload()), SolverContext())


def test_parse_reply_rejects_garbage():
    with pytest.raises(CaptchaError, match="not JSON"):
        wire.parse_reply("<html>nope</html>")
    with pytest.raises(CaptchaError, match="JSON object"):
        wire.parse_reply("[1]")


def test_parse_challenge_accepts_nested_data():
    assert wire.parse_challenge({"data": CHALLENGE})["cid"] == "CID1"


def test_parse_challenge_requires_all_three_fields():
    with pytest.raises(UnsolvableError, match="cid/bg/piece"):
        wire.parse_challenge({"cid": "C", "bgUrl": BG})


def test_drag_track_is_human_shaped_and_long_enough():
    track = wire.drag_track(150.0, points=4, rng=np.random.default_rng(7))

    assert len(track) == ep.MIN_TRACK_POINTS
    assert track[0]["x"] == 0.0
    assert track[-1]["x"] == 150.0
    assert all(set(point) == {"x", "y", "t"} for point in track)
    assert [point["t"] for point in track] == sorted(point["t"] for point in track)
    assert len({(point["x"], point["y"]) for point in track}) > ep.MIN_TRACK_POINTS // 2


def test_drag_track_is_seed_stable():
    first = wire.drag_track(90.0, rng=np.random.default_rng(3))
    second = wire.drag_track(90.0, rng=np.random.default_rng(3))
    assert first == second


@pytest.mark.parametrize(
    ("reply", "expected"), [({"token": "T"}, "T"), ({"response": "R"}, "R"), ({"data": {"token": "N"}}, "N")]
)
def test_parse_verify_accepts_the_three_token_shapes(reply, expected):
    assert wire.parse_verify(reply) == expected


def test_expired_challenge_is_challenge_expired():
    with pytest.raises(ChallengeExpiredError):
        wire.parse_verify({"error": "challenge expired"})
    with pytest.raises(ChallengeExpiredError):
        wire.parse_verify({"code": "expired"})


def test_token_with_a_failure_flag_is_not_success():
    with pytest.raises(CaptchaError, match="failure flag"):
        wire.parse_verify({"token": "T", "success": False})


def test_missing_token_is_retryable():
    with pytest.raises(CaptchaError, match="no token"):
        wire.parse_verify({"error": "answer rejected"})


def test_flow_posts_the_offset_and_the_track(monkeypatch):
    calls = stub_cv(monkeypatch, CutCaptchaSolver, offset=142.4)
    http = _http()

    result = run_http(CutCaptchaSolver(), task(CaptchaType.CUTCAPTCHA, _payload()), _ctx(http))

    assert result.token == "T0KEN"
    assert result.solve_mode is SolveMode.HTTP
    assert calls[0][0] is CaptchaType.SLIDER

    params = http.calls[0][2]["params"]
    assert params == {"apikey": "APIKEY1", "host": PAGE}

    body = http.by_method("POST")[0][2]["json"]
    assert body["cid"] == "CID1"
    assert body["offset"] == 142
    assert len(body["track"]) >= ep.MIN_TRACK_POINTS


def test_flow_rejects_a_garbage_challenge(monkeypatch):
    stub_cv(monkeypatch, CutCaptchaSolver)

    with pytest.raises(CaptchaError, match="not JSON"):
        run_http(
            CutCaptchaSolver(), task(CaptchaType.CUTCAPTCHA, _payload()), _ctx(_http(**{ep.GET_CHALLENGE: Garbage()}))
        )


def test_track_is_json_serialisable(monkeypatch):
    """The verify body is posted as JSON; numpy scalars would break encoding."""
    stub_cv(monkeypatch, CutCaptchaSolver, offset=33.0)
    http = _http()

    run_http(CutCaptchaSolver(), task(CaptchaType.CUTCAPTCHA, _payload()), _ctx(http))

    body = http.by_method("POST")[0][2]["json"]
    assert json.loads(json.dumps(body))["track"] == body["track"]
