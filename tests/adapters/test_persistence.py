"""Worker-side Postgres mirror.

The interesting properties are not "does SQLAlchemy build a statement" but:
a worker without a database must not crash, a terminal write must not raise, and
the refund must only move money once.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from captcha.adapters.storage.persistence import TaskPersistence
from captcha.domain.enums import SolveMode
from captcha.domain.task import SolverResult


class FakeResult:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class FakeSession:
    """Records what the repos ask for; the refund UPDATE answers with a row."""

    def __init__(self, refund_row: Any = None) -> None:
        self.refund_row = refund_row
        self.added: list[Any] = []
        self.statements: list[str] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def execute(self, stmt: Any) -> FakeResult:
        sql = str(stmt)
        self.statements.append(sql)
        if "SET cost=" in sql:
            return FakeResult(self.refund_row)
        return FakeResult(None)

    def matching(self, needle: str) -> list[str]:
        return [s for s in self.statements if needle in s]


def _factory(session: FakeSession):
    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


async def test_disabled_persistence_is_a_noop() -> None:
    persistence = TaskPersistence(None)
    assert not persistence.enabled
    task_id = uuid.uuid4()
    # None of these may raise, and none of them needs a database.
    await persistence.processing(task_id, "w-1")
    await persistence.ready(
        task_id, result=SolverResult(token="t", solve_mode=SolveMode.HTTP), latency_ms=1.0, solver_name="x", task_type="t"
    )
    await persistence.failed(
        task_id, error={"code": "ERR_UNSOLVABLE"}, latency_ms=1.0, solver_name="x", task_type="t", refund=True
    )


async def test_ready_writes_task_row_and_solver_run() -> None:
    session = FakeSession()
    persistence = TaskPersistence(_factory(session))
    task_id = uuid.uuid4()

    await persistence.ready(
        task_id,
        result=SolverResult(token="abc", solve_mode=SolveMode.HTTP, extra={"challenge": "c"}),
        latency_ms=12.5,
        solver_name="geetest_v3",
        task_type="geetest_v3",
    )

    assert session.matching("UPDATE tasks"), "task row must be updated"
    assert len(session.added) == 1
    run = session.added[0]
    assert run.ok is True
    assert run.solver_name == "geetest_v3"
    assert run.latency_ms == 12
    assert run.task_id == task_id


async def test_failed_refunds_once() -> None:
    account_id = uuid.uuid4()
    session = FakeSession(refund_row=(account_id, Decimal("0.002")))
    persistence = TaskPersistence(_factory(session))

    await persistence.failed(
        uuid.uuid4(),
        error={"code": "ERR_UNSOLVABLE", "message": "nope"},
        latency_ms=3.0,
        solver_name="hcaptcha",
        task_type="hcaptcha",
        refund=True,
    )

    assert session.matching("UPDATE tasks")
    assert session.matching("UPDATE accounts"), "the charge must go back to the account"
    run = session.added[0]
    assert run.ok is False
    assert run.error_code == "ERR_UNSOLVABLE"


async def test_failed_does_not_refund_twice() -> None:
    session = FakeSession(refund_row=None)  # cost already zeroed by a previous call
    persistence = TaskPersistence(_factory(session))

    await persistence.failed(
        uuid.uuid4(),
        error={"code": "ERR_TIMEOUT"},
        latency_ms=1.0,
        solver_name="friendly",
        task_type="friendly_captcha",
        refund=True,
    )

    assert session.matching("SET cost=")
    assert not session.matching("UPDATE accounts"), "second call must not move money again"


async def test_write_errors_are_swallowed() -> None:
    class BoomSession(FakeSession):
        async def execute(self, stmt: Any) -> FakeResult:
            raise RuntimeError("db down")

    persistence = TaskPersistence(_factory(BoomSession()))
    # A dead database must never propagate into the solve loop.
    await persistence.failed(
        uuid.uuid4(), error={"code": "ERR_INTERNAL"}, latency_ms=0.0, solver_name="x", task_type="t", refund=True
    )
