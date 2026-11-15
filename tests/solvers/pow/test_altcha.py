from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time

import pytest

from captcha.domain import CaptchaType, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ChallengeExpiredError, UnsolvableError
from captcha.domain.errors import TimeoutError as CaptchaTimeout
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext
from captcha.solvers.pow._generator import make_altcha_challenge
from captcha.solvers.pow.altcha import AltchaSolver


def _run(coro):
    return asyncio.run(coro)


def _make_task(payload: dict, *, deadline_ts: float | None = None) -> Task:
    return Task(
        id=new_task_id(),
        type=CaptchaType.ALTCHA,
        payload=payload,
        preferred_mode=SolveMode.HTTP,
        deadline_ts=deadline_ts,
    )


def _solve(payload: dict, *, deadline_ts: float | None = None):
    return _run(AltchaSolver().solve_http(_make_task(payload, deadline_ts=deadline_ts), SolverContext()))


def _decode(token: str) -> dict:
    # wire format: base64(JSON), exactly what the widget posts back
    return json.loads(base64.b64decode(token))


@pytest.mark.parametrize("expected", [0, 1, 42, 1337, 9999])
def test_altcha_solves_known_number(expected: int):
    payload = make_altcha_challenge(number=expected, max_n=max(expected + 100, 100))
    result = _solve(payload)
    body = _decode(result.token)
    assert body["number"] == expected
    assert body["challenge"] == payload["challenge"]
    assert body["salt"] == payload["salt"]
    assert body["signature"] == payload["signature"]
    assert body["algorithm"] == "SHA-256"
    assert set(body) == {"algorithm", "challenge", "number", "salt", "signature"}


def test_token_is_base64_not_raw_json():
    payload = make_altcha_challenge(number=3, max_n=100)
    token = _solve(payload).token
    assert not token.startswith("{")
    with pytest.raises(ValueError):
        json.loads(token)


def test_altcha_picks_lowest_matching_n():
    payload = make_altcha_challenge(number=250, max_n=1000, salt="fixed-salt")
    assert _decode(_solve(payload).token)["number"] == 250


def test_altcha_accepts_max_alias():
    payload = make_altcha_challenge(number=12, max_n=200)
    payload.pop("maxnumber")
    payload["max"] = 200
    assert _decode(_solve(payload).token)["number"] == 12


def test_missing_max_falls_back_to_the_library_default():
    payload = make_altcha_challenge(number=5, max_n=1000)
    payload.pop("maxnumber")
    assert _decode(_solve(payload).token)["number"] == 5


def test_salt_query_is_part_of_the_preimage():
    payload = make_altcha_challenge(number=21, max_n=100, salt="s", expires_at=int(time.time()) + 600)
    body = _decode(_solve(payload).token)
    assert body["salt"] == payload["salt"]
    assert body["salt"].endswith("&")
    digest = hashlib.sha256((body["salt"] + str(body["number"])).encode("utf-8")).hexdigest()
    assert digest == payload["challenge"]


def test_expired_salt_raises_challenge_expired():
    payload = make_altcha_challenge(number=5, max_n=100, salt="s", expires_at=int(time.time()) - 60)
    with pytest.raises(ChallengeExpiredError):
        _solve(payload)


def test_malformed_expiry_raises_challenge_expired():
    payload = make_altcha_challenge(number=5, max_n=100)
    payload["salt"] = "s?expires=not-a-number&"
    payload["challenge"] = hashlib.sha256((payload["salt"] + "5").encode()).hexdigest()
    with pytest.raises(ChallengeExpiredError):
        _solve(payload)


def test_altcha_unsolvable_raises():
    payload = {
        "algorithm": "SHA-256",
        "challenge": "0" * 64,
        "maxnumber": 500,
        "salt": "nope&",
        "signature": "sig",
    }
    with pytest.raises(UnsolvableError):
        _solve(payload)


def test_altcha_rejects_unknown_algorithm():
    payload = make_altcha_challenge(number=10, max_n=100)
    payload["algorithm"] = "MD5"
    with pytest.raises(UnsolvableError):
        _solve(payload)


def test_altcha_rejects_missing_fields():
    with pytest.raises(UnsolvableError):
        _solve({"algorithm": "SHA-256", "challenge": "aa", "maxnumber": 10})


def test_altcha_rejects_out_of_range_max():
    payload = make_altcha_challenge(number=1, max_n=100)
    payload["maxnumber"] = 10**15
    with pytest.raises(UnsolvableError):
        _solve(payload)


def test_altcha_rejects_challenge_of_wrong_length():
    payload = make_altcha_challenge(number=1, max_n=100)
    payload["challenge"] = "aa"
    with pytest.raises(UnsolvableError):
        _solve(payload)


def test_altcha_rejects_v2_parameter_payload():
    payload = {"parameters": {"algorithm": "PBKDF2/SHA-256", "nonce": "00", "salt": "00", "cost": 1000}}
    with pytest.raises(UnsolvableError):
        _solve(payload)


def test_deadline_in_the_past_raises_timeout():
    payload = make_altcha_challenge(number=1, max_n=100)
    with pytest.raises(CaptchaTimeout):
        _solve(payload, deadline_ts=time.time() - 1)


def test_altcha_sha1_and_sha512_also_work():
    for algo, number in (("SHA-1", 77), ("SHA-512", 321)):
        payload = make_altcha_challenge(number=number, max_n=1000, algorithm=algo)
        body = _decode(_solve(payload).token)
        assert body["algorithm"] == algo
        assert body["number"] == number
