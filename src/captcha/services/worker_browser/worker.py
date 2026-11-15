"""Browser-lane worker.

Fallback transport. Camoufox > patchright > vanilla playwright, picked by
`captcha.adapters.browser.Pool.launch()`. Workers share one Pool per process; solvers
`async with ctx.browser.acquire() as sess` to drive a context themselves.

Lifecycle
---------
1. Connect Redis, open the `browser` consumer group.
2. Launch `captcha.adapters.browser.Pool(size = settings.worker_browser_sessions)`.
3. Load proxy pool + fingerprint store into the shared `SolverContext`.
4. Discover solvers via `captcha.solvers` entry points; keep those whose
   `info.types` route to the `browser` lane under `pick_lane` AND whose
   `info.browser` flag is set.
5. `prepare()` each solver once.
6. Read loop: dispatch each task under a semaphore cap. Call `solve_browser`
   (never `solve_http` on this lane).
7. SIGINT/SIGTERM: drain in-flight tasks, close the browser pool, close Redis.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from captcha.adapters.browser import Pool as BrowserPool
from captcha.adapters.profiles import Store as FingerprintStore
from captcha.adapters.proxy import Pool as ProxyPool
from captcha.adapters.queue import Consumer, TaskStateStore
from captcha.adapters.queue.keys import Lane
from captcha.config import Settings
from captcha.domain import Task, TaskEnvelope
from captcha.domain.enums import CaptchaType, SolveMode, TaskStatus
from captcha.domain.errors import CaptchaError, ConfigError, UnsolvableError
from captcha.domain.errors import TimeoutError as CaptchaTimeoutError
from captcha.domain.lanes import pick_lane
from captcha.engines import global_registry as sensor_registry
from captcha.observability import metrics
from captcha.solvers import Solver, SolverContext
from captcha.solvers.registry import SolverRegistry, discover_solvers, global_registry

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

LANE: Lane = "browser"
DEFAULT_TIMEOUT_SEC: float = 180.0
DEFAULT_CONCURRENCY: int = 20
_MIN_TASK_TIMEOUT: float = 1.0


class Worker:
    """One browser-lane consumer. Multiple consumers share one Pool."""

    def __init__(
        self,
        settings: Settings,
        *,
        worker_id: str | None = None,
        concurrency: int | None = None,
        batch_size: int = 4,
        block_ms: int = 5_000,
        max_retries: int = 2,
        headless: bool = True,
        browser_pool: BrowserPool | None = None,
        registry: SolverRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.worker_id = worker_id or f"{LANE}-{uuid.uuid4().hex[:8]}"
        self.concurrency = concurrency or settings.worker_browser_sessions or DEFAULT_CONCURRENCY
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._max_retries = max_retries
        self._headless = headless
        self._stop = asyncio.Event()
        self._redis: Redis | None = None
        self._consumer: Consumer | None = None
        self._state: TaskStateStore | None = None
        self._registry: SolverRegistry | None = registry
        self._instances: dict[int, Solver] = {}
        self._by_type: dict[CaptchaType, Solver] = {}
        self._ctx: SolverContext | None = None
        self._sem: asyncio.Semaphore | None = None
        self._inflight: set[asyncio.Task[None]] = set()
        self._browser: BrowserPool | None = browser_pool
        self._owns_browser: bool = browser_pool is None
        self._proxy_pool: ProxyPool | None = None
        self._log = log.bind(lane=LANE, worker=self.worker_id)

    # ---- lifecycle -----------------------------------------------------

    async def run(self) -> None:
        await self._setup()
        self._install_signals()
        self._log.info(
            "worker.start",
            concurrency=self.concurrency,
            solver_types=sorted(ct.value for ct in self._by_type),
            browser_size=self.settings.worker_browser_sessions,
        )
        try:
            await self._loop()
        finally:
            await self._teardown()

    async def _setup(self) -> None:
        from redis.asyncio import Redis

        self._redis = Redis.from_url(self.settings.redis_url, decode_responses=False)
        try:
            await self._redis.ping()
        except Exception as exc:
            raise ConfigError(
                "redis ping failed",
                context={"url": self.settings.redis_url, "error": str(exc)},
            ) from exc

        self._state = TaskStateStore(self._redis, prefix=self.settings.redis_stream_prefix)
        self._consumer = Consumer(
            self._redis,
            lane=LANE,
            consumer_name=self.worker_id,
            prefix=self.settings.redis_stream_prefix,
            block_ms=self._block_ms,
            count=self._batch_size,
            max_retries=self._max_retries,
        )
        await self._consumer.ensure_group()

        if self._browser is None:
            self._browser = BrowserPool(
                size=self.settings.worker_browser_sessions or DEFAULT_CONCURRENCY,
                headless=self._headless,
                # backend=None → auto: camoufox → patchright → playwright
            )
            await self._browser.launch()
            self._log.info(
                "browser.pool.launched",
                size=self._browser.size,
                headless=self._headless,
            )

        self._proxy_pool = _load_proxy_pool(self.settings, self._log)
        fp_store = FingerprintStore("data/fingerprints")

        self._ctx = SolverContext(
            proxy_pool=self._proxy_pool,
            fingerprint_store=fp_store,
            sensors=sensor_registry(),
            browser=self._browser,
            config=self.settings,
            logger=self._log,
            extra={"redis": self._redis},
        )
        self._sem = asyncio.Semaphore(self.concurrency)

        if self._registry is None:
            self._registry = discover_solvers(registry=global_registry())
        await self._prepare_solvers()

    async def _prepare_solvers(self) -> None:
        assert self._registry is not None and self._ctx is not None
        for cls in self._registry.all_solvers():
            # Browser lane only takes solvers that opted in via info.browser.
            if not cls.info.browser:
                continue
            accepted = self._accepted_types(cls)
            if not accepted:
                continue
            key = id(cls)
            instance = self._instances.get(key)
            if instance is None:
                try:
                    instance = cls()
                    await instance.prepare(self._ctx)
                except Exception as exc:
                    self._log.error(
                        "solver.prepare_failed",
                        solver=cls.info.name,
                        error=str(exc),
                        exc_info=True,
                    )
                    continue
                self._instances[key] = instance
            for ct in accepted:
                if ct not in self._by_type:
                    self._by_type[ct] = instance
            self._log.info(
                "solver.ready",
                solver=cls.info.name,
                types=[t.value for t in accepted],
                version=cls.info.version,
            )

    def _accepted_types(self, cls: type[Solver]) -> list[CaptchaType]:
        """Keep types that route here either by preference or by type default."""
        accepted: list[CaptchaType] = []
        for ct in cls.info.types:
            # Browser lane matches two cases:
            #  1. preferred_mode=BROWSER on any type (explicit URL-mode request)
            #  2. type default routes to browser (none today; reserved)
            fake_browser = Task(
                id=uuid.uuid4(),
                type=ct,
                payload={},
                preferred_mode=SolveMode.BROWSER,
                created_at=0.0,
            )
            if pick_lane(fake_browser) == LANE:
                accepted.append(ct)
        return accepted

    def _install_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                with contextlib.suppress(OSError, ValueError):
                    signal.signal(sig, lambda _s, _f: self._stop.set())

    # ---- read loop -----------------------------------------------------

    async def _loop(self) -> None:
        assert self._consumer is not None and self._sem is not None
        while not self._stop.is_set():
            try:
                batch = await self._consumer.read()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.error("consumer.read_failed", error=str(exc), exc_info=True)
                await asyncio.sleep(1.0)
                continue

            for message_id, envelope in batch:
                if self._stop.is_set():
                    break
                await self._sem.acquire()
                t = asyncio.create_task(
                    self._run_one_slot(message_id, envelope),
                    name=f"browser-task-{envelope.task.id}",
                )
                self._inflight.add(t)
                t.add_done_callback(self._inflight.discard)

        self._log.info("worker.drain", inflight=len(self._inflight))
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)

    async def _run_one_slot(self, message_id: str, envelope: TaskEnvelope) -> None:
        try:
            await self._run_one(message_id, envelope)
        finally:
            assert self._sem is not None
            self._sem.release()

    # ---- per-task ------------------------------------------------------

    async def _run_one(self, message_id: str, envelope: TaskEnvelope) -> None:
        assert self._state is not None and self._consumer is not None and self._ctx is not None

        task = envelope.task
        solve_mode = SolveMode.BROWSER
        metrics.active_tasks.labels(lane=LANE).inc()
        start = time.perf_counter()
        wall_start = time.time()
        try:
            await self._state.set_processing(task.id, self.worker_id, started_at=wall_start)
        except Exception as exc:
            self._log.warning("state.set_processing_failed", error=str(exc))

        solver = self._by_type.get(task.type)
        if solver is None:
            await self._handle_missing_solver(message_id, envelope, solve_mode)
            return

        timeout = self._resolve_timeout(task)
        try:
            result = await asyncio.wait_for(solver.solve_browser(task, self._ctx), timeout=timeout)
        except TimeoutError:
            await self._handle_failure(
                message_id,
                envelope,
                CaptchaTimeoutError(
                    f"solver exceeded {timeout:.1f}s",
                    context={"timeout_s": timeout, "type": task.type.value},
                ),
                solve_mode,
                terminal=False,
            )
            return
        except CaptchaError as exc:
            await self._handle_failure(
                message_id,
                envelope,
                exc,
                solve_mode,
                terminal=isinstance(exc, UnsolvableError),
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log.exception("solver.unknown_error", task_id=str(task.id), type=task.type.value)
            wrapped = CaptchaError(
                str(exc) or type(exc).__name__,
                context={"exc_type": type(exc).__name__, "type": task.type.value},
            )
            await self._handle_failure(message_id, envelope, wrapped, solve_mode, terminal=False)
            return

        latency_s = time.perf_counter() - start
        if not result.latency_ms:
            result.latency_ms = latency_s * 1000.0
        result.solve_mode = solve_mode
        try:
            await self._state.set_ready(task.id, result, finished_at=time.time())
        except Exception as exc:
            self._log.warning("state.set_ready_failed", error=str(exc))
        await self._consumer.ack(message_id)
        metrics.active_tasks.labels(lane=LANE).dec()
        metrics.task_latency_seconds.labels(type=task.type.value, solve_mode=solve_mode.value).observe(latency_s)
        metrics.tasks_done_total.labels(
            type=task.type.value, status=TaskStatus.READY.value, solve_mode=solve_mode.value
        ).inc()
        self._log.info(
            "task.done",
            task_id=str(task.id),
            type=task.type.value,
            latency_ms=round(latency_s * 1000.0, 1),
        )

    async def _handle_missing_solver(
        self,
        message_id: str,
        envelope: TaskEnvelope,
        solve_mode: SolveMode,
    ) -> None:
        assert self._state is not None and self._consumer is not None
        task = envelope.task
        err = UnsolvableError(
            f"no browser solver registered for {task.type.value}",
            context={"type": task.type.value, "lane": LANE},
        )
        self._log.error("solver.missing", task_id=str(task.id), type=task.type.value)
        try:
            await self._state.set_failed(task.id, err.to_dict(), finished_at=time.time())
        except Exception as exc:
            self._log.warning("state.set_failed_failed", error=str(exc))
        await self._consumer.ack(message_id)
        metrics.active_tasks.labels(lane=LANE).dec()
        metrics.solver_errors_total.labels(type=task.type.value, error_code=err.code).inc()
        metrics.tasks_done_total.labels(
            type=task.type.value, status=TaskStatus.FAILED.value, solve_mode=solve_mode.value
        ).inc()

    async def _handle_failure(
        self,
        message_id: str,
        envelope: TaskEnvelope,
        exc: CaptchaError,
        solve_mode: SolveMode,
        *,
        terminal: bool,
    ) -> None:
        assert self._state is not None and self._consumer is not None
        task = envelope.task
        metrics.solver_errors_total.labels(type=task.type.value, error_code=exc.code).inc()
        if terminal:
            await self._consumer.ack(message_id)
            try:
                await self._state.set_failed(task.id, exc.to_dict(), finished_at=time.time())
            except Exception as exc2:
                self._log.warning("state.set_failed_failed", error=str(exc2))
            metrics.tasks_done_total.labels(
                type=task.type.value, status=TaskStatus.FAILED.value, solve_mode=solve_mode.value
            ).inc()
            self._log.warning(
                "task.failed_terminal",
                task_id=str(task.id),
                type=task.type.value,
                code=exc.code,
                message=exc.message,
            )
        else:
            dead = await self._consumer.fail(message_id, envelope, reason=exc.code)
            if dead:
                try:
                    await self._state.set_failed(task.id, exc.to_dict(), finished_at=time.time())
                except Exception as exc2:
                    self._log.warning("state.set_failed_failed", error=str(exc2))
                metrics.tasks_done_total.labels(
                    type=task.type.value,
                    status=TaskStatus.FAILED.value,
                    solve_mode=solve_mode.value,
                ).inc()
                self._log.warning(
                    "task.dead_lettered",
                    task_id=str(task.id),
                    code=exc.code,
                    retries=envelope.retries,
                )
            else:
                self._log.info(
                    "task.retry_scheduled",
                    task_id=str(task.id),
                    code=exc.code,
                    next_attempt=envelope.retries + 1,
                )
        metrics.active_tasks.labels(lane=LANE).dec()

    def _resolve_timeout(self, task: Task) -> float:
        if task.deadline_ts:
            remaining = task.deadline_ts - time.time()
            if remaining <= _MIN_TASK_TIMEOUT:
                return _MIN_TASK_TIMEOUT
            return min(remaining, DEFAULT_TIMEOUT_SEC)
        return DEFAULT_TIMEOUT_SEC

    # ---- teardown ------------------------------------------------------

    async def _teardown(self) -> None:
        self._log.info("worker.teardown")
        for inst in list(self._instances.values()):
            with contextlib.suppress(Exception):
                await inst.shutdown()
        if self._browser is not None and self._owns_browser:
            with contextlib.suppress(Exception):
                await self._browser.close_all()
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()


def _load_proxy_pool(settings: Settings, logger) -> ProxyPool | None:
    path = Path(settings.proxy_pool_file)
    if not path.exists():
        logger.info(
            "proxy_pool.file_missing",
            path=str(path),
            hint="browser worker running with no proxy pool",
        )
        return ProxyPool([])
    try:
        return ProxyPool.load_from_file(path)
    except Exception as exc:
        # Non-fatal: the browser lane can still drive a headful challenge
        # without a proxy, it's just useful in practice.
        logger.warning("proxy_pool.load_failed", path=str(path), error=str(exc))
        return ProxyPool([])
