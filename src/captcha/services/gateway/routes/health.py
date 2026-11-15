"""Liveness + readiness. /healthz never touches dependencies; /readyz pings all three.

Readiness means: Redis answers, Postgres answers, and at least one worker lane
has a fresh heartbeat. The last one is what stops a gateway from selling types
to an empty fleet — see `adapters/queue/capabilities.py` for the key layout.

The probes run concurrently and are wrapped in a short timeout on purpose: an
unreachable Postgres hangs on connect for the driver's default (tens of
seconds), which turns a container healthcheck into a hung request. Readiness has
to answer fast either way — 200 or 503. The lane probe reuses the Redis
connection, so it only runs once the Redis ping succeeded; otherwise the answer
is already 503 and the extra round-trip buys nothing.
"""

from __future__ import annotations

import asyncio
from typing import Final

from fastapi import APIRouter, Depends
from fastapi.responses import ORJSONResponse
from sqlalchemy import text

from captcha.adapters.queue.capabilities import HEARTBEAT_TTL_SECONDS, CapabilityRegistry
from captcha.services.gateway.deps import get_state
from captcha.services.gateway.state import AppState

router = APIRouter(tags=["health"], default_response_class=ORJSONResponse)

PROBE_TIMEOUT_SEC: Final[float] = 2.0


@router.get("/healthz")
async def healthz() -> ORJSONResponse:
    return ORJSONResponse({"ok": True})


@router.get("/readyz")
async def readyz(state: AppState = Depends(get_state)) -> ORJSONResponse:
    """200 only if Redis, Postgres and a fresh lane heartbeat all check out."""
    checks: dict[str, bool] = dict.fromkeys(("redis", "postgres", "lanes"), False)
    err: dict[str, str] = {}
    live_lanes: tuple[str, ...] = ()

    for name, ok, reason in await asyncio.gather(_check_redis(state), _check_postgres(state)):
        checks[name] = ok
        if reason is not None:
            err[name] = reason

    if checks["redis"]:
        name, ok, reason, live_lanes = await _check_lanes(state)
        checks[name] = ok
        if reason is not None:
            err[name] = reason
    else:
        err["lanes"] = "redis unavailable; lane heartbeats cannot be verified"

    healthy = all(checks.values())
    body: dict[str, object] = {"ok": healthy, **checks}
    if live_lanes:
        body["live_lanes"] = list(live_lanes)
    if err:
        body["errors"] = err
    return ORJSONResponse(body, status_code=200 if healthy else 503)


async def _check_redis(state: AppState) -> tuple[str, bool, str | None]:
    try:
        pong = await asyncio.wait_for(state.redis.ping(), timeout=PROBE_TIMEOUT_SEC)
        return "redis", bool(pong), None
    except Exception as exc:
        return "redis", False, _reason(exc)


async def _check_postgres(state: AppState) -> tuple[str, bool, str | None]:
    async def _ping() -> None:
        async with state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(_ping(), timeout=PROBE_TIMEOUT_SEC)
        return "postgres", True, None
    except Exception as exc:
        return "postgres", False, _reason(exc)


async def _check_lanes(state: AppState) -> tuple[str, bool, str | None, tuple[str, ...]]:
    """Fresh heartbeat from at least one lane (contract §2)."""

    async def _probe() -> tuple[str, ...]:
        registry = CapabilityRegistry(state.redis, prefix=state.settings.redis_stream_prefix)
        return tuple(await registry.live_lanes())

    try:
        lanes = await asyncio.wait_for(_probe(), timeout=PROBE_TIMEOUT_SEC)
    except Exception as exc:
        return "lanes", False, _reason(exc), ()
    if lanes:
        return "lanes", True, None, lanes
    return (
        "lanes",
        False,
        f"no live lane heartbeat in the last {HEARTBEAT_TTL_SECONDS}s",
        (),
    )


def _reason(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return f"probe timed out after {PROBE_TIMEOUT_SEC}s"
    return f"{type(exc).__name__}: {exc}"
