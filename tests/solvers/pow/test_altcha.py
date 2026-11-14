from __future__ import annotations

import asyncio
import json

import pytest

from captcha.domain import CaptchaType, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext
from captcha.solvers.pow._generator import make_altcha_challenge
from captcha.solvers.pow.altcha import AltchaSolver


def _run(coro):
    return asyncio.run(coro)


def _make_task(payload: dict) -> Task:
    return Task(
        id=new_task_id(),
        type=CaptchaType.ALTCHA,
        payload=payload,
        preferred_mode=SolveMode.HTTP,
    )


async def _solve(solver: AltchaSolver, task: Task):
    ctx = SolverContext()
    return await solver.solve_http(task, ctx)


@pytest.mark.parametrize("expected", [0, 1, 42, 1337, 9999])
def test_altcha_solves_known_number(expected: int):
    payload = make_altcha_challenge(number=expected, max_n=max(expected + 100, 100))
    result = _run(_solve(AltchaSolver(), _make_task(payload)))
    assert result.token is not None
    body = json.loads(result.token)
    assert body["number"] == expected
    assert body["challenge"] == payload["challenge"]
    assert body["salt"] == payload["salt"]
    assert body["signature"] == payload["signature"]
    assert body["algorithm"] == "SHA-256"
    assert isinstance(body["took_ms"], int)


def test_altcha_picks_lowest_matching_n():
    # Generator picks the challenge, but the solver iterates from 0 upward —
    # so the returned number must equal our seeded value.
    payload = make_altcha_challenge(number=250, max_n=1000, salt="fixed-salt")
    result = _run(_solve(AltchaSolver(), _make_task(payload)))
    assert json.loads(result.token)["number"] == 250


def test_altcha_unsolvable_raises():
    payload = {
        "algorithm": "SHA-256",
        "challenge": "0" * 64,   # no number in range produces this
        "max": 500,
        "salt": "nope",
        "signature": "sig",
    }
    with pytest.raises(UnsolvableError):
        _run(_solve(AltchaSolver(), _make_task(payload)))


def test_altcha_rejects_unknown_algorithm():
    payload = make_altcha_challenge(number=10, max_n=100)
    payload["algorithm"] = "MD5"
    with pytest.raises(UnsolvableError):
        _run(_solve(AltchaSolver(), _make_task(payload)))


def test_altcha_rejects_missing_fields():
    payload = {"algorithm": "SHA-256", "challenge": "aa", "max": 10}  # no salt
    with pytest.raises(UnsolvableError):
        _run(_solve(AltchaSolver(), _make_task(payload)))


def test_altcha_rejects_out_of_range_max():
    payload = make_altcha_challenge(number=1, max_n=100)
    payload["max"] = 10**15
    with pytest.raises(UnsolvableError):
        _run(_solve(AltchaSolver(), _make_task(payload)))


def test_altcha_sha1_also_works():
    payload = make_altcha_challenge(number=77, max_n=500, algorithm="SHA-1")
    result = _run(_solve(AltchaSolver(), _make_task(payload)))
    body = json.loads(result.token)
    assert body["algorithm"] == "SHA-1"
    assert body["number"] == 77
