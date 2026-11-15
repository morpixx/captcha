"""Per-consumer HTTP-lane worker loop.

Loop:
    while running:
        batch = await consumer.read()                         # XREADGROUP + stale reclaim
        for msg_id, envelope in batch:
            spawn task: _handle_one(msg_id, envelope)         # bounded by ConcurrencyLimiter

For each task:
    1. acquire semaphore slot (sem gates total in-flight, not per-consumer)
    2. SolverRegistry.pick(task.type) -> Solver class; instantiate once per worker
       (cached in self._instances) and call its `solve_http` with a timeout
       derived from `task.deadline_ts`
    3. On success: `TaskStateStore.set_ready` + `Consumer.ack` + metric
    4. On `CaptchaError`: route by `.code`
       - ERR_NEEDS_BROWSER: publish to the `browser` lane, ack the source,
         count an escalation, leave billing alone (one hop max)
       - ERR_UNSOLVABLE / ERR_CONFIG: terminal, set_failed + ack, no requeue
       - everything else: `Consumer.fail` (ack + re-xadd with retries++ or DLQ)
    5. On other exceptions: classify as ERR_UNKNOWN, fail-with-retry
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: F401 — type reference for task.id

import structlog

from captcha.adapters.pingback import PingbackSender, safe_url
from captcha.adapters.queue import Consumer, Producer, TaskStateStore
from captcha.adapters.queue.keys import Lane
from captcha.adapters.storage.persistence import TaskPersistence
from captcha.application.pingback import pingback_payload
from captcha.config import Settings
from captcha.domain.catalog import lane_for, requires_browser
from captcha.domain.enums import CaptchaType, SolveMode, TaskStatus
from captcha.domain.errors import (
    CaptchaError,
    ConfigError,
    NeedsBrowserError,
    ProxyError,
    UnsolvableError,
)
from captcha.domain.task import MAX_LANE_HOPS, SolverResult, Task, TaskEnvelope
from captcha.observability import metrics
from captcha.solvers import Solver, SolverContext, SolverRegistry

from .concurrency import ConcurrencyLimiter
from .context import WorkerContextBundle
from .escalation import escalate_to_browser
from .heartbeat import HttpLaneHeartbeat

if TYPE_CHECKING:
    from redis.asyncio import Redis


_LANE: Lane = "http"
_DEFAULT_TASK_TIMEOUT = 120.0
_MIN_TASK_TIMEOUT = 2.0


class Worker:
    """One XREADGROUP loop. Share `limiter` across workers for a global cap."""

    def __init__(
        self,
        *,
        worker_id: str,
        redis: Redis,
        registry: SolverRegistry,
        state: TaskStateStore,
        bundle: WorkerContextBundle,
        limiter: ConcurrencyLimiter,
        persistence: TaskPersistence,
        settings: Settings,
        producer: Producer | None = None,
        consumer: Consumer | None = None,
        pingback: PingbackSender | None = None,
        heartbeat: HttpLaneHeartbeat | None = None,
        consumer_count: int = 16,
        block_ms: int = 5_000,
        max_retries: int = 3,
    ) -> None:
        self._id = worker_id
        self._r = redis
        self._registry = registry
        self._state = state
        self._bundle = bundle
        # Variant dispatch (funcaptcha) and the reCAPTCHA audio path need the
        # registry; the worker already owns it, so expose it through the
        # process-wide context instead of leaving ctx.extra without it.
        extra = getattr(bundle.template, "extra", None)
        if isinstance(extra, dict):
            extra.setdefault("solver_registry", registry)
        self._limiter = limiter
        self._persistence = persistence
        self._settings = settings
        self._pingback = pingback
        self._log = structlog.get_logger("captcha.worker.http").bind(worker=worker_id)

        self._consumer = consumer or Consumer(
            redis,
            lane=_LANE,
            consumer_name=worker_id,
            prefix=settings.redis_stream_prefix,
            block_ms=block_ms,
            count=consumer_count,
            max_retries=max_retries,
        )
        # The HTTP lane needs a producer only for escalation, but it shares the
        # process-wide lane streams so the prefix must match the consumer.
        self._producer = producer or Producer(redis, prefix=settings.redis_stream_prefix)
        self._heartbeat = heartbeat or HttpLaneHeartbeat(
            redis,
            worker_id=worker_id,
            prefix=settings.redis_stream_prefix,
            types=self._http_capabilities(),
        )

        self._instances: dict[str, Solver] = {}
        self._running = asyncio.Event()
        self._inflight: set[asyncio.Task[None]] = set()

    # ---------------------------------------------------------------- lifecycle

    def _http_capabilities(self) -> tuple[CaptchaType, ...]:
        """Types this worker can actually pick on the HTTP lane.

        Advertised in captcha:cap:http so the gateway gates sales on a live
        worker, not on a solver being merely importable. A browser-only solver
        is excluded: solve_http on it is the base-class "not supported" error.
        """
        types = {
            ct
            for cls in self._registry.all_solvers()
            if cls.info.http
            for ct in cls.info.types
            if lane_for(ct) == "http" and not requires_browser(ct)
        }
        return tuple(sorted(types, key=lambda ct: ct.value))

    async def ensure_group(self) -> None:
        await self._consumer.ensure_group()

    async def _solver_for(self, ct: CaptchaType) -> Solver | None:
        # allow_experimental: every antibot and interactive-vendor solver is
        # marked experimental (never verified against a live site), and there is
        # no non-experimental alternative for those types. Refusing them here
        # would mean 36 registered solvers are unreachable.
        # mode=HTTP: a browser-only solver must never be instantiated here —
        # its solve_http is the base-class "not supported" error.
        cls = self._registry.pick(ct, mode=SolveMode.HTTP, allow_experimental=True)
        if cls is None:
            return None
        key = cls.info.name
        inst = self._instances.get(key)
        if inst is None:
            inst = cls()
            try:
                await inst.prepare(self._bundle.template)
            except Exception as exc:
                self._log.error(
                    "solver.prepare_failed",
                    solver=key,
                    type=ct.value,
                    error=str(exc),
                    exc_info=True,
                )
                return None
            self._instances[key] = inst
            self._log.info("solver.ready", solver=key, type=ct.value)
        return inst

    async def shutdown(self) -> None:
        """Signal the loop to stop, wait on in-flight, teardown solvers."""
        self._running.clear()
        await self._heartbeat.stop()
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
        for name, inst in list(self._instances.items()):
            with contextlib.suppress(Exception):
                await inst.shutdown()
            self._log.info("solver.shutdown", solver=name)

    # ---------------------------------------------------------------- loop

    async def run(self) -> None:
        self._running.set()
        self._log.info("worker.start", lane=_LANE)
        metrics.active_tasks.labels(lane=_LANE).inc(0)  # register label
        await self._heartbeat.start()

        while self._running.is_set():
            try:
                batch = await self._consumer.read()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.error("consumer.read_failed", error=str(exc), exc_info=True)
                await asyncio.sleep(0.5)
                continue
            await self._refresh_queue_depth()

            for msg_id, envelope in batch:
                t = asyncio.create_task(self._run_one(msg_id, envelope))
                self._inflight.add(t)
                t.add_done_callback(self._inflight.discard)

        self._log.info("worker.stop", lane=_LANE)

    async def _refresh_queue_depth(self) -> None:
        """Publish the lane backlog on every read pass (contract §10).

        Depth is the consumer group's lag: entries appended but not delivered
        yet. XLEN would count entries that were acked long ago (XACK does not
        remove them), so it is only the fallback for a Redis without lag.
        Best-effort: a Redis hiccup must not stop the consumer loop.
        """
        try:
            depth = await self._read_queue_depth()
        except Exception:
            return  # metric collection never blocks the loop
        if depth is None:
            return
        metrics.queue_depth.labels(lane=_LANE).set(depth)

    async def _read_queue_depth(self) -> int | None:
        stream = self._consumer.stream
        try:
            groups = await self._r.xinfo_groups(stream)
        except Exception:
            groups = []
        for info in groups:
            name = info.get("name", info.get(b"name"))
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            if name != self._consumer.group:
                continue
            lag = info.get("lag", info.get(b"lag"))
            if lag is not None:
                return max(0, int(lag))
            break
        try:
            return int(await self._r.xlen(stream))
        except Exception:
            return None

    # ---------------------------------------------------------------- one task

    async def _run_one(self, msg_id: str, envelope: TaskEnvelope) -> None:
        async with self._limiter.slot():
            metrics.active_tasks.labels(lane=_LANE).inc()
            start = time.time()
            try:
                await self._dispatch(msg_id, envelope, start)
            finally:
                metrics.active_tasks.labels(lane=_LANE).dec()

    async def _dispatch(self, msg_id: str, envelope: TaskEnvelope, start: float) -> None:
        task = envelope.task
        type_ = task.type
        log = self._log.bind(task_id=str(task.id), type=type_.value, msg_id=msg_id)

        try:
            await self._state.set_processing(task.id, self._id, started_at=start)
        except Exception as exc:
            log.warning("state.set_processing_failed", error=str(exc))
        await self._persistence.processing(task.id, self._id)

        solver = await self._solver_for(type_)
        if solver is None:
            err = UnsolvableError(
                f"no solver registered for type {type_.value}",
                context={"type": type_.value},
            )
            await self._finalize_failure(msg_id, envelope, err, start, terminal=True, log=log, solver_name="none")
            return

        timeout = self._compute_timeout(task)
        ctx = self._per_task_context(task)
        http_client = ctx.http

        try:
            result = await asyncio.wait_for(solver.solve_http(task, ctx), timeout=timeout)
        except TimeoutError:
            err = _timeout_error(timeout, type_.value)
            await self._finalize_failure(
                msg_id, envelope, err, start, terminal=False, log=log, solver_name=solver.info.name
            )
            return
        except NeedsBrowserError as exc:
            # Before the generic CaptchaError branch: this one is a reroute,
            # not a failure. It must never reach set_failed/refund.
            await self._handle_needs_browser(msg_id, envelope, exc, start, log=log, solver_name=solver.info.name)
            return
        except CaptchaError as exc:
            terminal = isinstance(exc, (UnsolvableError, ConfigError))
            await self._finalize_failure(
                msg_id, envelope, exc, start, terminal=terminal, log=log, solver_name=solver.info.name
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("solver.crashed", error=str(exc), exc_info=True)
            err = CaptchaError(
                f"solver crashed: {exc}",
                context={"type": type_.value, "error_type": exc.__class__.__name__},
            )
            await self._finalize_failure(
                msg_id, envelope, err, start, terminal=False, log=log, solver_name=solver.info.name
            )
            return
        finally:
            if http_client is not None:
                with contextlib.suppress(Exception):
                    await http_client.aclose()

        # success
        latency_ms = (time.time() - start) * 1000.0
        result = _with_latency(result, latency_ms)
        try:
            await self._state.set_ready(task.id, result, finished_at=time.time())
        except Exception as exc:
            log.warning("state.set_ready_failed", error=str(exc))
        await self._consumer.ack(msg_id)
        await self._persistence.ready(
            task.id,
            result=result,
            latency_ms=latency_ms,
            solver_name=solver.info.name,
            task_type=type_.value,
        )
        await self._send_pingback(task, TaskStatus.READY, result=result, error=None, log=log)

        metrics.tasks_total.labels(type=type_.value, lane=_LANE, status=TaskStatus.READY.value).inc()
        metrics.tasks_done_total.labels(
            type=type_.value,
            status=TaskStatus.READY.value,
            solve_mode=result.solve_mode.value,
        ).inc()
        metrics.task_latency_seconds.labels(
            type=type_.value,
            solve_mode=result.solve_mode.value,
        ).observe(latency_ms / 1000.0)
        log.info("task.done", latency_ms=round(latency_ms, 1))

    async def _handle_needs_browser(
        self,
        msg_id: str,
        envelope: TaskEnvelope,
        err: NeedsBrowserError,
        start: float,
        *,
        log: Any,
        solver_name: str,
    ) -> None:
        """Move the task to the browser lane, or fail it past the hop cap.

        No billing write happens here: the task stays `processing`, the source
        entry is acked only after the browser stream accepted the copy, and a
        second hop is terminal `ERR_UNSOLVABLE`.
        """
        if envelope.lane_hops >= MAX_LANE_HOPS:
            dead = UnsolvableError(
                "lane escalation budget exhausted",
                context={
                    "type": envelope.task.type.value,
                    "lane": _LANE,
                    "lane_hops": envelope.lane_hops,
                },
            )
            await self._finalize_failure(msg_id, envelope, dead, start, terminal=True, log=log, solver_name=solver_name)
            return

        try:
            await escalate_to_browser(
                producer=self._producer,
                consumer=self._consumer,
                message_id=msg_id,
                envelope=envelope,
                log=log,
            )
        except Exception as exc:
            # Redis down or publish rejected: retry the HTTP attempt. The source
            # entry was not acked, so the task is still ours.
            log.warning("task.escalation_failed", error=str(exc), exc_info=True)
            await self._finalize_failure(msg_id, envelope, err, start, terminal=False, log=log, solver_name=solver_name)

    async def _finalize_failure(
        self,
        msg_id: str,
        envelope: TaskEnvelope,
        err: CaptchaError,
        start: float,
        *,
        terminal: bool,
        log: Any,
        solver_name: str = "none",
    ) -> None:
        task = envelope.task
        type_val = task.type.value
        err_dict = err.to_dict()
        latency_ms = (time.time() - start) * 1000.0
        metrics.solver_errors_total.labels(type=type_val, error_code=err.code).inc()

        # ProxyError feedback to the pool, when we have a bound proxy on the task.
        if isinstance(err, ProxyError) and task.proxy and self._bundle.proxy_pool is not None:
            with contextlib.suppress(Exception):
                self._bundle.proxy_pool.mark_bad(
                    _proxy_for_feedback(task.proxy),
                    reason=err.code,
                )

        if terminal:
            try:
                await self._state.set_failed(task.id, err_dict, finished_at=time.time())
            except Exception as exc:
                log.warning("state.set_failed_failed", error=str(exc))
            await self._consumer.ack(msg_id)
            await self._persistence.failed(
                task.id,
                error=err_dict,
                latency_ms=latency_ms,
                solver_name=solver_name,
                task_type=type_val,
                refund=True,
            )
            await self._send_pingback(task, TaskStatus.FAILED, result=None, error=err_dict, log=log)
            metrics.tasks_total.labels(type=type_val, lane=_LANE, status=TaskStatus.FAILED.value).inc()
            metrics.tasks_done_total.labels(
                type=type_val,
                status=TaskStatus.FAILED.value,
                solve_mode=SolveMode.HTTP.value,
            ).inc()
            log.warning("task.failed_terminal", code=err.code, message=err.message)
            return

        # Non-terminal: let Consumer.fail decide between re-enqueue vs DLQ.
        dead = await self._consumer.fail(msg_id, envelope, reason=err.code)
        if dead:
            try:
                await self._state.set_failed(task.id, err_dict, finished_at=time.time())
            except Exception as exc:
                log.warning("state.set_failed_failed", error=str(exc))
            await self._persistence.failed(
                task.id,
                error=err_dict,
                latency_ms=latency_ms,
                solver_name=solver_name,
                task_type=type_val,
                refund=True,
            )
            await self._send_pingback(task, TaskStatus.FAILED, result=None, error=err_dict, log=log)
            metrics.tasks_total.labels(type=type_val, lane=_LANE, status=TaskStatus.FAILED.value).inc()
            metrics.tasks_done_total.labels(
                type=type_val,
                status=TaskStatus.FAILED.value,
                solve_mode=SolveMode.HTTP.value,
            ).inc()
            log.warning("task.dead_lettered", code=err.code, retries=envelope.retries)
        else:
            log.info(
                "task.retry_scheduled",
                code=err.code,
                next_attempt=envelope.retries + 1,
            )

    # ---------------------------------------------------------------- helpers

    async def _send_pingback(
        self,
        task: Task,
        task_status: TaskStatus,
        *,
        result: SolverResult | None,
        error: dict[str, Any] | None,
        log: Any,
    ) -> None:
        """Best-effort callback for a task that just reached a terminal state."""
        if not task.callback_url or self._pingback is None:
            return
        payload = pingback_payload(task_status, result, error, task.type, task.site_url)
        try:
            delivered = await self._pingback.send(task.callback_url, payload)
        except Exception:
            # The sender is contractually silent, but a callback may never kill the loop.
            log.exception("task.pingback_crashed", callback=safe_url(task.callback_url))
            return
        # Host only: a callbackUrl can carry a token in its path or query.
        log.info("task.pingback", callback=safe_url(task.callback_url), delivered=delivered)

    def _compute_timeout(self, task: Task) -> float:
        if task.deadline_ts is None:
            return _DEFAULT_TASK_TIMEOUT
        remaining = task.deadline_ts - time.time()
        if remaining < _MIN_TASK_TIMEOUT:
            return _MIN_TASK_TIMEOUT
        return remaining

    def _per_task_context(self, task: Task) -> SolverContext:
        """Shallow-copy the template; open a fresh HTTP client bound to the task."""
        tmpl = self._bundle.template
        http = self._bundle.http_factory(
            proxy=task.proxy,
            headers=_task_headers(task),
        )
        # dataclasses.replace swaps `.http` and leaves every other slot aliased.
        return dataclasses.replace(tmpl, http=http)


def _task_headers(task: Task) -> dict[str, str] | None:
    hdrs: dict[str, str] = {}
    if task.user_agent:
        hdrs["User-Agent"] = task.user_agent
    if task.language:
        hdrs["Accept-Language"] = task.language
    return hdrs or None


def _timeout_error(timeout: float, type_val: str) -> CaptchaError:
    # Use the common.errors.TimeoutError, not builtins.
    from captcha.domain.errors import TimeoutError as CaptchaTimeoutError

    return CaptchaTimeoutError(
        f"solver exceeded {timeout:.1f}s",
        context={"timeout_s": timeout, "type": type_val},
    )


def _with_latency(result: SolverResult, latency_ms: float) -> SolverResult:
    """msgspec.Structs are frozen in our scheme; rebuild with the measured latency.

    If the solver set its own latency, keep theirs (they know their work),
    otherwise write ours.
    """
    import msgspec

    if result.latency_ms:
        return result
    return msgspec.structs.replace(result, latency_ms=latency_ms)


def _proxy_for_feedback(proxy_url: str):
    """Build a minimal Proxy for `Pool.mark_bad` lookup.

    `mark_bad` matches on `url.split('@')[-1]`, so only the host:port needs
    to survive; provider is required by the Struct but unused in the match.
    """
    from captcha.adapters.proxy import Proxy

    return Proxy(url=proxy_url, provider="unknown")
