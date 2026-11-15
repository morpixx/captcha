"""Application factory + lifespan. The gateway owns:

- one AsyncEngine (Postgres)
- one Redis client
- one TaskStateStore + one Producer (built on top of that Redis client)

Each is created in `lifespan` and stashed on `app.state.ctx` as an AppState;
everything else goes through the dependency in `deps.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
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
    extra_types = parse_extra_types(settings.captcha_extra_types)
    solver_types = sellable_types(
        solver_registry,
        live_types,
        allow_experimental=settings.allow_experimental_tasks,
        extra_types=extra_types,
    )
    _log.info(
        "gateway.solvers",
        count=len(solver_types),
        registry=len(registry_types),
        live=len(live_types),
        extra=len(extra_types),
        allow_experimental=settings.allow_experimental_tasks,
    )
    return AppState(
        settings=settings,
        redis=redis,
        engine=engine,
        sessionmaker=sm,
        state_store=state_store,
        producer=producer,
        solver_registry=solver_registry,
        solver_types=solver_types,
    )


def _available_types(
    registry_types: frozenset[CaptchaType],
    live_types: frozenset[CaptchaType],
    extra_types: frozenset[CaptchaType] = frozenset(),
) -> frozenset[CaptchaType]:
    """Types the gateway may sell right now (contract §7).

    `registry ∪ CAPTCHA_EXTRA_TYPES ∪ heartbeat caps`. A browser-lane type is
    only sellable with a fresh browser-worker heartbeat: a registered solver (or
    a manual declaration) is not proof that the expensive lane is up. Everything
    else keeps the registry as the source of truth.
    """
    browser_lane = {ct for ct in CaptchaType if lane_for(ct) == "browser"}
    declared = registry_types | extra_types
    return frozenset((declared - browser_lane) | live_types)


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
    extra_types: frozenset[CaptchaType] = frozenset(),
) -> frozenset[CaptchaType]:
    """Types the gateway may sell right now (contract §7).

    `allow_experimental` defaults to True in Settings for the current beta, so
    the filter only runs when an operator sets ALLOW_EXPERIMENTAL_TASKS=0. A
    type stays sellable while it has at least one non-experimental solver; a
    live heartbeat advertising an experimental-only type does not override it.
    """
    available = _available_types(frozenset(registry.all_types()), live_types, extra_types)
    if allow_experimental:
        return available
    experimental_only = {
        ct for ct in registry.all_types() if registry.pick(ct, mode=SolveMode.AUTO, allow_experimental=False) is None
    }
    return available - experimental_only


def parse_extra_types(raw: str | None) -> frozenset[CaptchaType]:
    """`CAPTCHA_EXTRA_TYPES=a,b` — manual additions to the sellable set.

    An operator escape hatch, not a config schema: unknown names are logged and
    skipped instead of failing the boot, and the heartbeat path is untouched.
    """
    if not raw:
        return frozenset()
    declared: set[CaptchaType] = set()
    for item in raw.replace(";", ",").split(","):
        value = item.strip().lower()
        if not value:
            continue
        try:
            declared.add(CaptchaType(value))
        except ValueError:
            _log.warning("gateway.extra_type_unknown", value=value)
    return frozenset(declared)


class LiveTypesRefresher:
    """Recompute `state.solver_types` from live heartbeats on a timer.

    The boot snapshot alone is not enough: a worker that dies after startup
    stays sellable until the gateway restarts. Every tick re-reads the proof
    (`captcha:lane:*` + `captcha:cap:*`) and swaps the frozenset in one
    assignment, so a request either sees the old set or the new one.

    `sleep` is injectable: tests drive the loop with a fake clock instead of
    waiting 20 s.
    """

    def __init__(
        self,
        state: AppState,
        registry: SolverRegistry,
        *,
        interval_sec: float | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._state = state
        self._registry = registry
        self._interval = float(interval_sec if interval_sec is not None else state.settings.gateway_types_refresh_sec)
        self._extra = parse_extra_types(state.settings.captcha_extra_types)
        self._sleep = sleep or asyncio.sleep
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def interval_sec(self) -> float:
        return self._interval

    @property
    def extra_types(self) -> frozenset[CaptchaType]:
        return self._extra

    async def tick(self) -> frozenset[CaptchaType]:
        """Read one capability snapshot and publish it.

        A Redis read failure degrades to registry-only instead of raising; the
        loop tolerates anything else and keeps the gateway serving.
        """
        try:
            live_types = await gateway_live_types(
                self._state.redis,
                prefix=self._state.settings.redis_stream_prefix,
            )
        except Exception as exc:
            # A Redis hiccup must not silently make the gateway sell unsolvable
            # work: an unknown fleet sells registry types only, and browser
            # types drop out because there is no proof the lane is alive.
            _log.warning("gateway.capabilities_failed", error=str(exc))
            live_types = frozenset()
        sold = sellable_types(
            self._registry,
            live_types,
            allow_experimental=self._state.settings.allow_experimental_tasks,
            extra_types=self._extra,
        )
        previous = self._state.solver_types
        self._state.solver_types = sold
        if sold != previous:
            _log.info("gateway.solver_types", count=len(sold), live=len(live_types), extra=len(self._extra))
        return sold

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        await self.tick()
        self._task = asyncio.create_task(self._loop(), name="gateway-capability-refresh")
        _log.info("gateway.capabilities_refresh", interval_sec=self._interval, extra=len(self._extra))

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._sleep(self._interval)
            except asyncio.CancelledError:
                raise
            if self._stop.is_set():
                break
            try:
                await self.tick()
            except Exception:  # a refresh fault must not kill the gateway
                _log.exception("gateway.capabilities_refresh_failed")

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


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
        refresher = LiveTypesRefresher(state, state.solver_registry)
        await refresher.start()
        _log.info("gateway.started", redis=redact_dsn(cfg.redis_url), pg=redact_dsn(cfg.postgres_url))
        try:
            yield
        finally:
            _log.info("gateway.stopping")
            await refresher.stop()
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
