"""Application factory + lifespan. The gateway owns:

- one AsyncEngine (Postgres)
- one Redis client
- one TaskStateStore + one Producer (built on top of that Redis client)

Each is created in `lifespan` and stashed on `app.state.ctx` as an AppState;
everything else goes through the dependency in `deps.py`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker

from captcha_common.config import Settings, get_settings
from captcha_db import init_engine, shutdown_engine
from captcha_gateway.errors import validation_error_handler
from captcha_gateway.metrics import build_metrics_app
from captcha_gateway.routes import include_routers
from captcha_gateway.state import AppState
from captcha_observability.logging import setup_logging
from captcha_queue import Producer, TaskStateStore

if TYPE_CHECKING:
    pass

_log = structlog.get_logger(__name__)


async def build_state(settings: Settings) -> AppState:
    """Open connections. Call once at startup; `shutdown_state` reverses it."""
    engine = init_engine(settings.postgres_url, pool_size=settings.postgres_pool_size)
    redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=False,
        health_check_interval=30,
        socket_keepalive=True,
    )
    state_store = TaskStateStore(redis, prefix=settings.redis_stream_prefix)
    producer = Producer(redis, prefix=settings.redis_stream_prefix)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    return AppState(
        settings=settings,
        redis=redis,
        engine=engine,
        sessionmaker=sm,
        state_store=state_store,
        producer=producer,
    )


async def shutdown_state(state: AppState) -> None:
    try:
        await state.redis.aclose()
    except Exception:  # noqa: BLE001
        _log.exception("redis.close_failed")
    try:
        await shutdown_engine()
    except Exception:  # noqa: BLE001
        _log.exception("pg.close_failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    setup_logging(level=cfg.log_level, json=cfg.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = await build_state(cfg)
        app.state.ctx = state
        _log.info("gateway.started", redis=cfg.redis_url.split("@")[-1],
                  pg=cfg.postgres_url.split("@")[-1])
        try:
            yield
        finally:
            _log.info("gateway.stopping")
            await shutdown_state(state)

    app = FastAPI(
        title="captcha-gateway",
        version="0.1.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    include_routers(app)
    app.mount("/metrics", build_metrics_app())
    return app
