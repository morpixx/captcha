"""Canary suite against a fake gateway: fixtures in, type health out.

Covers contract §10: a run reads the fixture directory, asks the gateway for
every task, writes `captcha:typehealth:<type>` as `ok`/`degraded`, updates the
canary gauges, and fires the webhook only when the pass rate is below the
threshold. No sockets: the suite and the alerter accept an injected transport,
Redis is an in-memory double.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from captcha.observability import metrics
from captcha.services.canary import CanarySuite, WebhookAlerter
from captcha.services.canary.suite import TYPE_HEALTH_TTL_SECONDS, FixtureResult, SuiteReport


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail = fail

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        if self.fail:
            raise ConnectionError("redis down")
        self.values[key] = value
        self.ttls[key] = ex
        return True


class FakeGateway:
    """2captcha-shaped createTask/getTaskResult, solutions keyed by task type."""

    def __init__(self, solutions: dict[str, dict[str, Any]]) -> None:
        self.solutions = solutions
        self.created: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/createTask":
            body = json.loads(request.content)
            task = body["task"]
            self.created.append(task)
            return httpx.Response(200, json={"errorId": 0, "taskId": task["type"]})
        if request.url.path == "/getTaskResult":
            task_id = json.loads(request.content)["taskId"]
            return httpx.Response(
                200,
                json={"errorId": 0, "status": "ready", "solution": self.solutions.get(task_id, {})},
            )
        return httpx.Response(404)


def _write_fixtures(directory: Path, fixtures: list[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for index, fixture in enumerate(fixtures):
        (directory / f"{index}.json").write_text(json.dumps(fixture), encoding="utf-8")


def _fixture(task_type: str, *, expected_keys: list[str]) -> dict[str, Any]:
    return {
        "name": f"fixture-{task_type}",
        "clientKey": "canary",
        "task": {"type": task_type, "websiteKey": "k"},
        "expect": {"status": "ready", "solution_contains": expected_keys},
    }


def _suite(tmp_path: Path, gateway: FakeGateway, redis: FakeRedis) -> CanarySuite:
    return CanarySuite(
        gateway_url="http://fake-gateway",
        fixtures_dir=tmp_path,
        poll_interval_sec=0.0,
        task_timeout_sec=1.0,
        redis=redis,  # type: ignore[arg-type]
        health_threshold=0.9,
        transport=gateway.transport(),
    )


# ---------------------------------------------------------------------- suite


async def test_passing_fixture_writes_ok_type_health(tmp_path: Path) -> None:
    _write_fixtures(tmp_path, [_fixture("text_captcha", expected_keys=["text"])])
    gateway = FakeGateway({"text_captcha": {"text": "abcd"}})
    redis = FakeRedis()

    report = await _suite(tmp_path, gateway, redis).run_once()

    assert report.total == 1
    assert report.passed == 1
    assert gateway.created == [{"type": "text_captcha", "websiteKey": "k"}]
    assert redis.values == {"captcha:typehealth:text_captcha": "ok"}
    assert redis.ttls["captcha:typehealth:text_captcha"] == TYPE_HEALTH_TTL_SECONDS
    assert metrics.REGISTRY.get_sample_value("captcha_type_health", {"type": "text_captcha"}) == 1.0
    assert metrics.REGISTRY.get_sample_value("captcha_canary_pass_rate", {"type": "text_captcha"}) == 1.0


async def test_failing_type_is_degraded_while_other_type_stays_ok(tmp_path: Path) -> None:
    _write_fixtures(
        tmp_path,
        [
            _fixture("text_captcha", expected_keys=["text"]),
            _fixture("grid_captcha", expected_keys=["token"]),
        ],
    )
    # grid_captcha returns no token -> the fixture fails, the type is degraded.
    gateway = FakeGateway({"text_captcha": {"text": "abcd"}, "grid_captcha": {}})
    redis = FakeRedis()

    report = await _suite(tmp_path, gateway, redis).run_once()

    assert report.total == 2
    assert report.passed == 1
    assert redis.values == {
        "captcha:typehealth:text_captcha": "ok",
        "captcha:typehealth:grid_captcha": "degraded",
    }
    assert metrics.REGISTRY.get_sample_value("captcha_type_health", {"type": "grid_captcha"}) == 0.0
    assert metrics.REGISTRY.get_sample_value("captcha_canary_pass_rate", {"type": "grid_captcha"}) == 0.0


async def test_empty_fixture_dir_writes_nothing(tmp_path: Path) -> None:
    redis = FakeRedis()

    report = await _suite(tmp_path, FakeGateway({}), redis).run_once()

    assert report.total == 0
    assert redis.values == {}


async def test_redis_failure_does_not_lose_the_report(tmp_path: Path) -> None:
    _write_fixtures(tmp_path, [_fixture("text_captcha", expected_keys=["text"])])
    gateway = FakeGateway({"text_captcha": {"text": "abcd"}})

    report = await _suite(tmp_path, gateway, FakeRedis(fail=True)).run_once()

    assert report.passed == 1
    assert report.pass_rate == 1.0


# -------------------------------------------------------------------- alerter


def _report(*, ok: bool) -> SuiteReport:
    return SuiteReport(
        started_at=0.0,
        finished_at=1.0,
        results=[
            FixtureResult(
                name="one",
                type_="text_captcha",
                ok=ok,
                status="ready",
                latency_ms=12.0,
                task_id="1",
            )
        ],
    )


async def test_webhook_fires_below_threshold() -> None:
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(204)

    alerter = WebhookAlerter(
        webhook_url="https://hooks.example/canary",
        pass_threshold=0.9,
        gateway_url="http://fake-gateway",
        hostname="test-host",
        transport=httpx.MockTransport(handler),
    )

    fired = await alerter.maybe_alert(_report(ok=False))

    assert fired is True
    assert len(posted) == 1
    payload = posted[0]
    assert payload["reason"] == "pass_rate_below_threshold"
    assert payload["hostname"] == "test-host"
    assert payload["report"]["passed"] == 0
    assert payload["report"]["failed"] == 1


async def test_webhook_is_silent_at_or_above_threshold() -> None:
    posted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(204)

    alerter = WebhookAlerter(
        webhook_url="https://hooks.example/canary",
        pass_threshold=0.9,
        transport=httpx.MockTransport(handler),
    )

    assert await alerter.maybe_alert(_report(ok=True)) is False
    assert posted == []


async def test_webhook_is_silent_without_url() -> None:
    alerter = WebhookAlerter(webhook_url="")
    assert await alerter.maybe_alert(_report(ok=False)) is False
