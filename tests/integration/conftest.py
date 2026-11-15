"""Fixtures for tests that need real Redis and Postgres.

The suite has no in-memory stand-ins on purpose: the point is to prove the
gateway against the adapters it actually runs with. Infrastructure comes from
one of two places:

- locally: `tests/integration/docker-compose.yml` (`make test-integration-up`);
- CI: the service containers of the `integration` job in
  `.github/workflows/ci.yml`.

URLs are read from `INTEGRATION_REDIS_URL` / `INTEGRATION_POSTGRES_URL` and
default to the compose file's ports and database. When nothing answers, every
test here skips with a reason, so `pytest` stays green on a laptop without
docker. CI sets `INTEGRATION_REQUIRED=1`, which turns a missing service into a
failure instead of a skip — a suite that silently skips in CI proves nothing.

No test here talks to a live captcha vendor. The queue is drained by the test
itself and every solver result is manufactured locally.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from support import CLIENT_KEY, OTHER_CLIENT_KEY

from captcha.adapters.storage import Account, AccountRepo, Base
from captcha.config import Settings
from captcha.services.gateway.app import build_state, create_app, shutdown_state

REDIS_URL = os.getenv("INTEGRATION_REDIS_URL", "redis://127.0.0.1:6379/15")
POSTGRES_URL = os.getenv(
    "INTEGRATION_POSTGRES_URL",
    "postgresql+asyncpg://captcha:captcha@127.0.0.1:5432/captcha_it",
)
#: CI sets this. Without it an unreachable service is a skip, not a failure.
REQUIRED = os.getenv("INTEGRATION_REQUIRED", "").lower() in {"1", "true", "yes"}

_SRC = Path(__file__).resolve().parents[2] / "src"


def _tcp_reachable(url: str, default_port: int) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or default_port
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _wait_reachable(label: str, url: str, default_port: int) -> bool:
    """One probe normally; 30 one-second probes when CI demands the service."""
    attempts = 30 if REQUIRED else 1
    for attempt in range(attempts):
        if _tcp_reachable(url, default_port):
            return True
        if attempt + 1 < attempts:
            time.sleep(1)
    print(f"integration: {label} unreachable at {url}")
    return False


@pytest.fixture(scope="session")
def infra() -> None:
    """Fail fast in CI, skip everywhere else when the services are absent."""
    missing: list[str] = []
    if not _wait_reachable("redis", REDIS_URL, 6379):
        missing.append(f"redis ({REDIS_URL})")
    if not _wait_reachable("postgres", POSTGRES_URL, 5432):
        missing.append(f"postgres ({POSTGRES_URL})")
    if not missing:
        return
    detail = ", ".join(missing)
    if REQUIRED:
        pytest.fail(
            f"INTEGRATION_REQUIRED=1 but integration infrastructure is unavailable: {detail}. "
            "Start tests/integration/docker-compose.yml or fix INTEGRATION_*_URL.",
            pytrace=False,
        )
    pytest.skip(
        f"integration infrastructure unavailable: {detail}. "
        "Run 'make test-integration-up' (tests/integration/docker-compose.yml) and retry."
    )


@pytest.fixture(scope="session")
def database(infra: None) -> Iterator[None]:
    """Bring the real schema to head once per session.

    Runs the operator's own bootstrap command in a child process rather than
    calling alembic in-process: the CLI path is what production actually runs,
    and a subprocess keeps alembic's `asyncio.run` out of pytest-asyncio's
    event loop.
    """
    env = dict(os.environ)
    env["POSTGRES_URL"] = POSTGRES_URL
    env["PYTHONPATH"] = os.pathsep.join(path for path in (str(_SRC), env.get("PYTHONPATH", "")) if path)
    subprocess.run(
        [sys.executable, "-m", "captcha.services.cli", "init-db"],
        env=env,
        check=True,
    )
    yield


async def _clear_redis(redis: aioredis.Redis) -> None:
    keys = [key async for key in redis.scan_iter(match="captcha:it:*", count=500)]
    if keys:
        await redis.delete(*keys)


@pytest.fixture(autouse=True)
async def clean_state(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Empty tables and test Redis keys before and after every integration test.

    The browser smoke test lives under this directory but needs no datastore.
    It is selected by the `browser` marker, not `integration`, so the fixtures
    are resolved lazily instead of being declared as arguments (an argument
    would drag Redis/Postgres in before the marker check could run).
    """
    if request.node.get_closest_marker("integration") is None:
        yield
        return
    request.getfixturevalue("database")

    engine = create_async_engine(POSTGRES_URL, poolclass=NullPool)
    redis = aioredis.from_url(REDIS_URL)
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        await _clear_redis(redis)
        yield
    finally:
        try:
            await _clear_redis(redis)
        finally:
            await redis.aclose()
            await engine.dispose()


@pytest.fixture
def prefix() -> str:
    """Unique Redis namespace per test; `clean_state` sweeps the whole family."""
    return f"captcha:it:{uuid.uuid4().hex[:10]}"


@pytest.fixture
def settings(prefix: str) -> Settings:
    """App config pointed at the test services, with quotas disabled by default.

    Quota limits are exercised by the one test that sets them explicitly; the
    rest of the suite must not trip over a 60/min default when it creates more
    than one task.
    """
    return Settings(
        REDIS_URL=REDIS_URL,
        POSTGRES_URL=POSTGRES_URL,
        REDIS_STREAM_PREFIX=prefix,
        RATE_LIMIT_CREATE_PER_MIN=0,
        MAX_ACTIVE_TASKS_PER_ACCOUNT=0,
        MAX_DAILY_TASKS=0,
        MAX_DAILY_SPEND=0,
        LOG_JSON=False,
    )


@pytest.fixture
async def state(settings: Settings) -> AsyncIterator[Any]:
    """A real AppState: AsyncEngine, Redis client, TaskStateStore, Producer."""
    app_state = await build_state(settings)
    try:
        yield app_state
    finally:
        await shutdown_state(app_state)


@pytest.fixture
def app(settings: Settings, state: Any) -> Any:
    """The real application, wired to the real AppState.

    ASGITransport does not run lifespan, so the state built above is attached
    the same way lifespan would attach it.
    """
    application = create_app(settings)
    application.state.ctx = state
    return application


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as http:
        yield http


@pytest.fixture
async def account(state: Any) -> Account:
    """An account with enough balance for the whole suite, keyed by CLIENT_KEY."""
    async with state.sessionmaker() as session:
        acc = await AccountRepo(session).create(
            api_key=CLIENT_KEY,
            name="integration",
            balance=Decimal("25.000000"),
        )
        await session.commit()
        return acc


@pytest.fixture
async def other_account(state: Any) -> Account:
    async with state.sessionmaker() as session:
        acc = await AccountRepo(session).create(
            api_key=OTHER_CLIENT_KEY,
            name="other",
            balance=Decimal("25.000000"),
        )
        await session.commit()
        return acc
