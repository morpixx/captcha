"""Shared `SolverContext` construction for the HTTP worker.

Everything on the context is a *shared* client/pool/registry — except HTTP,
which is session-per-task. We don't hand solvers a single `Client`; we hand
them a factory the worker's dispatch loop uses to build one bound to the
proxy chosen for *that* task. The solver sees `ctx.http` as a short-lived
client scoped to its call.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from captcha.adapters.jssandbox import LocalRuntime, Runtime, ScriptCache
from captcha.adapters.profiles import Store as FingerprintStore
from captcha.adapters.proxy import Pool as ProxyPool
from captcha.adapters.transport import Client as HTTPClient
from captcha.adapters.transport import pick_impersonate
from captcha.adapters.weights import Registry as ModelRegistry
from captcha.config import Settings
from captcha.domain.errors import ConfigError
from captcha.engines import global_registry as sensor_registry
from captcha.solvers import SolverContext

_DEFAULT_FP_DIR = "data/fingerprints"

if TYPE_CHECKING:
    from redis.asyncio import Redis


HTTPFactory = Callable[..., HTTPClient]


@dataclass(slots=True)
class WorkerContextBundle:
    """Container for everything the worker holds and tears down at shutdown.

    The `SolverContext` handed to each solver call is shallow-copied from
    `template` with `.http` swapped to a per-task `Client`. Everything else
    is shared.
    """

    settings: Settings
    redis: Redis
    pg: Any  # sqlalchemy AsyncEngine or None
    template: SolverContext
    http_factory: HTTPFactory
    js_runtime: Runtime | None
    proxy_pool: ProxyPool | None

    async def aclose(self) -> None:
        runner = (self.template.extra or {}).get("hcaptcha_js")
        if runner is not None:
            try:
                await runner.aclose()
            except Exception:
                structlog.get_logger(__name__).warning("hcaptcha_js.close_failed", exc_info=True)
        if self.js_runtime is not None:
            try:
                await self.js_runtime.close()
            except Exception:
                structlog.get_logger(__name__).warning("js_runtime.close_failed", exc_info=True)


def _load_proxy_pool(settings: Settings) -> ProxyPool | None:
    path = Path(settings.proxy_pool_file)
    if not path.exists():
        structlog.get_logger(__name__).info(
            "proxy_pool.file_missing", path=str(path), hint="running with no proxy pool"
        )
        return ProxyPool([])
    try:
        return ProxyPool.load_from_file(path)
    except Exception as exc:
        raise ConfigError(
            "failed to load proxy pool",
            context={"path": str(path), "error": str(exc)},
        ) from exc


def _build_hcaptcha_js(log: Any) -> Any:
    """Persistent Node bundle worker for the hCaptcha HTTP path.

    Absent Node/node_modules is not fatal: the solver reports ERR_CONFIG per
    task instead of taking the whole HTTP worker down at boot.
    """
    try:
        from captcha.adapters.hcaptcha_js.runner import JsBundleRunner

        return JsBundleRunner(node_binary=os.getenv("HCAPTCHA_JS_NODE", "node"))
    except Exception as exc:
        log.warning("hcaptcha_js.unavailable", error=str(exc))
        return None


def _build_js_runtime() -> Runtime | None:
    """LocalRuntime is the default; return None if py-mini-racer isn't available.

    Solvers that need JS must raise `ConfigError` on their own when
    `ctx.js_runtime is None` — this way a worker with no JS deps can still
    serve the pure-math/OCR load.
    """
    try:
        return LocalRuntime(cache=ScriptCache())
    except ConfigError:
        return None


def _make_http_factory(settings: Settings) -> HTTPFactory:
    impersonate_default = pick_impersonate(settings.worker_http_impersonate)

    def factory(
        *,
        proxy: str | None = None,
        impersonate: str | None = None,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> HTTPClient:
        return HTTPClient(
            impersonate=impersonate or impersonate_default,
            proxy=proxy,
            timeout=timeout,
            default_headers=headers,
        )

    return factory


async def build_context(
    settings: Settings,
    *,
    redis: Redis,
    pg: Any = None,
    logger: Any = None,
) -> WorkerContextBundle:
    """Wire the shared bits once at worker startup.

    `pg` is accepted but not forced — workers that don't persist go passing
    None; the DB-backed audit path lives in a separate consumer.

    Async because fingerprint `load_all` is awaitable; keeping the whole
    build in one place means callers don't partially-initialise the context.
    """
    log = logger or structlog.get_logger("captcha.worker.http")
    log.info("context.build.start")

    proxy_pool = _load_proxy_pool(settings)

    fp_store = FingerprintStore(_DEFAULT_FP_DIR)
    try:
        fp_count = await fp_store.load_all()
        log.info("fingerprints.loaded", count=fp_count, source=_DEFAULT_FP_DIR)
    except ConfigError as exc:
        # Missing S3 wiring or unparseable file: keep booting; solvers that
        # need fingerprints will fail individually with a clearer error.
        log.warning("fingerprints.load_failed", error=exc.message, context=exc.context)

    model_registry = ModelRegistry(
        s3_endpoint=settings.s3_endpoint,
        bucket=settings.s3_bucket,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )
    sensors = sensor_registry()
    js_runtime = _build_js_runtime()
    hcaptcha_js = _build_hcaptcha_js(log)
    http_factory = _make_http_factory(settings)

    template = SolverContext(
        http=None,  # replaced per-task
        js_runtime=js_runtime,
        proxy_pool=proxy_pool,
        fingerprint_store=fp_store,
        models=model_registry,
        sensors=sensors,
        config=settings,
        logger=log,
        extra={
            "redis": redis,
            "pg": pg,
            "http_factory": http_factory,
            "hcaptcha_js": hcaptcha_js,
        },
    )

    log.info(
        "context.build.done",
        proxies=len(proxy_pool) if proxy_pool is not None else 0,
        js_runtime="local" if js_runtime is not None else "none",
        cv_device=settings.worker_cv_device,
    )

    return WorkerContextBundle(
        settings=settings,
        redis=redis,
        pg=pg,
        template=template,
        http_factory=http_factory,
        js_runtime=js_runtime,
        proxy_pool=proxy_pool,
    )
