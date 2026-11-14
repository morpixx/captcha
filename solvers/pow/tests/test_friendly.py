from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct

import pytest

from captcha_common import CaptchaType, Task
from captcha_common.enums import SolveMode
from captcha_common.errors import UnsolvableError
from captcha_common.types import new_task_id
from captcha_solver_base import SolverContext
from captcha_solver_pow._generator import make_friendly_puzzle
from captcha_solver_pow._hash import leading_zero_bits
from captcha_solver_pow.friendly import FriendlySolver, _parse_puzzle


def _run(coro):
    return asyncio.run(coro)


def _verify_solution(payload: dict, nonce: int, worker: int, difficulty: int, salt_base: bytes) -> int:
    buf = salt_base + struct.pack("<I", worker) + struct.pack("<Q", nonce)
    h = hashlib.sha256(buf).digest()
    return leading_zero_bits(h)


def _make_task(payload: dict) -> Task:
    return Task(
        id=new_task_id(),
        type=CaptchaType.FRIENDLY,
        payload=payload,
        preferred_mode=SolveMode.HTTP,
    )


async def _solve(solver: FriendlySolver, task: Task) -> "object":
    ctx = SolverContext()
    await solver.prepare(ctx)
    try:
        return await solver.solve_http(task, ctx)
    finally:
        await solver.shutdown()


def test_friendly_solves_low_difficulty_single_worker():
    payload = make_friendly_puzzle(difficulty=6, workers=1, seed=1)
    _, diff, workers, salt_base = _parse_puzzle(payload)
    assert workers == 1
    result = _run(_solve(FriendlySolver(), _make_task(payload)))
    assert result.token is not None
    token = json.loads(base64.b64decode(result.token).decode("ascii"))
    assert token["worker_count"] == workers
    assert len(token["solutions"]) == workers
    sol = token["solutions"][0]
    bits = _verify_solution(payload, sol["nonce"], sol["worker"], diff, salt_base)
    assert bits >= diff


def test_friendly_solves_multi_worker_difficulty_8():
    payload = make_friendly_puzzle(difficulty=8, workers=4, seed=2)
    sig, diff, workers, salt_base = _parse_puzzle(payload)
    result = _run(_solve(FriendlySolver(), _make_task(payload)))
    token = json.loads(base64.b64decode(result.token).decode("ascii"))
    assert token["response_signature"] == sig
    assert token["worker_count"] == workers
    assert len(token["solutions"]) == workers
    seen = set()
    for sol in token["solutions"]:
        assert sol["worker"] not in seen
        seen.add(sol["worker"])
        bits = _verify_solution(payload, sol["nonce"], sol["worker"], diff, salt_base)
        assert bits >= diff, f"worker {sol['worker']} got only {bits} bits"


def test_friendly_accepts_structured_payload():
    salt_hex = "cafebabedeadbeef00112233"
    structured = {
        "signature": "struct-sig",
        "difficulty": 6,
        "workers": 2,
        "salt_base": salt_hex,
    }
    result = _run(_solve(FriendlySolver(), _make_task(structured)))
    token = json.loads(base64.b64decode(result.token).decode("ascii"))
    assert token["response_signature"] == "struct-sig"
    assert token["worker_count"] == 2
    for sol in token["solutions"]:
        bits = _verify_solution(
            structured, sol["nonce"], sol["worker"], 6, bytes.fromhex(salt_hex),
        )
        assert bits >= 6


def test_friendly_rejects_malformed_puzzle():
    task = _make_task({"puzzle": "only.three.parts"})
    with pytest.raises(UnsolvableError):
        _run(_solve(FriendlySolver(), task))


def test_friendly_rejects_out_of_range_workers():
    task = _make_task({"puzzle": "sig.4.0.deadbeef"})
    with pytest.raises(UnsolvableError):
        _run(_solve(FriendlySolver(), task))


def test_leading_zero_bits_sanity():
    assert leading_zero_bits(b"\x00\x00\x00\x00") == 32
    assert leading_zero_bits(b"\x01\x00\x00\x00") == 7
    assert leading_zero_bits(b"\x80\x00\x00\x00") == 0
    assert leading_zero_bits(b"\x00\x10\x00\x00") == 8 + 3
    assert leading_zero_bits(b"\xff") == 0
