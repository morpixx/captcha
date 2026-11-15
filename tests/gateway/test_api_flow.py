"""End-to-end walk through the HTTP surface, with infrastructure faked out.

No redis and no postgres here on purpose: the point of this file is the wiring
between route, domain and queue — the parts that break when a field is renamed
or a lane disappears. The adapters are replaced by in-memory objects that keep
the same coroutine shape.

Covers: createTask (JSON), getTaskResult (GET, processing and ready),
legacy /in.php + /res.php, and the auth error path of both.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

import httpx
import pytest

from captcha.domain.enums import CaptchaType, SolveMode, TaskStatus
from captcha.domain.task import SolverResult, TaskEnvelope
from captcha.services.gateway import auth as auth_module
from captcha.services.gateway.app import create_app
from captcha.services.gateway.deps import get_db
from captcha.services.gateway.routes import legacy as legacy_routes
from captcha.services.gateway.routes import report as report_routes
from captcha.services.gateway.routes import tasks as tasks_routes

CLIENT_KEY = "test-key-0000000001"


# ---- fakes ------------------------------------------------------------------


@dataclass
class FakeAccount:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    api_key: str = CLIENT_KEY
    balance: Decimal = Decimal("10.000000")
    disabled: bool = False


ACCOUNT = FakeAccount()


class FakeAccountRepo:
    def __init__(self, _session: Any) -> None: ...

    async def get_by_key(self, api_key: str) -> FakeAccount | None:
        # The real repo does not filter on `disabled`; authenticate() does.
        return ACCOUNT if api_key == ACCOUNT.api_key else None

    charges: ClassVar[list[Decimal]] = []

    async def charge(self, _account_id: uuid.UUID, amount: Decimal) -> Decimal:
        FakeAccountRepo.charges.append(amount)
        return ACCOUNT.balance

    async def refund(self, _account_id: uuid.UUID, _amount: Decimal) -> Decimal:
        return ACCOUNT.balance


@dataclass
class FakeRow:
    id: uuid.UUID
    account_id: uuid.UUID
    type: str
    status: str = "pending"
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    started_at: Any = None
    finished_at: Any = None


CREATED: list[FakeRow] = []


class FakeTaskRepo:
    def __init__(self, _session: Any) -> None: ...

    async def create(
        self,
        *,
        account_id: uuid.UUID,
        type_: str,
        payload: dict[str, Any],
        cost: Decimal | None = None,
        callback_url: str | None = None,
    ) -> FakeRow:
        row = FakeRow(id=uuid.uuid4(), account_id=account_id, type=type_, payload=payload)
        CREATED.append(row)
        return row

    async def get(self, _task_id: uuid.UUID) -> FakeRow | None:
        return None


class FakeStore:
    def __init__(self) -> None:
        self.by_id: dict[uuid.UUID, TaskEnvelope] = {}

    async def set_pending(self, envelope: TaskEnvelope) -> None:
        self.by_id[envelope.task.id] = envelope

    async def get(self, task_id: uuid.UUID) -> TaskEnvelope | None:
        return self.by_id.get(task_id)


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[uuid.UUID, str]] = []

    async def publish(self, envelope: TaskEnvelope, lane: str) -> None:
        self.published.append((envelope.task.id, lane))


def _public_resolver(_host: str) -> tuple[str, ...]:
    """Every test host is public; the gateway guard must not touch real DNS."""
    return ("93.184.216.34",)


@dataclass
class FakeState:
    state_store: FakeStore
    producer: FakeProducer
    settings: Any = None
    #: Types this fake deployment claims to solve. createTask refuses the rest.
    solver_types: frozenset[CaptchaType] = frozenset({CaptchaType.TEXT})
    url_resolver: Any = _public_resolver


async def _fake_db():
    """Stands in for get_db(); the repos are faked, so the session is opaque."""
    yield object()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[httpx.AsyncClient, FakeState]:
    """App with fake storage/queue; returns (client, state) for assertions."""
    CREATED.clear()
    FakeAccountRepo.charges.clear()
    state = FakeState(state_store=FakeStore(), producer=FakeProducer())

    app = create_app()
    app.state.ctx = state
    app.dependency_overrides[get_db] = _fake_db

    # Every consumer binds the repo class at import time, so patch each module.
    for module in (auth_module, tasks_routes, legacy_routes, report_routes):
        monkeypatch.setattr(module, "AccountRepo", FakeAccountRepo, raising=False)
        monkeypatch.setattr(module, "TaskRepo", FakeTaskRepo, raising=False)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    return client, state


async def test_create_task_publishes_to_the_type_lane(wired) -> None:
    client, state = wired
    response = await client.post(
        "/createTask",
        json={
            "clientKey": CLIENT_KEY,
            "task": {"type": "text_captcha", "websiteURL": "https://example.com", "websiteKey": "k"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["errorId"] == 0
    task_id = uuid.UUID(body["taskId"])

    assert [tid for tid, _lane in state.producer.published] == [task_id]
    # text_captcha is a cv-lane type in the catalog; the gateway must honour it.
    assert state.producer.published[0][1] == "cv"
    assert task_id in state.state_store.by_id


async def test_get_task_result_processing_then_ready(wired) -> None:
    client, state = wired
    created = await client.post(
        "/createTask",
        json={"clientKey": CLIENT_KEY, "task": {"type": "text_captcha", "websiteKey": "k"}},
    )
    task_id = uuid.UUID(created.json()["taskId"])

    pending = await client.get("/getTaskResult", params={"clientKey": CLIENT_KEY, "taskId": str(task_id)})
    assert pending.json()["status"] == "processing"

    envelope = state.state_store.by_id[task_id]
    envelope.status = TaskStatus.READY
    envelope.result = SolverResult(text="abcd", solve_mode=SolveMode.HTTP)

    ready = await client.get("/getTaskResult", params={"clientKey": CLIENT_KEY, "taskId": str(task_id)})
    body = ready.json()
    assert body["status"] == "ready"
    # text_captcha projects onto the single `text` key, not onto `token`.
    assert body["solution"] == {"text": "abcd"}


async def test_legacy_in_and_res_round_trip(wired) -> None:
    client, state = wired
    created = await client.post(
        "/in.php",
        data={"key": CLIENT_KEY, "method": "textcaptcha", "textcaptcha": "what is 2+2"},
    )
    assert created.headers["content-type"].startswith("text/plain")
    assert created.text.startswith("OK|")
    task_id = uuid.UUID(created.text.removeprefix("OK|"))

    envelope = state.state_store.by_id[task_id]
    envelope.status = TaskStatus.READY
    envelope.result = SolverResult(text="4", solve_mode=SolveMode.HTTP)

    answered = await client.get("/res.php", params={"key": CLIENT_KEY, "action": "get", "id": str(task_id)})
    assert answered.text == "OK|4"


async def test_legacy_json_mode_and_not_ready(wired) -> None:
    client, _state = wired
    created = await client.post(
        "/in.php",
        data={"key": CLIENT_KEY, "method": "textcaptcha", "textcaptcha": "q", "json": "1"},
    )
    body = created.json()
    assert body["status"] == 1
    task_id = body["request"]

    waiting = await client.get(
        "/res.php",
        params={"key": CLIENT_KEY, "action": "get", "id": task_id, "json": "1"},
    )
    assert waiting.json() == {"status": 0, "request": "CAPCHA_NOT_READY"}
    assert str(uuid.UUID(task_id)) == task_id


async def test_unknown_key_is_an_auth_error_in_both_protocols(wired, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _state = wired
    monkeypatch.setattr(ACCOUNT, "disabled", True, raising=False)

    modern = await client.post("/createTask", json={"clientKey": CLIENT_KEY, "task": {"type": "text_captcha"}})
    assert modern.status_code == 403
    assert modern.json()["detail"]["errorCode"] == "ERROR_ACCOUNT_SUSPENDED"

    legacy = await client.post("/in.php", data={"key": CLIENT_KEY, "method": "textcaptcha", "textcaptcha": "q"})
    assert legacy.text == "ERROR_ACCOUNT_SUSPENDED"


async def test_unsupported_type_is_refused_before_charging(wired) -> None:
    client, state = wired
    # gt is a slider-vendor type this fake deployment does not claim to solve.
    response = await client.post(
        "/createTask",
        json={"clientKey": CLIENT_KEY, "task": {"type": "capy", "websiteURL": "https://example.com"}},
    )
    body = response.json()
    assert body["errorId"] == 12
    assert body["errorCode"] == "ERROR_TASK_NOT_SUPPORTED"
    assert FakeAccountRepo.charges == [], "a task that cannot be solved must not be billed"
    assert state.producer.published == []


async def test_legacy_refuses_unsupported_method_before_charging(wired) -> None:
    client, _state = wired
    response = await client.post(
        "/in.php",
        data={"key": CLIENT_KEY, "method": "geetest", "gt": "g", "challenge": "c", "pageurl": "https://e.com"},
    )
    assert response.text == "ERROR_TASK_NOT_SUPPORTED"
    assert FakeAccountRepo.charges == []


async def test_unknown_legacy_method(wired) -> None:
    client, _state = wired
    response = await client.post("/in.php", data={"key": CLIENT_KEY, "method": "definitely_not_a_method"})
    assert response.text == "ERROR_NO_SUCH_METHOD"
