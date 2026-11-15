"""The checked-in canary fixtures must be real, loadable cases.

An empty fixture directory makes CanarySuite report total=0 / pass_rate=1.0,
which reads as green (runbooks/canary.md §3). This test pins a non-empty set
and validates each payload against the solver that has to consume it, so a
fixture cannot silently rot into a schema the worker will reject.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from captcha.domain.enums import CaptchaType
from captcha.services.canary import CanarySuite
from captcha.services.gateway.type_map import resolve_captcha_type
from captcha.solvers.pow.altcha import _parse_challenge
from captcha.solvers.pow.friendly import parse_puzzle

FIXTURES = Path(__file__).with_name("fixtures")


def _suite() -> CanarySuite:
    # No gateway is contacted here: load_fixtures() only reads the directory.
    return CanarySuite(gateway_url="http://unused", fixtures_dir=FIXTURES)


def test_fixture_dir_is_not_empty() -> None:
    fixtures = _suite().load_fixtures()
    assert fixtures, "canary fixtures missing: an empty set is not a green run"
    assert {fx.name for fx in fixtures} == {"friendly_captcha_v1", "altcha_v1"}


@pytest.mark.parametrize("name", ["friendly_captcha_v1", "altcha_v1"])
def test_fixture_matches_the_canary_suite_schema(name: str) -> None:
    fixtures = {fx.name: fx for fx in _suite().load_fixtures()}
    fx = fixtures[name]

    # CanarySuite's contract: a 2captcha task object plus expected status/keys.
    assert resolve_captcha_type(fx.task["type"]) in {CaptchaType.FRIENDLY, CaptchaType.ALTCHA}
    assert fx.expect.get("status") == "ready"
    assert fx.expect.get("solution_contains") == ["data"]
    assert fx.client_key == "canary", "no real account key belongs in git"
    assert "puzzle" in fx.task or "challenge" in fx.task


def test_friendly_payload_parses_as_a_solvable_puzzle() -> None:
    fixtures = {fx.name: fx for fx in _suite().load_fixtures()}
    puzzle = parse_puzzle(fixtures["friendly_captcha_v1"].task)

    assert puzzle.signature
    assert puzzle.solutions > 0
    assert puzzle.difficulty > 0
    assert 32 <= len(puzzle.buffer) <= 64


def test_altcha_payload_parses_as_a_solvable_challenge() -> None:
    fixtures = {fx.name: fx for fx in _suite().load_fixtures()}
    algorithm, challenge, max_n, salt, signature = _parse_challenge(fixtures["altcha_v1"].task)

    assert algorithm == "SHA-256"
    assert len(challenge) == 64
    assert max_n > 0
    assert salt
    assert signature
