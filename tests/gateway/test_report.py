"""`reportCorrect` / `reportIncorrect` tests.

No redis and no postgres: a fake session answers the two queries the route makes
(account by key, task by id) and records the UPDATE the repo issues, so the test
pins both the public envelope and the stored verdict. The schema check is pure
pydantic.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from captcha.adapters.storage import TaskRepo
from captcha.services.gateway.deps import get_db
from captcha.services.gateway.routes.report import router
from captcha.services.gateway.schemas import ReportRequest

CLIENT_KEY = "test-key-123"


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class FakeSession:
    """Minimal AsyncSession: account lookup via select, task lookup via get."""

    def __init__(self, *, account: Any, task: Any) -> None:
        self.account = account
        self.task = task
        self.executed: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.executed.append(stmt)
        if getattr(stmt, "is_select", False):
            return _Result(self.account)
        return _Result(None)

    async def get(self, model: Any, pk: Any) -> Any:
        return self.task


def _compiled_params(stmt: Any) -> dict[str, Any]:
    return dict(stmt.compile(dialect=postgresql.dialect()).params)


def _account(account_id: uuid.UUID) -> Any:
    return SimpleNamespace(id=account_id, disabled=False)


def _task(task_id: uuid.UUID, account_id: uuid.UUID) -> Any:
    return SimpleNamespace(id=task_id, account_id=account_id)


def _client(session: FakeSession) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def _override_db() -> Any:
        yield session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def test_report_schema_requires_both_fields() -> None:
    req = ReportRequest.model_validate({"clientKey": CLIENT_KEY, "taskId": "abc"})
    assert req.client_key == CLIENT_KEY
    assert req.task_id == "abc"
    with pytest.raises(ValidationError):
        ReportRequest.model_validate({"clientKey": CLIENT_KEY})


async def test_repo_persists_verdict_and_callback_url() -> None:
    task_id = uuid.uuid4()

    class Recorder:
        def __init__(self) -> None:
            self.statements: list[Any] = []

        async def execute(self, stmt: Any) -> None:
            self.statements.append(stmt)

    session = Recorder()
    repo = TaskRepo(session)  # type: ignore[arg-type]
    await repo.report(task_id, True)
    await repo.report(task_id, False)
    await repo.set_callback_url(task_id, "https://client.example.com/cb")

    assert _compiled_params(session.statements[0])["reported"] == 1
    assert _compiled_params(session.statements[1])["reported"] == -1
    assert _compiled_params(session.statements[2])["callback_url"] == "https://client.example.com/cb"


def test_report_correct_returns_ok_and_stores_one() -> None:
    account_id = uuid.uuid4()
    task_id = uuid.uuid4()
    session = FakeSession(account=_account(account_id), task=_task(task_id, account_id))

    resp = _client(session).post(
        "/reportCorrect",
        json={"clientKey": CLIENT_KEY, "taskId": str(task_id)},
    )

    assert resp.status_code == 200
    assert resp.json() == {"errorId": 0, "status": "OK"}
    updates = [stmt for stmt in session.executed if not getattr(stmt, "is_select", False)]
    assert _compiled_params(updates[-1])["reported"] == 1


def test_report_incorrect_stores_minus_one() -> None:
    account_id = uuid.uuid4()
    task_id = uuid.uuid4()
    session = FakeSession(account=_account(account_id), task=_task(task_id, account_id))

    resp = _client(session).post(
        "/reportIncorrect",
        json={"clientKey": CLIENT_KEY, "taskId": str(task_id)},
    )

    assert resp.status_code == 200
    assert resp.json() == {"errorId": 0, "status": "OK"}
    updates = [stmt for stmt in session.executed if not getattr(stmt, "is_select", False)]
    assert _compiled_params(updates[-1])["reported"] == -1


def test_unknown_task_returns_task_not_found() -> None:
    session = FakeSession(account=_account(uuid.uuid4()), task=None)

    resp = _client(session).post(
        "/reportCorrect",
        json={"clientKey": CLIENT_KEY, "taskId": str(uuid.uuid4())},
    )

    assert resp.status_code == 200
    assert resp.json()["errorId"] == 16
    assert resp.json()["errorCode"] == "ERROR_TASK_NOT_FOUND"


def test_foreign_task_is_not_found() -> None:
    task_id = uuid.uuid4()
    session = FakeSession(account=_account(uuid.uuid4()), task=_task(task_id, uuid.uuid4()))

    resp = _client(session).post(
        "/reportIncorrect",
        json={"clientKey": CLIENT_KEY, "taskId": str(task_id)},
    )

    assert resp.json()["errorId"] == 16
    assert not [stmt for stmt in session.executed if not getattr(stmt, "is_select", False)]


def test_malformed_task_id_is_not_found() -> None:
    session = FakeSession(account=_account(uuid.uuid4()), task=None)

    resp = _client(session).post(
        "/reportCorrect",
        json={"clientKey": CLIENT_KEY, "taskId": "not-a-uuid"},
    )

    assert resp.json()["errorId"] == 16
