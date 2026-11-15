"""Browser-lane worker: composition, read loop, drain.

Contract surface (docs/production-contract.md §2–§4):

* **Per-task provider, no shared pool.** A solver gets `ctx.browser` —
  `adapters.browser.provider.CamoufoxProvider` — and opens one fresh Camoufox
  session per task. The worker owns no browser and no context pool;
  `resolve_provider()` builds the provider once per process (see `cli.py`).
* **Concurrency.** `WORKER_BROWSER_SESSIONS` (12) sessions per process, ramped
  up from `WORKER_BROWSER_RAMP_START` while free RAM stays above
  `WORKER_BROWSER_MIN_FREE_MB` (`gate.ConcurrencyGate`).
* **Hard timeout.** 180–300 s per task (`limits.clamp_timeout`), enforced by
  `runner.TaskRunner`, which kills the task's process tree.
* **Heartbeat.** `SETEX captcha:lane:browser:<worker> 90 1`, `SADD
  captcha:cap:browser <types>` (`heartbeat.LaneHeartbeat` over
  `adapters/queue/capabilities`).
* **Graceful drain.** SIGINT/SIGTERM stops reads, in-flight tasks finish, then
  every remaining session is killed through the provider's supervisor.

Per task only `solve_browser` runs; `ERR_NEEDS_BROWSER` is terminal here.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from captcha.adapters.profiles import Store as FingerprintStore
from captcha.adapters.proxy import Pool as ProxyPool
from captcha.adapters.queue import Consumer, TaskStateStore
from captcha.config import Settings
from captcha.domain import TaskEnvelope
from captcha.domain.enums import CaptchaType
from captcha.domain.errors import ConfigError
from captcha.engines import global_registry as sensor_registry
from captcha.observability import metrics
from captcha.solvers import Solver, SolverContext
from captcha.solvers.registry import SolverRegistry, discover_solvers, global_registry

from .gate import ConcurrencyGate
from .heartbeat import LaneHeartbeat
from .limits import DEFAULT_CONCURRENCY, DEFAULT_TIMEOUT_SEC, LANE, clamp_timeout
from .provider import close_provider, resolve_provider
from .runner import TaskRunner
from .tracking import SessionTracker, TrackingProvider, bind_label, reset_label, task_label

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

__all__ = ["DEFAULT_CONCURRENCY", "DEFAULT_TIMEOUT_SEC", "LANE", "Worker"]


class Worker:
    """One browser-lane consumer. Consumers in a process share the provider."""

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
        provider: Any = None,
        registry: SolverRegistry | None = None,
        consumer: Any = None,
        state: Any = None,
        heartbeat: Any = None,
        gate: ConcurrencyGate | None = None,
        tracker: SessionTracker | None = None,
        timeout_sec: float | None = None,
        install_signals: bool = True,
    ) -> None:
        self.settings = settings
        self.worker_id = worker_id or f"{LANE}-{uuid.uuid4().hex[:8]}"
        default_sessions = int(getattr(settings, "worker_browser_sessions", DEFAULT_CONCURRENCY) or DEFAULT_CONCURRENCY)
        self.concurrency = max(1, concurrency or default_sessions)
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._max_retries = max_retries
        self._headless = headless
        self._signals_enabled = install_signals
        # Explicit timeout is for tests/short-lived probes; settings are clamped
        # to the contract's 180–300 s window.
        self._hard_timeout = (
            float(timeout_sec)
            if timeout_sec is not None
            else clamp_timeout(float(getattr(settings, "worker_browser_task_timeout_sec", DEFAULT_TIMEOUT_SEC)))
        )
        self._ramp_start = int(getattr(settings, "worker_browser_ramp_start", 2) or 1)
        self._min_free_mb = int(getattr(settings, "worker_browser_min_free_mb", 0) or 0)
        self._provider = provider
        self._owns_provider = provider is None
        self._tracker = tracker
        self._stop = asyncio.Event()
        self._redis: Redis | None = None
        self._consumer = consumer
        self._state = state
        self._heartbeat = heartbeat
        self._registry = registry
        self._instances: dict[int, Solver] = {}
        self._by_type: dict[CaptchaType, Solver] = {}
        self._ctx: SolverContext | None = None
        self._runner: TaskRunner | None = None
        self._gate = gate
        self._inflight: set[asyncio.Task[None]] = set()
        self._proxy_pool: ProxyPool | None = None
        self._log = log.bind(lane=LANE, worker=self.worker_id)

    # ---- lifecycle -----------------------------------------------------

    async def run(self) -> None:
        await self._setup()
        if self._signals_enabled:
            self._install_signals()
        self._log.info(
            "worker.start",
            concurrency=self.concurrency,
            timeout_sec=self._hard_timeout,
            min_free_mb=self._min_free_mb,
            solver_types=sorted(ct.value for ct in self._by_type),
        )
        try:
            await self._loop()
        finally:
            await self._teardown()

    def request_stop(self) -> None:
        self._stop.set()

    async def _setup(self) -> None:
        if self._consumer is None or self._state is None:
            await self._connect_redis()
        if self._state is None:
            assert self._redis is not None
            self._state = TaskStateStore(self._redis, prefix=self.settings.redis_stream_prefix)
        if self._consumer is None:
            assert self._redis is not None
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

        if self._provider is None:
            self._provider = resolve_provider(self.settings, headless=self._headless)
            self._log.info("browser.provider.started", provider=type(self._provider).__name__)
        if self._tracker is None:
            self._tracker = SessionTracker(self._provider)
        tracked = TrackingProvider(self._provider, self._tracker)

        self._proxy_pool = _load_proxy_pool(self.settings, self._log)
        self._ctx = SolverContext(
            proxy_pool=self._proxy_pool,
            fingerprint_store=FingerprintStore("data/fingerprints"),
            sensors=sensor_registry(),
            browser=tracked,
            config=self.settings,
            logger=self._log,
            extra={"redis": self._redis},
        )
        if self._gate is None:
            self._gate = ConcurrencyGate(
                self.concurrency,
                min_free_mb=self._min_free_mb,
                ramp_start=self._ramp_start,
            )

        if self._registry is None:
            self._registry = discover_solvers(registry=global_registry())
        await self._prepare_solvers()
        self._runner = TaskRunner(
            state=self._state,
            consumer=self._consumer,
            ctx=self._ctx,
            solvers=self._by_type,
            hard_timeout=self._hard_timeout,
            worker_id=self.worker_id,
            tracker=self._tracker,
            logger=self._log,
        )

        if self._heartbeat is None and self._redis is not None:
            self._heartbeat = LaneHeartbeat(
                self._redis,
                worker_id=self.worker_id,
                lane=LANE,
                prefix=self.settings.redis_stream_prefix,
                types=tuple(self._by_type),
            )
        if self._heartbeat is not None:
            setter = getattr(self._heartbeat, "set_types", None)
            if callable(setter):
                setter(tuple(self._by_type))
            starter = getattr(self._heartbeat, "start", None)
            if callable(starter):
                await starter()

    async def _connect_redis(self) -> None:
        # Local import: keeps redis out of the import graph for mock-only tests.
        from redis.asyncio import Redis

        self._redis = Redis.from_url(self.settings.redis_url, decode_responses=False)
        try:
            await self._redis.ping()
        except Exception as exc:
            raise ConfigError(
                "redis ping failed",
                context={"url": self.settings.redis_url, "error": str(exc)},
            ) from exc

    async def _prepare_solvers(self) -> None:
        assert self._registry is not None and self._ctx is not None
        for cls in self._registry.all_solvers():
            if not cls.info.browser:
                continue
            accepted = list(cls.info.types)
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
                self._by_type.setdefault(ct, instance)
            self._log.info(
                "solver.ready",
                solver=cls.info.name,
                types=[t.value for t in accepted],
                version=cls.info.version,
            )

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
        assert self._consumer is not None and self._gate is not None
        while not self._stop.is_set():
            if not await self._gate.wait_for_memory(stop=self._stop):
                break
            try:
                batch = await self._consumer.read()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.error("consumer.read_failed", error=str(exc), exc_info=True)
                await asyncio.sleep(1.0)
                continue

            for message_id, envelope in batch:
                if not await self._gate.acquire(stop=self._stop):
                    # Shutdown while waiting for a slot: leave the rest of the
                    # batch claimed so XAUTOCLAIM hands it to a live worker.
                    break
                label = task_label(envelope.task)
                t = asyncio.create_task(
                    self._run_one_slot(message_id, envelope, label),
                    name=f"browser-task-{label}",
                )
                self._inflight.add(t)
                t.add_done_callback(self._inflight.discard)

        self._log.info("worker.drain", inflight=len(self._inflight))
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)

    async def _run_one_slot(self, message_id: str, envelope: TaskEnvelope, label: str) -> None:
        assert self._runner is not None and self._gate is not None
        token = bind_label(label)
        ok = False
        try:
            ok = await self._runner.run(message_id, envelope, label)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log.exception(
                "task.slot_crashed",
                task_id=str(envelope.task.id),
                type=envelope.task.type.value,
            )
        finally:
            reset_label(token)
            self._gate.release(ok=ok)

    # ---- teardown ------------------------------------------------------

    async def _teardown(self) -> None:
        self._log.info("worker.teardown")
        if self._heartbeat is not None:
            stopper = getattr(self._heartbeat, "stop", None)
            if callable(stopper):
                with contextlib.suppress(Exception):
                    await stopper()
        for inst in list(self._instances.values()):
            with contextlib.suppress(Exception):
                await inst.shutdown()
        if self._tracker is not None:
            with contextlib.suppress(Exception):
                killed = await self._tracker.kill_all()
                if killed:
                    self._log.warning("browser.sessions_killed", count=killed)
        metrics.set_browser_sessions_active(0)
        if self._owns_provider and self._provider is not None:
            with contextlib.suppress(Exception):
                await close_provider(self._provider)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()


def _load_proxy_pool(settings: Settings, logger: Any) -> ProxyPool | None:
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
        # Non-fatal: a headful challenge can still be driven without a proxy.
        logger.warning("proxy_pool.load_failed", path=str(path), error=str(exc))
        return ProxyPool([])
