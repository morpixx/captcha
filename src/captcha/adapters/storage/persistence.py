"""Postgres mirror of what a worker does.

Redis is the fast path `getTaskResult` reads; Postgres is the durable record
that outlives the 24h state hash. Without this module the DB only ever sees
`pending` rows, so every "task older than the TTL" poll answers `processing`
forever and the admin rollups count nothing.

Every method is best-effort: a broken database must not take down a worker that
is otherwise solving captchas. Failures are logged and swallowed, and the whole
object degrades to a no-op when the worker was started without Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from typing import Any

import msgspec
import structlog

log = structlog.get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[Any]]


class TaskPersistence:
    """Writes task state and solver runs; no-op when `session_factory` is None."""

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self._factory = session_factory

    @property
    def enabled(self) -> bool:
        return self._factory is not None

    async def processing(self, task_id: uuid.UUID, worker_id: str) -> None:
        await self._write("processing", self._processing, task_id, worker_id)

    async def ready(
        self,
        task_id: uuid.UUID,
        *,
        result: Any,
        latency_ms: float,
        solver_name: str,
        task_type: str,
    ) -> None:
        payload = msgspec.to_builtins(result)
        await self._write("ready", self._ready, task_id, payload, latency_ms, solver_name, task_type)

    async def failed(
        self,
        task_id: uuid.UUID,
        *,
        error: dict[str, Any],
        latency_ms: float,
        solver_name: str,
        task_type: str,
        refund: bool,
    ) -> None:
        await self._write("failed", self._failed, task_id, error, latency_ms, solver_name, task_type, refund)

    # ---- internals ----------------------------------------------------------

    async def _write(self, kind: str, fn: Callable[..., Any], *args: Any) -> None:
        if self._factory is None:
            return
        try:
            async with self._factory() as session:
                await fn(session, *args)
        except Exception as exc:
            log.warning("persistence.write_failed", kind=kind, error=str(exc), exc_info=True)

    async def _processing(self, session: Any, task_id: uuid.UUID, worker_id: str) -> None:
        from captcha.adapters.storage import TaskRepo

        await TaskRepo(session).update_status(task_id, "processing", worker_id=worker_id)

    async def _ready(
        self,
        session: Any,
        task_id: uuid.UUID,
        result: dict[str, Any],
        latency_ms: float,
        solver_name: str,
        task_type: str,
    ) -> None:
        from captcha.adapters.storage import SolverRunRepo, TaskRepo

        await TaskRepo(session).update_result(task_id, result=result)
        await SolverRunRepo(session).record(
            solver_name=solver_name,
            task_type=task_type,
            ok=True,
            latency_ms=latency_ms,
            task_id=task_id,
        )

    async def _failed(
        self,
        session: Any,
        task_id: uuid.UUID,
        error: dict[str, Any],
        latency_ms: float,
        solver_name: str,
        task_type: str,
        refund: bool,
    ) -> None:
        from captcha.adapters.storage import SolverRunRepo, TaskRepo

        repo = TaskRepo(session)
        await repo.update_result(task_id, error=error)
        await SolverRunRepo(session).record(
            solver_name=solver_name,
            task_type=task_type,
            ok=False,
            latency_ms=latency_ms,
            error_code=str(error.get("code") or "")[:64] or None,
            task_id=task_id,
        )
        if refund:
            refunded = await repo.refund_once(task_id)
            if refunded is not None:
                log.info("task.refunded", task_id=str(task_id), amount=str(Decimal(refunded)))


def session_factory_for(engine: Any) -> SessionFactory | None:
    """Return the shared session factory when an engine exists, else None."""
    if engine is None:
        return None
    from .engine import async_session

    return async_session


def engine_session_factory(postgres_url: str, *, pool_size: int = 5) -> SessionFactory:
    """Build (once per process) the engine and hand back its session factory.

    `init_engine` is a process-wide singleton and does not connect, so this
    cannot fail on a cold database — the first write reports the failure and the
    worker keeps solving.
    """
    from .engine import async_session, init_engine

    init_engine(postgres_url, pool_size=pool_size)
    return async_session
