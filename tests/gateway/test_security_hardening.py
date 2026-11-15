"""Security hardening: hashed API keys, Redis quotas, payload/DLQ redaction.

No live Redis and no live Postgres. The repos are exercised through a fake
session that records the SQL they build, Redis is a dict-backed fake, and the
gateway runs on the ASGI app with the same repo fakes test_api_flow uses.

Covered here:
- AccountRepo.create stores only the hash + tail; get_by_key queries the hash;
  the pepper participates in the hash; the seed CLI keeps printing its key.
- migration 0004 follows 0003 and the model carries the new columns.
- QuotaGuard enforces each of the four limits independently, and createTask
  answers errorId 2 / ERROR_NO_SLOT_AVAILABLE with HTTP 200 and no charge.
- Postgres gets a payload without proxy*/cookies while the queued Task keeps
  the full payload; producer and consumer DLQ writes are redacted.
"""

from __future__ import annotations

import importlib.util
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import msgspec
import pytest
from fastapi import HTTPException

from captcha.adapters.queue.streams import Consumer, Producer
from captcha.adapters.storage import models as storage_models
from captcha.adapters.storage import repos as repos_module
from captcha.adapters.storage.models import Account
from captcha.adapters.storage.repos import AccountRepo
from captcha.domain.apikey import hash_api_key, key_tail
from captcha.domain.enums import CaptchaType, TaskStatus
from captcha.domain.redaction import is_sensitive_key, redact_envelope, redact_payload
from captcha.domain.task import Task, TaskEnvelope
from captcha.services import cli as cli_module
from captcha.services.gateway import auth as auth_module
from captcha.services.gateway.app import create_app
from captcha.services.gateway.deps import get_db
from captcha.services.gateway.quotas import QuotaGuard, QuotaLimits
from captcha.services.gateway.routes import tasks as tasks_routes

CLIENT_KEY = "test-key-0000000001"
PROXY = "http://user:pass@10.0.0.1:8080"
REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "src" / "captcha" / "adapters" / "storage" / "migrations" / "versions"


# ---- redis fake -------------------------------------------------------------


class FakeRedis:
    """Only the commands the quotas and the queue adapter issue."""

    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.expires: dict[str, int] = {}
        self.xadds: list[tuple[str, dict[Any, Any]]] = []
        self.acked: list[tuple[str, str, Any]] = []

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

    async def expire(self, key: str, ttl: int) -> bool:
        self.expires[key] = ttl
        return True

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    async def zremrangebyscore(self, key: str, min_score: Any, max_score: Any) -> int:
        zset = self.zsets.get(key, {})
        low = float("-inf") if min_score == "-inf" else float(min_score)
        high = float(max_score)
        doomed = [member for member, score in zset.items() if low <= score <= high]
        for member in doomed:
            del zset[member]
        return len(doomed)

    async def xadd(self, stream: str, fields: dict[Any, Any], **_kwargs: Any) -> bytes:
        self.xadds.append((stream, fields))
        return b"1-1"

    async def xack(self, stream: str, group: str, message_id: Any) -> int:
        self.acked.append((stream, group, message_id))
        return 1


class PoisonRedis(FakeRedis):
    """XREADGROUP hands out an entry whose bytes do not decode."""

    async def xautoclaim(self, **_kwargs: Any) -> tuple[str, list[Any], list[Any]]:
        return ("0-0", [], [])

    async def xreadgroup(self, **_kwargs: Any) -> list[Any]:
        return [("captcha:stream:http", [("9-9", {b"data": b"not-msgpack-at-all"})])]


# ---- gateway fakes ----------------------------------------------------------


@dataclass
class FakeAccount:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    api_key: str | None = CLIENT_KEY
    key_tail: str = CLIENT_KEY[-4:]
    balance: Decimal = Decimal("10.000000")
    disabled: bool = False


ACCOUNT = FakeAccount()


class FakeAccountRepo:
    charges: ClassVar[list[Decimal]] = []

    def __init__(self, _session: Any) -> None: ...

    async def get_by_key(self, api_key: str) -> FakeAccount | None:
        return ACCOUNT if api_key == CLIENT_KEY else None

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
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    started_at: Any = None
    finished_at: Any = None


class FakeTaskRepo:
    created: ClassVar[list[FakeRow]] = []

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
        FakeTaskRepo.created.append(row)
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
        self.published: list[uuid.UUID] = []

    async def publish(self, envelope: TaskEnvelope, lane: str) -> None:
        self.published.append(envelope.task.id)


@dataclass
class FakeState:
    state_store: FakeStore
    producer: FakeProducer
    settings: Any = None
    redis: Any = None
    solver_types: frozenset[CaptchaType] = frozenset({CaptchaType.TEXT})


