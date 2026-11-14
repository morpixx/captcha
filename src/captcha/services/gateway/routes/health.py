"""Liveness + readiness. /healthz never touches dependencies; /readyz pings both."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import ORJSONResponse
from sqlalchemy import text

from captcha.services.gateway.deps import get_state
from captcha.services.gateway.state import AppState

router = APIRouter(tags=["health"], default_response_class=ORJSONResponse)


@router.get("/healthz")
async def healthz() -> ORJSONResponse:
    return ORJSONResponse({"ok": True})


@router.get("/readyz")
async def readyz(state: AppState = Depends(get_state)) -> ORJSONResponse:
    """200 only if Redis + Postgres both answer."""
    ok_redis = False
    ok_pg = False
    err: dict[str, str] = {}

    try:
        pong = await state.redis.ping()
        ok_redis = bool(pong)
    except Exception as exc:
        err["redis"] = str(exc)

    try:
        async with state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        ok_pg = True
    except Exception as exc:
        err["postgres"] = str(exc)

    status_code = 200 if (ok_redis and ok_pg) else 503
    return ORJSONResponse(
        {"ok": ok_redis and ok_pg, "redis": ok_redis, "postgres": ok_pg, **({"errors": err} if err else {})},
        status_code=status_code,
    )
