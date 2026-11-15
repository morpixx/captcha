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

from captcha.adapters.queue import Producer, TaskStateStore
from captcha.adapters.queue.capabilities import gateway_live_types
from captcha.adapters.storage import init_engine, shutdown_engine
from captcha.config import Settings, get_settings
from captcha.domain.catalog import lane_for
from captcha.domain.enums import CaptchaType, SolveMode
from captcha.domain.redaction import redact_dsn
from captcha.observability.logging import setup_logging
from captcha.services.gateway.errors import validation_error_handler
from captcha.services.gateway.metrics import build_metrics_app
from captcha.services.gateway.routes import include_routers
from captcha.services.gateway.state import AppState

if TYPE_CHECKING:
    from captcha.solvers.registry import SolverRegistry

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
    solver_registry = _load_solvers()
    registry_types = frozenset(solver_registry.all_types())
    try:
        live_types = await gateway_live_types(redis, prefix=settings.redis_stream_prefix)
    except Exception as exc:
        # A Redis hiccup must not silently make the gateway sell unsolvable work.
        _log.warning("gateway.capabilities_failed", error=str(exc))
        live_types = frozenset()
    solver_types = sellable_types(
        solver_registry,
        live_types,
        allow_experimental=settings.allow_experimental_tasks,
    )
    _log.info(
        "gateway.solvers",
        count=len(solver_types),
        registry=len(registry_types),
        live=len(live_types),
        allow_experimental=settings.allow_experimental_tasks,
    )
    return AppState(
        settings=settings,
        redis=redis,
        engine=engine,
        sessionmaker=sm,
        state_store=state_store,
        producer=producer,
        solver_types=solver_types,
    )


def _available_types(
    registry_types: frozenset[CaptchaType],
    live_types: frozenset[CaptchaType],
) -> frozenset[CaptchaType]:
    """Types the gateway may sell right now.

    A browser-lane type is only sellable with a fresh browser-worker heartbeat:
    a registered solver is not proof that the expensive lane is up. Everything
    else keeps the registry as the source of truth.
    """
    browser_lane = {ct for ct in CaptchaType if lane_for(ct) == "browser"}
    return frozenset((registry_types - browser_lane) | live_types)


def _load_solvers() -> SolverRegistry:
    """Load the solver registry once at boot.

    The gateway refuses types it cannot solve up front (`ERROR_TASK_NOT_SUPPORTED`)
    instead of charging the client and letting the task rot until the deadline.
    Loading the registry costs one import pass and mirrors what every worker does.
    """
    from captcha.solvers import discover_solvers

    return discover_solvers()


def sellable_types(
    registry: SolverRegistry,
    live_types: frozenset[CaptchaType],
    *,
    allow_experimental: bool,
) -> frozenset[CaptchaType]:
    """Types the gateway may sell right now (contract §7).

    `allow_experimental` defaults to True in Settings for the current beta, so
    the filter only runs when an operator sets ALLOW_EXPERIMENTAL_TASKS=0. A
    type stays sellable while it has at least one non-experimental solver; a
    live heartbeat advertising an experimental-only type does not override it.
    """
    available = _available_types(frozenset(registry.all_types()), live_types)
    if allow_experimental:
        return available
    experimental_only = {
        ct for ct in registry.all_types() if registry.pick(ct, mode=SolveMode.AUTO, allow_experimental=False) is None
    }
    return available - experimental_only


async def shutdown_state(state: AppState) -> None:
    try:
        await state.redis.aclose()
    except Exception:
        _log.exception("redis.close_failed")
    try:
        await shutdown_engine()
    except Exception:
        _log.exception("pg.close_failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    setup_logging(level=cfg.log_level, json=cfg.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = await build_state(cfg)
        app.state.ctx = state
        _log.info("gateway.started", redis=redact_dsn(cfg.redis_url), pg=redact_dsn(cfg.postgres_url))
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
