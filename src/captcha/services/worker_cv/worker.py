"""CV-lane worker.

GPU-bound solves (OCR, Whisper, ViT classification, YOLO detection, slider
gap-detect, rotate regressor). All solvers run via `solve_http` — the lane
label is a scheduling detail, not a transport one.

Lifecycle
---------
1. Connect Redis, open the `cv` consumer group, build `SolverContext` with a
   `captcha.adapters.weights.Registry` ready to lazy-pull weights. The context
   has no HTTP client of its own: `context.task_context` swaps in a fresh one
   per task, because the client carries that task's proxy and cookie jar.
2. Discover solvers via `captcha.solvers` entry points; keep those whose
   `info.types` route to the `cv` lane under `pick_lane`.
3. `prepare()` each solver once. Failures are logged and the solver is skipped.
4. Read loop: dispatch each task to its solver under a semaphore cap.
5. SIGINT/SIGTERM: drain in-flight tasks, release GPU, close Redis.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
import uuid
from typing import TYPE_CHECKING

import structlog

from captcha.adapters.queue import Consumer, TaskStateStore
from captcha.adapters.queue.keys import Lane
from captcha.adapters.storage.persistence import TaskPersistence, engine_session_factory
from captcha.adapters.weights import Registry as ModelRegistry
from captcha.config import Settings
from captcha.domain import Task, TaskEnvelope
from captcha.domain.enums import CaptchaType, SolveMode, TaskStatus
from captcha.domain.errors import CaptchaError, ConfigError, UnsolvableError
from captcha.domain.errors import TimeoutError as CaptchaTimeoutError
from captcha.domain.lanes import pick_lane
from captcha.observability import metrics
from captcha.solvers import Solver, SolverContext
from captcha.solvers.registry import SolverRegistry, discover_solvers, global_registry

from .context import HttpFactory, task_context

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

LANE: Lane = "cv"
DEFAULT_TIMEOUT_SEC: float = 60.0
DEFAULT_CONCURRENCY: int = 8
_MIN_TASK_TIMEOUT: float = 0.5


class Worker:
    """One CV-lane consumer. Share a process per GPU."""

    def __init__(
        self,
        settings: Settings,
        *,
        worker_id: str | None = None,
        concurrency: int | None = None,
        batch_size: int = 8,
        block_ms: int = 5_000,
        max_retries: int = 3,
        cache_dir: str = "./models-cache",
        registry: SolverRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.worker_id = worker_id or f"{LANE}-{uuid.uuid4().hex[:8]}"
        self.concurrency = concurrency or DEFAULT_CONCURRENCY
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._max_retries = max_retries
        self._cache_dir = cache_dir
        self._stop = asyncio.Event()
        self._redis: Redis | None = None
        self._consumer: Consumer | None = None
        self._state: TaskStateStore | None = None
        self._registry: SolverRegistry | None = registry
        # one Solver instance per solver class; one class can serve many types
        self._instances: dict[int, Solver] = {}
        self._by_type: dict[CaptchaType, Solver] = {}
        self._ctx: SolverContext | None = None
        self._sem: asyncio.Semaphore | None = None
        self._inflight: set[asyncio.Task[None]] = set()
        self._model_registry: ModelRegistry | None = None
        self._http_factory = HttpFactory(getattr(settings, "worker_http_impersonate", ""))
        self._log = log.bind(lane=LANE, worker=self.worker_id)

    # ---- lifecycle -----------------------------------------------------

    async def run(self) -> None:
        await self._setup()
        self._install_signals()
        self._log.info(
            "worker.start",
            concurrency=self.concurrency,
            solver_types=sorted(ct.value for ct in self._by_type),
            device=self.settings.worker_cv_device,
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
        self._persistence = TaskPersistence(engine_session_factory(self.settings.postgres_url))
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

        self._model_registry = ModelRegistry(
            s3_endpoint=self.settings.s3_endpoint,
            bucket=self.settings.s3_bucket,
            access_key=self.settings.s3_access_key or None,
            secret_key=self.settings.s3_secret_key or None,
            region=self.settings.s3_region,
            cache_dir=self._cache_dir,
        )

        self._ctx = SolverContext(
            models=self._model_registry,
            config=self.settings,
            logger=self._log,
            extra={
                "redis": self._redis,
                "device": self.settings.worker_cv_device,
            },
        )
        self._sem = asyncio.Semaphore(self.concurrency)

        if self._registry is None:
            self._registry = discover_solvers(registry=global_registry())
        await self._prepare_solvers()

    async def _prepare_solvers(self) -> None:
        assert self._registry is not None and self._ctx is not None
        # Non-experimental first: `_by_type` keeps the first hit, so this is
        # the same preference `pick()` applies.
        for cls in self._registry.resolution_order():
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
        """Keep only solver types that route here under `pick_lane`."""
        if not cls.info.http:
            # A browser-only solver has no `solve_http`; caching it here would
            # turn every task into the base-class "not supported" error (and a
            # retry loop). It belongs to the browser lane.
            return []
        accepted: list[CaptchaType] = []
        for ct in cls.info.types:
            fake = Task(
                id=uuid.uuid4(),
                type=ct,
                payload={},
                preferred_mode=SolveMode.AUTO,
                created_at=0.0,
            )
            if pick_lane(fake) == LANE:
                accepted.append(ct)
        return accepted

    def _install_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                # Windows ProactorEventLoop only wires SIGINT via signal.signal.
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
                    # Don't start new work mid-drain; the entry stays pending
                    # and another consumer (or us on restart) reclaims it.
                    break
                await self._sem.acquire()
                t = asyncio.create_task(
                    self._run_one_slot(message_id, envelope),
                    name=f"cv-task-{envelope.task.id}",
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
        solve_mode = SolveMode.HTTP  # cv solvers always implement solve_http
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
        # HTTP is per task, never shared: proxy, UA and cookies belong to it.
        ctx, client = task_context(self._ctx, self._http_factory, task)
        try:
            result = await asyncio.wait_for(solver.solve_http(task, ctx), timeout=timeout)
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
                solver_name=solver.info.name,
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
            await self._handle_failure(
                message_id, envelope, wrapped, solve_mode, terminal=False, solver_name=solver.info.name
            )
            return
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()

        latency_s = time.perf_counter() - start
        if not result.latency_ms:
            result.latency_ms = latency_s * 1000.0
        result.solve_mode = solve_mode
        try:
            await self._state.set_ready(task.id, result, finished_at=time.time())
        except Exception as exc:
            self._log.warning("state.set_ready_failed", error=str(exc))
        await self._persistence.ready(
            task.id,
            result=result,
            latency_ms=latency_s * 1000.0,
            solver_name=solver.info.name,
            task_type=task.type.value,
        )
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
            f"no solver registered for {task.type.value} on lane {LANE}",
            context={"type": task.type.value, "lane": LANE},
        )
        self._log.error("solver.missing", task_id=str(task.id), type=task.type.value)
        try:
            await self._state.set_failed(task.id, err.to_dict(), finished_at=time.time())
        except Exception as exc:
            self._log.warning("state.set_failed_failed", error=str(exc))
        await self._persistence.failed(
            task.id,
            error=err.to_dict(),
            latency_ms=0.0,
            solver_name="none",
            task_type=task.type.value,
            refund=True,
        )
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
        solver_name: str = "none",
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
            await self._persistence.failed(
                task.id,
                error=exc.to_dict(),
                latency_ms=0.0,
                solver_name=solver_name,
                task_type=task.type.value,
                refund=True,
            )
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
                await self._persistence.failed(
                    task.id,
                    error=exc.to_dict(),
                    latency_ms=0.0,
                    solver_name=solver_name,
                    task_type=task.type.value,
                    refund=True,
                )
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
        if self._model_registry is not None:
            with contextlib.suppress(Exception):
                self._model_registry.stop_watch()
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
