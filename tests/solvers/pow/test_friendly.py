from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import time

import pytest

from captcha.domain import CaptchaType, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ChallengeExpiredError, UnsolvableError
from captcha.domain.errors import TimeoutError as CaptchaTimeout
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext
from captcha.solvers.pow._generator import make_friendly_puzzle
from captcha.solvers.pow._hash import difficulty_threshold, hash_meets_threshold, leading_zero_bits
from captcha.solvers.pow.friendly import FriendlySolver, parse_puzzle


def _run(coro):
    return asyncio.run(coro)


def _make_task(payload: dict, *, deadline_ts: float | None = None) -> Task:
    return Task(
        id=new_task_id(),
        type=CaptchaType.FRIENDLY,
        payload=payload,
        preferred_mode=SolveMode.HTTP,
        deadline_ts=deadline_ts,
    )


def _solve(payload: dict, *, deadline_ts: float | None = None):
    return _run(FriendlySolver().solve_http(_make_task(payload, deadline_ts=deadline_ts), SolverContext()))


def _verify(payload: dict, token: str) -> None:
    """Recompute the friendly-pow check from the payload, not from solver internals."""
    puzzle = parse_puzzle(payload)
    signature, b64buf, b64solutions, b64diagnostics = token.split(".")
    assert signature == puzzle.signature
    assert base64.b64decode(b64buf) == puzzle.buffer
    assert len(base64.b64decode(b64diagnostics)) == 3

    solutions = base64.b64decode(b64solutions)
    assert len(solutions) == puzzle.solutions * 8
    base = puzzle.buffer.ljust(128, b"\x00")[:120]
    threshold = difficulty_threshold(puzzle.difficulty)
    seen: set[bytes] = set()
    for i in range(puzzle.solutions):
        candidate = solutions[i * 8 : (i + 1) * 8]
        assert candidate not in seen
        seen.add(candidate)
        digest = hashlib.blake2b(base + candidate, digest_size=32).digest()
        assert hash_meets_threshold(digest, threshold), (
            f"solution {i} has u32le {int.from_bytes(digest[:4], 'little')} >= {threshold}"
        )


def test_solves_all_requested_solutions():
    payload = make_friendly_puzzle(difficulty=44, solutions=5, seed=7)
    result = _solve(payload)
    _verify(payload, result.token)
    assert result.extra["difficulty"] == 44
    assert result.extra["solutions"] == 5
    assert result.latency_ms >= 0


def test_token_is_four_dot_parts_on_the_wire():
    payload = make_friendly_puzzle(difficulty=40, solutions=2, seed=9, signature="sig-1")
    token = _solve(payload).token
    assert token.count(".") == 3
    assert token.split(".")[0] == "sig-1"


def test_accepts_split_signature_and_buffer():
    puzzle = make_friendly_puzzle(difficulty=40, solutions=3, seed=3)["puzzle"]
    signature, encoded = puzzle.split(".", 1)
    result = _solve({"signature": signature, "puzzle_buffer": encoded})
    _verify({"puzzle": puzzle}, result.token)


def test_puzzle_buffer_with_additional_data():
    payload = make_friendly_puzzle(difficulty=40, solutions=2, seed=11, extra_data=b"\x11" * 32)
    puzzle = parse_puzzle(payload)
    assert len(puzzle.buffer) == 64
    _verify(payload, _solve(payload).token)


def test_expired_puzzle_is_a_challenge_expiry_not_a_mine_failure():
    payload = make_friendly_puzzle(difficulty=40, solutions=1, seed=1, timestamp=int(time.time()) - 3600, expiry=12)
    with pytest.raises(ChallengeExpiredError):
        _solve(payload)


def test_expiry_zero_never_expires():
    payload = make_friendly_puzzle(difficulty=40, solutions=1, seed=1, timestamp=0, expiry=0)
    _verify(payload, _solve(payload).token)


def test_rejects_unknown_puzzle_version():
    encoded = bytearray(base64.b64decode(make_friendly_puzzle(solutions=1)["puzzle"].split(".")[1]))
    encoded[12] = 2
    payload = {"puzzle": "sig." + base64.b64encode(bytes(encoded)).decode()}
    with pytest.raises(UnsolvableError):
        _solve(payload)


def test_rejects_zero_solutions():
    encoded = bytearray(base64.b64decode(make_friendly_puzzle(solutions=1)["puzzle"].split(".")[1]))
    encoded[14] = 0
    payload = {"puzzle": "sig." + base64.b64encode(bytes(encoded)).decode()}
    with pytest.raises(UnsolvableError):
        _solve(payload)


def test_rejects_wrong_buffer_length():
    payload = {"puzzle": "sig." + base64.b64encode(b"short").decode()}
    with pytest.raises(UnsolvableError):
        _solve(payload)


def test_rejects_bad_base64():
    with pytest.raises(UnsolvableError):
        _solve({"puzzle": "sig.@@@not-base64@@@"})


def test_rejects_missing_puzzle():
    with pytest.raises(UnsolvableError):
        _solve({"sitekey": "FCMGEMUD2M567T8G", "websiteURL": "https://example.com"})


def test_deadline_in_the_past_raises_timeout():
    payload = make_friendly_puzzle(difficulty=40, solutions=1, seed=2)
    with pytest.raises(CaptchaTimeout):
        _solve(payload, deadline_ts=time.time() - 1)


def test_unsatisfiable_difficulty_hits_the_deadline():
    payload = make_friendly_puzzle(difficulty=255, solutions=1, seed=2)
    with pytest.raises(CaptchaTimeout):
        _solve(payload, deadline_ts=time.time() + 0.05)


def test_threshold_math_matches_the_spec():
    assert difficulty_threshold(0) == min(int(math.pow(2.0, 255.999 / 8)), 0xFFFFFFFF)
    assert difficulty_threshold(255) == 1
    assert difficulty_threshold(32) > difficulty_threshold(64)
    assert difficulty_threshold(64) == int(math.pow(2.0, (255.999 - 64) / 8))


def test_hash_threshold_reads_little_endian_u32():
    assert hash_meets_threshold(b"\x01\x00\x00\x00", 2) is True
    assert hash_meets_threshold(b"\x02\x00\x00\x00", 2) is False
    # big-endian reading would call this 0x01000000 and fail a threshold of 2
    assert hash_meets_threshold(b"\x00\x00\x00\x01", 2) is False


def test_leading_zero_bits_sanity():
    assert leading_zero_bits(b"\x00\x00\x00\x00") == 32
    assert leading_zero_bits(b"\x01\x00\x00\x00") == 7
    assert leading_zero_bits(b"\x80\x00\x00\x00") == 0
    assert leading_zero_bits(b"\x00\x10\x00\x00") == 8 + 3
    assert leading_zero_bits(b"\xff") == 0
