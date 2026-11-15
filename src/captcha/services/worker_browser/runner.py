"""Per-task execution for the browser lane.

One `solve_browser` call under the hard deadline, and the terminal/retry
classification that follows it. Split out of `worker.py` so the loop stays
readable and each module stays under the size cap.

Failure policy (contract §5):

* terminal — `ERR_UNSOLVABLE`, `ERR_CONFIG`, `ERR_URL_REJECTED`, and
  `ERR_NEEDS_BROWSER` (a second browser hop is not allowed): ack + `set_failed`;
* everything else, including the hard timeout — `Consumer.fail`, which either
  re-enqueues with `retries + 1` or dead-letters; `set_failed` only on DLQ.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import structlog

from captcha.domain.enums import SolveMode, TaskStatus
from captcha.domain.errors import CaptchaError, NeedsBrowserError, UnsolvableError
from captcha.domain.errors import TimeoutError as CaptchaTimeoutError
from captcha.domain.task import Task, TaskEnvelope
from captcha.observability import metrics
from captcha.solvers import Solver, SolverContext

from .limits import CANCEL_GRACE_SEC, LANE, MIN_TASK_TIMEOUT, TERMINAL_CODES

log = structlog.get_logger(__name__)


class TaskRunner:
    """Executes one envelope. Holds no loop state; safe to share per worker."""

    def __init__(
        self,
        *,
        state: Any,
        consumer: Any,
        ctx: SolverContext,
        solvers: dict[Any, Solver],
        hard_timeout: float,
        worker_id: str,
        tracker: Any = None,
        logger: Any = None,
    ) -> None:
        self._state = state
        self._consumer = consumer
        self._ctx = ctx
        self._solvers = solvers
        self._hard_timeout = hard_timeout
        self._worker_id = worker_id
        self._tracker = tracker
        self._log = logger or log

    async def run(self, message_id: str, envelope: TaskEnvelope, label: str) -> bool:
        """Returns `True` only for a clean success (feeds the concurrency ramp)."""
        task = envelope.task
        log = self._log.bind(task_id=str(task.id), type=task.type.value)
        metrics.active_tasks.labels(lane=LANE).inc()
        start = time.perf_counter()
        try:
            try:
                await self._state.set_processing(task.id, self._worker_id, started_at=time.time())
            except Exception as exc:
                log.warning("state.set_processing_failed", error=str(exc))

            solver = self._solvers.get(task.type)
            if solver is None:
                await self._missing_solver(message_id, envelope, log)
                return False

            timeout = self._resolve_timeout(task)
            try:
                result = await self._solve_with_deadline(task, solver, timeout, label)
            except TimeoutError:
                await self._handle_failure(
                    message_id,
                    envelope,
                    CaptchaTimeoutError(
                        f"solver exceeded {timeout:.1f}s",
                        context={"timeout_s": timeout, "type": task.type.value},
                    ),
                    terminal=False,
                    log=log,
                )
                return False
            except CaptchaError as exc:
                terminal = exc.code in TERMINAL_CODES
                err = exc
                if isinstance(exc, NeedsBrowserError):
                    # Contract §2: the hop budget is one; a browser-lane task
                    # asking for the browser lane is a dead end, not a retry.
                    err = UnsolvableError(
                        "browser escalation requested inside the browser lane",
                        context={
                            "type": task.type.value,
                            "lane": LANE,
                            "lane_hops": getattr(envelope, "lane_hops", 0),
                            "cause": exc.message,
                        },
                    )
                await self._handle_failure(message_id, envelope, err, terminal=terminal, log=log)
                return False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("solver.unknown_error")
                wrapped = CaptchaError(
                    str(exc) or type(exc).__name__,
                    context={"exc_type": type(exc).__name__, "type": task.type.value},
                )
                await self._handle_failure(message_id, envelope, wrapped, terminal=False, log=log)
                return False

            latency_s = time.perf_counter() - start
            if not result.latency_ms:
                result.latency_ms = latency_s * 1000.0
            result.solve_mode = SolveMode.BROWSER
            try:
                await self._state.set_ready(task.id, result, finished_at=time.time())
            except Exception as exc:
                log.warning("state.set_ready_failed", error=str(exc))
            await self._consumer.ack(message_id)
            metrics.task_latency_seconds.labels(type=task.type.value, solve_mode=SolveMode.BROWSER.value).observe(
                latency_s
            )
            metrics.tasks_done_total.labels(
                type=task.type.value, status=TaskStatus.READY.value, solve_mode=SolveMode.BROWSER.value
            ).inc()
            log.info("task.done", latency_ms=round(latency_s * 1000.0, 1))
            return True
        finally:
            metrics.active_tasks.labels(lane=LANE).dec()

    async def _solve_with_deadline(self, task: Task, solver: Solver, timeout: float, label: str) -> Any:
        """`asyncio.wait` instead of `wait_for`: the deadline must also kill the tree.

        On expiry: cancel the solve, kill this task's browser process tree, give
        cleanup a bounded grace period, then raise. The caller releases the slot
        whether or not that cleanup ever finishes.
        """
        solve_task: asyncio.Task[Any] = asyncio.create_task(
            solver.solve_browser(task, self._ctx),
            name=f"browser-solve-{task.id}",
        )
        done, _ = await asyncio.wait({solve_task}, timeout=timeout)
        if solve_task in done:
            return solve_task.result()

        solve_task.cancel()
        killed = 0
        if self._tracker is not None:
            killed = await self._tracker.kill_label(label)
        self._log.error(
            "task.hard_timeout",
            task_id=str(task.id),
            type=task.type.value,
            timeout_s=timeout,
            killed=killed,
        )
        await self._abandon(solve_task)
        raise CaptchaTimeoutError(
            f"solver exceeded {timeout:.1f}s",
            context={"timeout_s": timeout, "type": task.type.value, "killed": killed},
        )

    @staticmethod
    async def _abandon(solve_task: asyncio.Task[Any]) -> None:
        done, _ = await asyncio.wait({solve_task}, timeout=CANCEL_GRACE_SEC)
        if solve_task in done:
            with contextlib.suppress(BaseException):
                solve_task.exception()
        else:
            # Still unwinding: consume the exception so asyncio does not warn.
            solve_task.add_done_callback(_consume_exception)

    async def _missing_solver(self, message_id: str, envelope: TaskEnvelope, log: Any) -> None:
        task = envelope.task
        err = UnsolvableError(
            f"no browser solver registered for {task.type.value}",
            context={"type": task.type.value, "lane": LANE},
        )
        log.error("solver.missing")
        try:
            await self._state.set_failed(task.id, err.to_dict(), finished_at=time.time())
        except Exception as exc:
            log.warning("state.set_failed_failed", error=str(exc))
        await self._consumer.ack(message_id)
        metrics.solver_errors_total.labels(type=task.type.value, error_code=err.code).inc()
        metrics.tasks_done_total.labels(
            type=task.type.value, status=TaskStatus.FAILED.value, solve_mode=SolveMode.BROWSER.value
        ).inc()

    async def _handle_failure(
        self,
        message_id: str,
        envelope: TaskEnvelope,
        exc: CaptchaError,
        *,
        terminal: bool,
        log: Any,
    ) -> None:
        task = envelope.task
        metrics.solver_errors_total.labels(type=task.type.value, error_code=exc.code).inc()

        if terminal:
            await self._consumer.ack(message_id)
            try:
                await self._state.set_failed(task.id, exc.to_dict(), finished_at=time.time())
            except Exception as exc2:
                log.warning("state.set_failed_failed", error=str(exc2))
            metrics.tasks_done_total.labels(
                type=task.type.value, status=TaskStatus.FAILED.value, solve_mode=SolveMode.BROWSER.value
            ).inc()
            log.warning("task.failed_terminal", code=exc.code, message=exc.message)
            return

        dead = await self._consumer.fail(message_id, envelope, reason=exc.code)
        if dead:
            try:
                await self._state.set_failed(task.id, exc.to_dict(), finished_at=time.time())
            except Exception as exc2:
                log.warning("state.set_failed_failed", error=str(exc2))
            metrics.tasks_done_total.labels(
                type=task.type.value, status=TaskStatus.FAILED.value, solve_mode=SolveMode.BROWSER.value
            ).inc()
            log.warning("task.dead_lettered", code=exc.code, retries=envelope.retries)
        else:
            log.info("task.retry_scheduled", code=exc.code, next_attempt=envelope.retries + 1)

    def _resolve_timeout(self, task: Task) -> float:
        if task.deadline_ts:
            remaining = task.deadline_ts - time.time()
            if remaining <= MIN_TASK_TIMEOUT:
                return MIN_TASK_TIMEOUT
            return min(remaining, self._hard_timeout)
        return self._hard_timeout


def _consume_exception(task: asyncio.Task[Any]) -> None:
    with contextlib.suppress(BaseException):
        task.exception()
