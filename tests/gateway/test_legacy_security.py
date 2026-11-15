"""Legacy /in.php: the quota gate and the Postgres payload redaction.

The JSON createTask path had both added in the security hardening commit, but
the form protocol runs its own copy of the create flow, so it needs its own
proof. No redis and no postgres here: Redis is a dict-backed fake and the repos
are the same in-memory shapes the other gateway tests use.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, ClassVar
from uuid import UUID

import httpx
import pytest

from captcha.domain.enums import CaptchaType
from captcha.domain.redaction import is_sensitive_key
from captcha.domain.task import TaskEnvelope
from captcha.services.gateway import auth as auth_module
from captcha.services.gateway.app import create_app
from captcha.services.gateway.deps import get_db
from captcha.services.gateway.routes import legacy as legacy_routes

CLIENT_KEY = "test-key-0000000003"
PAGE = "https://example.com/login"
SITEKEY = "10000000-ffff-ffff-ffff-000000000001"
PROXY = "http://user:pass@10.0.0.1:8080"
SOLVABLE = frozenset({CaptchaType.HCAPTCHA})


# ---- fakes ------------------------------------------------------------------


class FakeRedis:
    """Only the commands QuotaGuard issues."""

    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    async def incr(self, key: str) -> int:
        value = int(self.kv.get(key, 0) or 0) + 1
        self.kv[key] = value
        return value

    async def incrbyfloat(self, key: str, amount: float) -> float:
        value = float(self.kv.get(key, 0) or 0) + float(amount)
        self.kv[key] = value
        return value

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def expire(self, _key: str, _ttl: int) -> bool:
        return True

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    async def zremrangebyscore(self, _key: str, _min: Any, _max: Any) -> int:
        return 0


@dataclass
class FakeAccount:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    api_key: str = CLIENT_KEY
    balance: Decimal = Decimal("10.000000")
    disabled: bool = False


ACCOUNT = FakeAccount()


class FakeAccountRepo:
    charges: ClassVar[list[Decimal]] = []

    def __init__(self, _session: Any) -> None: ...

    async def get_by_key(self, api_key: str) -> FakeAccount | None:
        return ACCOUNT if api_key == ACCOUNT.api_key else None

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
    payload: dict[str, Any]
    status: str = "pending"


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


@dataclass
class FakeState:
    state_store: FakeStore
    producer: FakeProducer
    redis: Any = None
    settings: Any = None
    solver_types: frozenset[CaptchaType] = SOLVABLE


async def _fake_db():
    yield object()


def _build(
    monkeypatch: pytest.MonkeyPatch,
    *,
    redis: Any = None,
    settings: Any = None,
) -> tuple[httpx.AsyncClient, FakeState]:
    CREATED.clear()
    FakeAccountRepo.charges.clear()
    state = FakeState(state_store=FakeStore(), producer=FakeProducer(), redis=redis, settings=settings)

    app = create_app()
    app.state.ctx = state
    app.dependency_overrides[get_db] = _fake_db
    for module in (auth_module, legacy_routes):
        monkeypatch.setattr(module, "AccountRepo", FakeAccountRepo, raising=False)
        monkeypatch.setattr(module, "TaskRepo", FakeTaskRepo, raising=False)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    return client, state


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[httpx.AsyncClient, FakeState]:
    return _build(monkeypatch)


async def _submit(client: httpx.AsyncClient, **fields: Any) -> httpx.Response:
    return await client.post("/in.php", data={"key": CLIENT_KEY, **fields})


# ---- redaction --------------------------------------------------------------


async def test_legacy_in_redacts_the_postgres_payload(wired) -> None:
    client, state = wired
    response = await _submit(
        client,
        method="hcaptcha",
        sitekey=SITEKEY,
        pageurl=PAGE,
        proxy=PROXY,
        proxytype="HTTP",
        cookies="sid=secret",
        rqdata="rq-1",
        host="example.com",
    )
    assert response.text.startswith("OK|"), response.text
    task_id = UUID(response.text.removeprefix("OK|"))

    row = CREATED[0]
    assert row.type == "hcaptcha"
    assert not any(is_sensitive_key(key) for key in row.payload), row.payload
    assert "cookies" not in row.payload
    # Everything the worker needs but a credential stays.
    assert row.payload["sitekey"] == SITEKEY
    assert row.payload["rqdata"] == "rq-1"
    assert row.payload["host"] == "example.com"

    # The queued envelope still carries the full payload, proxy included.
    task = state.state_store.by_id[task_id].task
    assert task.proxy == PROXY
    assert task.payload["proxy"] == PROXY
    assert task.payload["cookies"] == [{"name": "sid", "value": "secret"}]


# ---- quotas -----------------------------------------------------------------


async def test_legacy_in_quota_rejection_is_not_billed(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    client, state = _build(monkeypatch, redis=redis, settings=SimpleNamespace(rate_limit_create_per_min=1))
    fields = {"method": "hcaptcha", "sitekey": SITEKEY, "pageurl": PAGE}

    first = await _submit(client, **fields)
    assert first.text.startswith("OK|"), first.text

    second = await _submit(client, **fields)
    assert second.text == "ERROR_NO_SLOT_AVAILABLE"
    assert len(FakeAccountRepo.charges) == 1, "a quota rejection must not be billed"
    assert len(state.producer.published) == 1, "a quota rejection must not reach a stream"
    assert len(CREATED) == 1


async def test_legacy_in_without_limits_allows_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _state = _build(monkeypatch, redis=FakeRedis(), settings=None)
    fields = {"method": "hcaptcha", "sitekey": SITEKEY, "pageurl": PAGE}
    for _ in range(2):
        response = await _submit(client, **fields)
        assert response.text.startswith("OK|"), response.text
    assert len(FakeAccountRepo.charges) == 2
