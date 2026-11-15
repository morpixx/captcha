"""Canary fixtures: every checked-in case must be loadable and shape-correct.

An empty fixture directory makes CanarySuite report total=0 / pass_rate=1.0,
which reads as green (runbooks/canary.md §3). This module pins the corpus, the
solution keys from domain/solution.py, and one end-to-end run through a fake
gateway.

Each fixture carries a note naming the recorded test artefact its values came
from. A note that says "replay case only" means no live target was captured:
the case proves the wire/schema, not an L3 pass rate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from captcha.domain.catalog import SolutionKind, solution_kind
from captcha.domain.enums import CaptchaType
from captcha.domain.solution import build_solution
from captcha.services.canary import CanarySuite
from captcha.services.gateway.schemas import TaskPayload
from captcha.solvers.pow.altcha import _parse_challenge
from captcha.solvers.pow.friendly import parse_puzzle

FIXTURES = Path(__file__).with_name("fixtures")

#: One row per file: fixture name -> type the gateway must resolve it to.
EXPECTED_FIXTURES: dict[str, CaptchaType] = {
    "altcha_v1": CaptchaType.ALTCHA,
    "datadome_tags": CaptchaType.DATADOME_FULL,
    "friendly_captcha_v1": CaptchaType.FRIENDLY,
    "funcaptcha_gt2": CaptchaType.FUNCAPTCHA,
    "geetest_v3_slide": CaptchaType.GEETEST_V3,
    "geetest_v4_slide": CaptchaType.GEETEST_V4,
    "hcaptcha_http_example_com": CaptchaType.HCAPTCHA,
    "recaptcha_v2_audio": CaptchaType.RECAPTCHA_V2,
    "recaptcha_v3_score": CaptchaType.RECAPTCHA_V3,
    "turnstile_widget": CaptchaType.TURNSTILE,
}


def _suite() -> CanarySuite:
    # No gateway is contacted here: load_fixtures() only reads the directory.
    return CanarySuite(gateway_url="http://unused", fixtures_dir=FIXTURES)


def _fixtures() -> dict[str, Any]:
    return {fx.name: fx for fx in _suite().load_fixtures()}


def _raw(name: str) -> dict[str, Any]:
    for path in FIXTURES.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("name") == name:
            return data
    raise AssertionError(f"fixture {name!r} has no file")


def _representative_result(kind: SolutionKind) -> dict[str, Any]:
    """A minimal solver answer carrying only the fields that kind projects.

    The point is not to fabricate a live answer — it is to make domain/solution
    the oracle for "which keys does a client read for this type". Solver
    bookkeeping (audio_bytes, path, action) is deliberately left out.
    """
    if kind is SolutionKind.HCAPTCHA:
        return {"token": "canary-token", "extra": {"userAgent": "canary-ua"}}
    if kind is SolutionKind.RECAPTCHA:
        return {"token": "canary-token"}
    if kind is SolutionKind.TURNSTILE:
        return {"token": "canary-token", "extra": {"userAgent": "canary-ua"}}
    if kind is SolutionKind.TOKEN:
        return {"token": "canary-token", "extra": {"userAgent": "canary-ua"}}
    if kind is SolutionKind.GEETEST_V3:
        return {
            "token": "canary-validate",
            "extra": {
                "challenge": "canary-challenge",
                "validate": "canary-validate",
                "seccode": "canary-validate|jordan",
            },
        }
    if kind is SolutionKind.GEETEST_V4:
        return {
            "token": "canary-output",
            "extra": {
                "captcha_id": "canary-id",
                "lot_number": "canary-lot",
                "pass_token": "canary-pass",
                "gen_time": "1",
                "captcha_output": "canary-output",
            },
        }
    if kind is SolutionKind.COOKIE_ENGINE:
        return {
            "token": "canary-cookie",
            "extra": {"cookies": {"datadome": "canary-cookie"}, "userAgent": "canary-ua"},
        }
    if kind in (SolutionKind.FRIENDLY, SolutionKind.ALTCHA):
        return {"token": "canary-token"}
    raise AssertionError(f"no representative result for solution kind {kind!r}")


def test_fixture_dir_is_not_empty() -> None:
    fixtures = _suite().load_fixtures()
    assert fixtures, "canary fixtures missing: an empty set is not a green run"
    assert set(_fixtures()) == set(EXPECTED_FIXTURES)


def test_fixture_ids_are_unique() -> None:
    fixtures = _fixtures()
    assert len(fixtures) == len(EXPECTED_FIXTURES)
    ids = [_raw(name).get("id") or name for name in fixtures]
    assert len(ids) == len(set(ids)), "duplicate fixture id"


@pytest.mark.parametrize("name", sorted(EXPECTED_FIXTURES))
def test_fixture_matches_the_canary_suite_schema(name: str) -> None:
    fx = _fixtures()[name]
    raw = _raw(name)

    # The gateway schema must accept the task and resolve it to the target type.
    payload = TaskPayload.model_validate(fx.task)
    assert payload.type is EXPECTED_FIXTURES[name]

    assert fx.name == name
    assert fx.client_key == "canary", "no real account key belongs in git"
    assert "clientKey" not in raw, "keys come from env/secret, never the fixture file"
    assert "proxy" not in fx.task, "canary fixtures must not pin a proxy"

    assert fx.expect.get("status") == "ready"
    keys = fx.expect.get("solution_contains")
    assert isinstance(keys, list) and keys
    assert len(keys) == len(set(keys))
    assert raw.get("note"), "every fixture names its recorded source"


@pytest.mark.parametrize("name", sorted(EXPECTED_FIXTURES))
def test_expected_keys_are_the_domain_solution_projection(name: str) -> None:
    fx = _fixtures()[name]
    payload = TaskPayload.model_validate(fx.task)
    kind = solution_kind(payload.type)
    assert kind is not None

    solution = build_solution(payload.type, _representative_result(kind), site_url=payload.site_url)
    assert solution is not None
    assert set(fx.expect["solution_contains"]) == set(solution)


def test_friendly_payload_parses_as_a_solvable_puzzle() -> None:
    fixtures = _fixtures()
    puzzle = parse_puzzle(fixtures["friendly_captcha_v1"].task)

    assert puzzle.signature
    assert puzzle.solutions > 0
    assert puzzle.difficulty > 0
    assert 32 <= len(puzzle.buffer) <= 64


def test_altcha_payload_parses_as_a_solvable_challenge() -> None:
    fixtures = _fixtures()
    algorithm, challenge, max_n, salt, signature = _parse_challenge(fixtures["altcha_v1"].task)

    assert algorithm == "SHA-256"
    assert len(challenge) == 64
    assert max_n > 0
    assert salt
    assert signature


async def test_one_fixture_runs_through_a_fake_gateway(tmp_path: Path) -> None:
    source = FIXTURES / "hcaptcha_http.json"
    (tmp_path / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    calls: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path == "/createTask":
            assert body["clientKey"] == "canary"
            assert body["task"]["type"] == "hcaptcha"
            return httpx.Response(200, json={"errorId": 0, "taskId": "canary-task-1"})
        if request.url.path == "/getTaskResult":
            assert body["taskId"] == "canary-task-1"
            return httpx.Response(
                200,
                json={
                    "errorId": 0,
                    "status": "ready",
                    "solution": {"gRecaptchaResponse": "canary-token", "userAgent": "canary-ua"},
                },
            )
        return httpx.Response(404)

    suite = CanarySuite(
        gateway_url="http://gateway.test",
        fixtures_dir=tmp_path,
        poll_interval_sec=0.0,
        task_timeout_sec=1.0,
        transport=httpx.MockTransport(handler),
    )

    report = await suite.run_once()

    assert [path for path, _ in calls] == ["/createTask", "/getTaskResult"]
    assert report.total == 1
    assert report.passed == 1
    assert report.failed == 0
    assert report.pass_rate == 1.0
    assert report.results[0].status == "ready"
    assert report.results[0].task_id == "canary-task-1"
    assert set(report.results[0].solution_keys) == {"gRecaptchaResponse", "userAgent"}


async def test_fake_gateway_missing_expected_key_is_a_failure(tmp_path: Path) -> None:
    source = FIXTURES / "hcaptcha_http.json"
    (tmp_path / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": "canary-task-2"})
        return httpx.Response(
            200,
            json={"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "canary-token"}},
        )

    suite = CanarySuite(
        gateway_url="http://gateway.test",
        fixtures_dir=tmp_path,
        poll_interval_sec=0.0,
        task_timeout_sec=1.0,
        transport=httpx.MockTransport(handler),
    )

    report = await suite.run_once()

    assert report.total == 1
    assert report.failed == 1
    assert report.results[0].ok is False
    assert "solution missing keys" in (report.results[0].error or "")