async def _fake_db():
    yield object()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[httpx.AsyncClient, FakeState]:
    FakeAccountRepo.charges.clear()
    FakeTaskRepo.created.clear()
    state = FakeState(state_store=FakeStore(), producer=FakeProducer())

    app = create_app()
    app.state.ctx = state
    app.dependency_overrides[get_db] = _fake_db
    monkeypatch.setattr(auth_module, "AccountRepo", FakeAccountRepo, raising=False)
    monkeypatch.setattr(tasks_routes, "AccountRepo", FakeAccountRepo, raising=False)
    monkeypatch.setattr(tasks_routes, "TaskRepo", FakeTaskRepo, raising=False)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    return client, state


async def _create(client: httpx.AsyncClient, task: dict[str, Any] | None = None) -> httpx.Response:
    body = task or {"type": "text_captcha", "websiteURL": "https://example.com", "websiteKey": "k"}
    return await client.post("/createTask", json={"clientKey": CLIENT_KEY, "task": body})


# ---- 1. API key hashing -----------------------------------------------------


class _Result:
    def __init__(self, account: Any) -> None:
        self._account = account

    def scalar_one_or_none(self) -> Any:
        return self._account


class RepoSession:
    """Records statements; the account row is whatever the test canned."""

    def __init__(self, account: Any = None) -> None:
        self.account = account
        self.added: list[Any] = []
        self.statements: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return _Result(self.account)


def test_hash_api_key_is_deterministic_and_peppered() -> None:
    assert hash_api_key(CLIENT_KEY) == hash_api_key(CLIENT_KEY)
    assert hash_api_key(CLIENT_KEY, "pepper") != hash_api_key(CLIENT_KEY, "")
    assert len(hash_api_key(CLIENT_KEY)) == 64
    assert key_tail(CLIENT_KEY) == "0001"
    assert key_tail("") == ""


async def test_create_stores_only_hash_and_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repos_module, "api_key_pepper", lambda: "pepper")
    session = RepoSession()

    account = await AccountRepo(session).create(api_key=CLIENT_KEY, name="demo")

    assert account.api_key is None, "the plaintext key must never be persisted"
    assert account.api_key_hash == hash_api_key(CLIENT_KEY, "pepper")
    assert account.key_tail == "0001"
    assert session.added == [account]


async def test_get_by_key_looks_up_the_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repos_module, "api_key_pepper", lambda: "pepper")
    stored = SimpleNamespace(id=uuid.uuid4())
    session = RepoSession(account=stored)

    found = await AccountRepo(session).get_by_key(CLIENT_KEY)

    assert found is stored
    stmt = session.statements[0]
    assert "api_key_hash" in str(stmt)
    params = stmt.compile().params
    assert hash_api_key(CLIENT_KEY, "pepper") in params.values()
    assert CLIENT_KEY not in params.values(), "the plaintext key must not be a query parameter"


async def test_get_by_key_ignores_empty_keys() -> None:
    session = RepoSession(account=SimpleNamespace(id=uuid.uuid4()))
    assert await AccountRepo(session).get_by_key("") is None
    assert session.statements == []


async def test_auth_rejects_keys_outside_the_length_bounds() -> None:
    for key in ("short", "x" * 65):
        with pytest.raises(HTTPException) as exc:
            await auth_module.authenticate(key, session=None)  # type: ignore[arg-type]
        assert exc.value.status_code == 401


