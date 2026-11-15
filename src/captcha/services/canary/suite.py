"""Fixture loader + runner. One `CanarySuite.run_once()` returns a `SuiteReport`."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import httpx
import structlog

from captcha.observability.metrics import record_canary_type

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

#: A health key has to outlive one missed run (canary interval is 6h, contract §10).
TYPE_HEALTH_TTL_SECONDS: Final[int] = 12 * 3600


def type_health_key(prefix: str, type_: str) -> str:
    """`captcha:typehealth:<type>` = ok | degraded (contract §10)."""
    return f"{prefix}:typehealth:{type_}"


@dataclass(slots=True)
class Fixture:
    name: str
    client_key: str
    task: dict[str, Any]
    expect: dict[str, Any] = field(default_factory=dict)
    source: str = ""


@dataclass(slots=True)
class FixtureResult:
    name: str
    type_: str
    ok: bool
    status: str
    latency_ms: float
    task_id: str | None
    error: str | None = None
    # for alert payloads
    expected_keys: list[str] = field(default_factory=list)
    solution_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SuiteReport:
    started_at: float
    finished_at: float
    results: list[FixtureResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total) if self.total else 1.0

    @property
    def p50_latency_ms(self) -> float:
        return _percentile([r.latency_ms for r in self.results], 50)

    @property
    def p95_latency_ms(self) -> float:
        return _percentile([r.latency_ms for r in self.results], 95)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": round(self.finished_at - self.started_at, 3),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.pass_rate, 4),
            "p50_latency_ms": round(self.p50_latency_ms, 1),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "failures": [
                {
                    "name": r.name,
                    "type": r.type_,
                    "status": r.status,
                    "error": r.error,
                    "latency_ms": round(r.latency_ms, 1),
                }
                for r in self.results
                if not r.ok
            ],
        }


def _percentile(xs: list[float], p: int) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(0, min(len(s) - 1, round((p / 100.0) * (len(s) - 1))))
    return s[k]


class CanarySuite:
    """Runs a directory of fixtures against a 2captcha-compat gateway."""

    def __init__(
        self,
        *,
        gateway_url: str,
        fixtures_dir: Path | str,
        poll_interval_sec: float = 5.0,
        task_timeout_sec: float = 180.0,
        http_timeout_sec: float = 15.0,
        redis: Redis | None = None,
        prefix: str = "captcha",
        health_threshold: float = 0.9,
        type_health_ttl_sec: int = TYPE_HEALTH_TTL_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.fixtures_dir = Path(fixtures_dir)
        self.poll_interval_sec = poll_interval_sec
        self.task_timeout_sec = task_timeout_sec
        self.http_timeout_sec = http_timeout_sec
        #: optional: without it the suite still runs, it just cannot publish type health
        self.redis = redis
        self.prefix = prefix
        self.health_threshold = health_threshold
        self.type_health_ttl_sec = type_health_ttl_sec
        #: injected by tests; production goes through the real network
        self.transport = transport

    def load_fixtures(self) -> list[Fixture]:
        if not self.fixtures_dir.exists():
            logger.warning("canary.fixtures.missing", path=str(self.fixtures_dir))
            return []
        fixtures: list[Fixture] = []
        for path in sorted(self.fixtures_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.error("canary.fixture.parse_failed", path=str(path), error=str(exc))
                continue
            if "task" not in raw:
                logger.error("canary.fixture.missing_task", path=str(path))
                continue
            fixtures.append(
                Fixture(
                    name=raw.get("name") or path.stem,
                    client_key=raw.get("clientKey") or raw.get("client_key") or "canary",
                    task=raw["task"],
                    expect=raw.get("expect") or {},
                    source=str(path),
                )
            )
        return fixtures

    async def run_once(self) -> SuiteReport:
        fixtures = self.load_fixtures()
        started_at = time.time()
        if not fixtures:
            logger.warning("canary.run.no_fixtures")
            return SuiteReport(started_at=started_at, finished_at=time.time(), results=[])

        results: list[FixtureResult] = []
        async with httpx.AsyncClient(
            base_url=self.gateway_url,
            timeout=self.http_timeout_sec,
            transport=self.transport,
        ) as client:
            # sequential by default — the gateway owns concurrency; canary should be
            # cheap and predictable, not a load test
            for fx in fixtures:
                results.append(await self._run_fixture(client, fx))

        report = SuiteReport(started_at=started_at, finished_at=time.time(), results=results)
        await self._publish_type_health(report)
        return report

    async def _publish_type_health(self, report: SuiteReport) -> None:
        """Per-type verdict -> metrics + `captcha:typehealth:<type>` (contract §10).

        Redis is best-effort: a canary run that cannot publish health is still a
        valid measurement, and a Redis outage must not hide the pass rate.
        """
        by_type: dict[str, list[FixtureResult]] = {}
        for result in report.results:
            by_type.setdefault(result.type_, []).append(result)

        for type_, results in sorted(by_type.items()):
            passed = sum(1 for r in results if r.ok)
            pass_rate = passed / len(results)
            healthy = pass_rate >= self.health_threshold
            record_canary_type(type_, pass_rate=pass_rate, healthy=healthy)

            if self.redis is None:
                continue
            try:
                await self.redis.set(
                    type_health_key(self.prefix, type_),
                    "ok" if healthy else "degraded",
                    ex=self.type_health_ttl_sec or None,
                )
            except Exception as exc:
                logger.warning("canary.typehealth.write_failed", type=type_, error=str(exc))

    async def _run_fixture(self, client: httpx.AsyncClient, fx: Fixture) -> FixtureResult:
        type_ = str(fx.task.get("type") or "unknown")
        t0 = time.perf_counter()

        try:
            create = await client.post(
                "/createTask",
                json={"clientKey": fx.client_key, "task": fx.task},
            )
        except httpx.HTTPError as exc:
            return FixtureResult(
                name=fx.name,
                type_=type_,
                ok=False,
                status="transport_error",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                task_id=None,
                error=f"createTask transport: {exc!s}",
            )

        if create.status_code != 200:
            return FixtureResult(
                name=fx.name,
                type_=type_,
                ok=False,
                status=f"http_{create.status_code}",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                task_id=None,
                error=f"createTask http {create.status_code}: {create.text[:300]}",
            )

        try:
            body = create.json()
        except ValueError as exc:
            return FixtureResult(
                name=fx.name,
                type_=type_,
                ok=False,
                status="bad_json",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                task_id=None,
                error=f"createTask bad json: {exc!s}",
            )

        error_id = int(body.get("errorId") or 0)
        task_id = body.get("taskId")
        if error_id != 0 or not task_id:
            return FixtureResult(
                name=fx.name,
                type_=type_,
                ok=False,
                status="create_rejected",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                task_id=None,
                error=f"{body.get('errorCode')}: {body.get('errorDescription')}",
            )

        # poll until terminal or deadline
        expect_status = str(fx.expect.get("status") or "ready")
        expect_keys = list(fx.expect.get("solution_contains") or [])
        deadline = time.perf_counter() + self.task_timeout_sec
        last_status = "processing"
        last_body: dict[str, Any] = {}

        while time.perf_counter() < deadline:
            await asyncio.sleep(self.poll_interval_sec)
            try:
                poll = await client.post(
                    "/getTaskResult",
                    json={"clientKey": fx.client_key, "taskId": task_id},
                )
            except httpx.HTTPError as exc:
                return FixtureResult(
                    name=fx.name,
                    type_=type_,
                    ok=False,
                    status="transport_error",
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    task_id=str(task_id),
                    error=f"getTaskResult transport: {exc!s}",
                )

            if poll.status_code != 200:
                return FixtureResult(
                    name=fx.name,
                    type_=type_,
                    ok=False,
                    status=f"http_{poll.status_code}",
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    task_id=str(task_id),
                    error=f"getTaskResult http {poll.status_code}: {poll.text[:300]}",
                )

            try:
                last_body = poll.json()
            except ValueError:
                last_body = {}
            last_status = str(last_body.get("status") or "processing")

            if last_status in ("ready", "failed"):
                break

        latency_ms = (time.perf_counter() - t0) * 1000.0
        solution = last_body.get("solution") or {}
        solution_keys = list(solution.keys()) if isinstance(solution, dict) else []

        if last_status != expect_status:
            return FixtureResult(
                name=fx.name,
                type_=type_,
                ok=False,
                status=last_status,
                latency_ms=latency_ms,
                task_id=str(task_id),
                error=(f"expected status={expect_status} got={last_status} errorCode={last_body.get('errorCode')!r}"),
                expected_keys=expect_keys,
                solution_keys=solution_keys,
            )

        if expect_keys:
            missing = [k for k in expect_keys if k not in solution_keys]
            if missing:
                return FixtureResult(
                    name=fx.name,
                    type_=type_,
                    ok=False,
                    status=last_status,
                    latency_ms=latency_ms,
                    task_id=str(task_id),
                    error=f"solution missing keys: {missing}",
                    expected_keys=expect_keys,
                    solution_keys=solution_keys,
                )

        return FixtureResult(
            name=fx.name,
            type_=type_,
            ok=True,
            status=last_status,
            latency_ms=latency_ms,
            task_id=str(task_id),
            expected_keys=expect_keys,
            solution_keys=solution_keys,
        )


def load_fixtures_dir(path: Path | str) -> list[Fixture]:
    return CanarySuite(gateway_url="http://unused", fixtures_dir=path).load_fixtures()
