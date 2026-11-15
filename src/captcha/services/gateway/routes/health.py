"""Liveness + readiness. /healthz never touches dependencies; /readyz pings both.

The probes run concurrently and are wrapped in a short timeout on purpose: an
unreachable Postgres hangs on connect for the driver's default (tens of
seconds), which turns a container healthcheck into a hung request. Readiness has
to answer fast either way — 200 or 503 — and it must not take longer than one
probe just because there are two dependencies.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import ORJSONResponse
from sqlalchemy import text

from captcha.services.gateway.deps import get_state
from captcha.services.gateway.state import AppState

router = APIRouter(tags=["health"], default_response_class=ORJSONResponse)

PROBE_TIMEOUT_SEC = 2.0


@router.get("/healthz")
async def healthz() -> ORJSONResponse:
    return ORJSONResponse({"ok": True})


@router.get("/readyz")
async def readyz(state: AppState = Depends(get_state)) -> ORJSONResponse:
    """200 only if Redis + Postgres both answer within PROBE_TIMEOUT_SEC."""
    checks = dict.fromkeys(("redis", "postgres"), False)
    err: dict[str, str] = {}

    for name, ok, reason in await asyncio.gather(_check_redis(state), _check_postgres(state)):
        checks[name] = ok
        if reason is not None:
            err[name] = reason

    healthy = all(checks.values())
    return ORJSONResponse(
        {"ok": healthy, **checks, **({"errors": err} if err else {})},
        status_code=200 if healthy else 503,
    )


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


def _reason(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return f"probe timed out after {PROBE_TIMEOUT_SEC}s"
    return f"{type(exc).__name__}: {exc}"
