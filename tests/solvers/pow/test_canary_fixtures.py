"""Canary fixtures: payload -> token, verified from the fixture (not the solver)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from captcha.domain import CaptchaType
from captcha.domain.solution import build_solution
from captcha.solvers.harness import load_fixtures_dir, run_fixtures
from captcha.solvers.pow._hash import difficulty_threshold, hash_meets_threshold
from captcha.solvers.pow.altcha import AltchaSolver
from captcha.solvers.pow.friendly import FriendlySolver, parse_puzzle

FIXTURES = Path(__file__).with_name("fixtures")
EXPECTED_FRIENDLY = {"friendly_v1_canary", "friendly_v1_64byte_canary"}
EXPECTED_ALTCHA = {"altcha_v1_canary": 1337, "altcha_v1_sha512_canary": 31337}


def _by_type(captcha_type: CaptchaType) -> list[Any]:
    return [f for f in load_fixtures_dir(FIXTURES) if f.type is captcha_type]


def _verify_friendly(payload: dict, token: str) -> None:
    puzzle = parse_puzzle(payload)
    signature, b64buf, b64solutions, b64diagnostics = token.split(".")
    assert signature == puzzle.signature
    assert base64.b64decode(b64buf) == puzzle.buffer
    assert base64.b64decode(b64solutions) and len(base64.b64decode(b64diagnostics)) == 3

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
        assert hash_meets_threshold(digest, threshold)


def test_friendly_canary_fixtures():
    fixtures = _by_type(CaptchaType.FRIENDLY)
    assert {f.name for f in fixtures} == EXPECTED_FRIENDLY
    report = asyncio.run(run_fixtures(FriendlySolver(), fixtures))
    assert [entry["fixture"] for entry in report] == sorted(EXPECTED_FRIENDLY)
    payloads = {f.name: f.payload for f in fixtures}
    for entry in report:
        assert entry["ok"], entry
        token = entry["result"]["token"]
        _verify_friendly(payloads[entry["fixture"]], token)
        # what the 2captcha client actually reads: solution.data.token
        assert build_solution(CaptchaType.FRIENDLY, {"token": token}) == {"data": {"token": token}}


def test_altcha_canary_fixtures():
    fixtures = _by_type(CaptchaType.ALTCHA)
    assert {f.name for f in fixtures} == set(EXPECTED_ALTCHA)
    report = asyncio.run(run_fixtures(AltchaSolver(), fixtures))
    assert [entry["fixture"] for entry in report] == sorted(EXPECTED_ALTCHA)
    payloads = {f.name: f.payload for f in fixtures}
    for entry in report:
        assert entry["ok"], entry
        token = entry["result"]["token"]
        body = json.loads(base64.b64decode(token))
        payload = payloads[entry["fixture"]]
        assert body["number"] == EXPECTED_ALTCHA[entry["fixture"]]
        assert body["algorithm"] == payload["algorithm"]
        assert body["salt"] == payload["salt"]
        assert build_solution(CaptchaType.ALTCHA, {"token": token})["data"]["token"] == token
        digest = hashlib.new(
            payload["algorithm"].replace("-", "").lower(),
            (body["salt"] + str(body["number"])).encode("utf-8"),
        ).hexdigest()
        assert digest == payload["challenge"]