def test_seed_command_still_prints_the_key(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """`make seed` is the only place a key is ever visible: it must keep working."""
    account = SimpleNamespace(id=uuid.uuid4(), balance=Decimal("10.000000"))

    class SeedRepo:
        async def create(self, *, api_key: str, name: str = "", balance: Decimal | None = None) -> Any:
            assert api_key
            return account

    async def _with_repo(fn: Any) -> Any:
        return await fn(SeedRepo())

    monkeypatch.setattr(cli_module, "_with_repo", _with_repo)

    explicit = "given-key-0123456789"
    args = SimpleNamespace(key=explicit, name="demo", balance="10")
    assert cli_module.cmd_create_account(args) == 0
    out = capsys.readouterr().out
    assert f"key      {explicit}" in out

    generated_args = SimpleNamespace(key=None, name="demo", balance="10")
    assert cli_module.cmd_create_account(generated_args) == 0
    printed = capsys.readouterr().out.split("key      ", 1)[1].splitlines()[0]
    assert len(printed) == 32 and int(printed, 16) >= 0


def test_account_model_carries_hash_columns() -> None:
    columns = Account.__table__.c
    assert "api_key_hash" in columns
    assert "key_tail" in columns
    assert columns["api_key"].nullable is True
    assert columns["api_key_hash"].unique is True
    assert storage_models.Account is Account


def _load_migration(name: str) -> Any:
    path = MIGRATIONS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0004_extends_the_chain() -> None:
    chain = {
        name: _load_migration(name) for name in ("0001_init", "0002_reporting_pingback", "0003_money", "0004_security")
    }
    assert chain["0002_reporting_pingback"].down_revision == "0001_init"
    assert chain["0003_money"].down_revision == "0002"
    assert chain["0004_security"].revision == "0004"
    assert chain["0004_security"].down_revision == "0003"
    assert callable(chain["0004_security"].upgrade)
    assert callable(chain["0004_security"].downgrade)


# ---- 2. quotas --------------------------------------------------------------


def _limits(**overrides: Any) -> QuotaLimits:
    return QuotaLimits(
        **{"create_per_min": 0, "active_tasks": 0, "daily_tasks": 0, "daily_spend": Decimal("0"), **overrides}
    )


async def test_quota_guard_allows_everything_when_unlimited_or_headless() -> None:
    assert await QuotaGuard(None, _limits(create_per_min=1)).check(uuid.uuid4(), Decimal("1")) is None
    assert await QuotaGuard(FakeRedis(), QuotaLimits()).check(uuid.uuid4(), Decimal("1")) is None


async def test_quota_guard_rate_limits_per_account() -> None:
    redis = FakeRedis()
    guard = QuotaGuard(redis, _limits(create_per_min=2))
    account_id = uuid.uuid4()

    assert await guard.check(account_id, Decimal("0")) is None
    assert await guard.check(account_id, Decimal("0")) is None
    assert await guard.check(account_id, Decimal("0")) == "rate_per_min"
    # A different account starts with a fresh window.
    assert await QuotaGuard(redis, _limits(create_per_min=2)).check(uuid.uuid4(), Decimal("0")) is None


async def test_quota_guard_counts_active_tasks() -> None:
    redis = FakeRedis()
    guard = QuotaGuard(redis, _limits(active_tasks=1))
    account_id = uuid.uuid4()

    assert await guard.check(account_id, Decimal("0")) is None
    await guard.record(account_id, uuid.uuid4(), Decimal("0"))
    assert await guard.check(account_id, Decimal("0")) == "active_tasks"


async def test_quota_guard_counts_daily_tasks() -> None:
    redis = FakeRedis()
    guard = QuotaGuard(redis, _limits(daily_tasks=1))
    account_id = uuid.uuid4()

    assert await guard.check(account_id, Decimal("0")) is None
    await guard.record(account_id, uuid.uuid4(), Decimal("0"))
    assert await guard.check(account_id, Decimal("0")) == "daily_tasks"


async def test_quota_guard_counts_daily_spend() -> None:
    redis = FakeRedis()
    guard = QuotaGuard(redis, _limits(daily_spend=Decimal("0.001")))
    account_id = uuid.uuid4()
    cost = Decimal("0.0005")

    assert await guard.check(account_id, cost) is None
    await guard.record(account_id, uuid.uuid4(), cost)
    assert await guard.check(account_id, cost) is None
    await guard.record(account_id, uuid.uuid4(), cost)
    assert await guard.check(account_id, cost) == "daily_spend"


def test_quota_limits_read_settings_and_treat_zero_as_off() -> None:
    settings = SimpleNamespace(
        rate_limit_create_per_min=60,
        max_active_tasks_per_account=0,
        max_daily_tasks=100,
        max_daily_spend=5.5,
    )
    limits = QuotaLimits.from_settings(settings)
    assert limits.enabled is True
    assert limits.create_per_min == 60
    assert limits.active_tasks == 0
    assert limits.daily_tasks == 100
    assert limits.daily_spend == Decimal("5.5")
    assert QuotaLimits.from_settings(None).enabled is False


async def test_create_task_returns_no_slot_when_rate_limited(wired) -> None:
    client, state = wired
    state.settings = SimpleNamespace(
        rate_limit_create_per_min=1,
        max_active_tasks_per_account=0,
        max_daily_tasks=0,
        max_daily_spend=0,
    )
    state.redis = FakeRedis()

    first = await _create(client)
    assert first.json()["errorId"] == 0

    second = await _create(client)
    assert second.status_code == 200
    body = second.json()
    assert body["errorId"] == 2
    assert body["errorCode"] == "ERROR_NO_SLOT_AVAILABLE"
    assert "errorDescription" in body

    assert len(FakeAccountRepo.charges) == 1, "a quota rejection must not be billed"
    assert len(state.producer.published) == 1, "a quota rejection must not reach a stream"


# ---- 3. payload redaction ---------------------------------------------------


def test_redact_payload_drops_proxy_and_cookies_recursively() -> None:
    payload = {
        "sitekey": "k",
        "proxy": PROXY,
        "proxyType": "http",
        "proxyAddress": "10.0.0.1",
        "proxyPort": 8080,
        "cookies": [{"name": "sid", "value": "s"}],
        "nested": {"proxyLogin": "user", "keep": 1},
        "list": [{"cookie": "a"}, {"keep": 2}],
    }
    redacted = redact_payload(payload)

    # A dict that held only credentials stays as an empty dict: the list shape
    # may be meaningful, the values are gone.
    assert redacted == {"sitekey": "k", "nested": {"keep": 1}, "list": [{}, {"keep": 2}]}
    assert payload["cookies"], "the caller's dict must not be mutated"
    assert is_sensitive_key("ProxyPassword")
    assert not is_sensitive_key("sitekey")


def test_redact_envelope_clears_task_proxy() -> None:
    envelope = _envelope()
    redacted = redact_envelope(envelope)
    assert redacted.task.proxy is None
    assert redacted.task.payload.get("cookies") is None
    assert envelope.task.proxy == PROXY, "the live envelope keeps its proxy"


async def test_create_task_persists_a_redacted_payload(wired) -> None:
    client, state = wired
    response = await _create(
        client,
        {
            "type": "text_captcha",
            "websiteURL": "https://example.com",
            "websiteKey": "k",
            "textcaptcha": "what is 2+2",
            "proxyAddress": PROXY,
            "proxyType": "http",
            "proxyPort": 8080,
            "cookies": [{"name": "sid", "value": "secret"}],
        },
    )
    body = response.json()
    assert body["errorId"] == 0, body

    row = FakeTaskRepo.created[0]
    assert "proxy" not in row.payload
    assert "proxyType" not in row.payload
    assert "proxyPort" not in row.payload
    assert "cookies" not in row.payload
    assert row.payload["textcaptcha"] == "what is 2+2"

    envelope = state.state_store.by_id[uuid.UUID(body["taskId"])]
    assert envelope.task.proxy == PROXY, "the queued Task keeps the full payload"
    assert "cookies" in envelope.task.payload


# ---- 4. DLQ redaction -------------------------------------------------------


def _envelope() -> TaskEnvelope:
    task = Task(
        id=uuid.uuid4(),
        type=CaptchaType.TEXT,
        payload={
            "textcaptcha": "q",
            "proxyAddress": PROXY,
            "cookies": [{"name": "sid", "value": "secret"}],
            "nested": {"proxyPort": 8080},
        },
        proxy=PROXY,
    )
    return TaskEnvelope(task=task, status=TaskStatus.PROCESSING)


def _stored(raw: bytes) -> TaskEnvelope:
    return msgspec.msgpack.Decoder(TaskEnvelope).decode(raw)


async def test_producer_dead_letter_is_redacted() -> None:
    redis = FakeRedis()
    envelope = _envelope()

    await Producer(redis, prefix="captcha").publish_dead_letter(envelope, "boom")

    stream, fields = redis.xadds[-1]
    assert stream == "captcha:dlq"
    stored = _stored(fields[b"data"])
    assert stored.task.proxy is None
    assert "cookies" not in stored.task.payload
    assert "proxyAddress" not in stored.task.payload
    assert stored.task.payload["nested"] == {}
    assert stored.task.payload["textcaptcha"] == "q"
    assert fields[b"reason"] == b"boom"


async def test_consumer_dead_letter_is_redacted() -> None:
    redis = FakeRedis()
    consumer = Consumer(redis, lane="http", consumer_name="c1", max_retries=2)
    envelope = msgspec.structs.replace(_envelope(), retries=1)

    assert await consumer.fail("5-5", envelope, "solver exploded") is True

    assert redis.acked == [("captcha:stream:http", "group:lane-http", "5-5")]
    stream, fields = redis.xadds[-1]
    assert stream == "captcha:dlq"
    stored = _stored(fields[b"data"])
    assert stored.task.proxy is None
    assert "cookies" not in stored.task.payload
    assert stored.task.payload["textcaptcha"] == "q"
    assert b"solver exploded" in fields[b"reason"]


async def test_consumer_never_copies_undecodable_bytes_to_the_dlq() -> None:
    redis = PoisonRedis()
    consumer = Consumer(redis, lane="http", consumer_name="c1")

    assert await consumer.read() == []

    stream, fields = redis.xadds[-1]
    assert stream == "captcha:dlq"
    assert fields[b"data"] == b"", "raw poison bytes may carry credentials"
    assert fields[b"reason"].startswith(b"decode_failed:")
