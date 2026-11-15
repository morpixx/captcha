"""Client URLs are checked before a task is priced or queued (contract §8).

The gateway suite runs without Redis, Postgres or DNS: the repos and the queue
are in-memory shapes and the resolver is a stub. `pageurl`, `websiteURL`,
`challenge_url`, `surl` and `api_server` are client-supplied fetch targets, so a
private/loopback/link-local/metadata host must answer ERROR_BAD_REQUEST, cost
nothing and never reach a stream — in both the JSON and the legacy protocol.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

import httpx
import pytest

from captcha.domain.enums import CaptchaType
from captcha.domain.task import TaskEnvelope
from captcha.services.gateway import auth as auth_module
from captcha.services.gateway.app import create_app
from captcha.services.gateway.deps import get_db
from captcha.services.gateway.routes import legacy as legacy_routes
from captcha.services.gateway.routes import tasks as tasks_routes

CLIENT_KEY = "test-key-0000000009"
PUBLIC = "93.184.216.34"
PAGE = "https://example.com/login"
SITEKEY = "10000000-ffff-ffff-ffff-000000000001"
SOLVABLE = frozenset(
    {
        CaptchaType.TEXT,
        CaptchaType.HCAPTCHA,
        CaptchaType.FUNCAPTCHA,
        CaptchaType.GEETEST_V3,
        CaptchaType.ALTCHA,
    }
)

def resolver_map(
    mapping: dict[str, tuple[str, ...]] | None = None,
    default: tuple[str, ...] = (PUBLIC,),
) -> Any:
    """Stub resolver. Records the names it was asked about, never touches DNS."""
    calls: list[str] = []

    def resolve(host: str) -> tuple[str, ...]:
        calls.append(host)
        if mapping is not None and host in mapping:
            return mapping[host]
        return default

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


# ---- fakes ------------------------------------------------------------------


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
    settings: Any = None
    solver_types: frozenset[CaptchaType] = SOLVABLE
    url_resolver: Any = None


async def _fake_db():
    yield object()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[httpx.AsyncClient, FakeState]:
    CREATED.clear()
    FakeAccountRepo.charges.clear()
    state = FakeState(state_store=FakeStore(), producer=FakeProducer(), url_resolver=resolver_map())

    app = create_app()
    app.state.ctx = state
    app.dependency_overrides[get_db] = _fake_db
    for module in (auth_module, tasks_routes, legacy_routes):
        monkeypatch.setattr(module, "AccountRepo", FakeAccountRepo, raising=False)
        monkeypatch.setattr(module, "TaskRepo", FakeTaskRepo, raising=False)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    return client, state


async def _create(client: httpx.AsyncClient, task: dict[str, Any]) -> dict[str, Any]:
    response = await client.post("/createTask", json={"clientKey": CLIENT_KEY, "task": task})
    assert response.status_code == 200, response.text
    return response.json()


async def _legacy(client: httpx.AsyncClient, **fields: Any) -> httpx.Response:
    return await client.post("/in.php", data={"key": CLIENT_KEY, **fields})


# ---- createTask -------------------------------------------------------------


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.169.254/latest/meta-data/"])
async def test_private_literal_is_rejected_before_charging(wired, url: str) -> None:
    client, state = wired

    body = await _create(client, {"type": "text_captcha", "websiteKey": "k", "websiteURL": url})

    assert body["errorId"] == 21
    assert body["errorCode"] == "ERROR_BAD_REQUEST"
    assert "public http(s)" in body["errorDescription"]
    assert FakeAccountRepo.charges == [], "a rejected URL must not be billed"
    assert state.producer.published == []
    assert CREATED == []


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.0.0.1/"])
async def test_private_literal_is_rejected_in_every_client_url_field(wired, url: str) -> None:
    client, state = wired
    tasks = [
        {"type": "hcaptcha", "websiteKey": SITEKEY, "pageurl": url},
        {"type": "altcha", "websiteURL": PAGE, "challenge_url": url},
        {"type": "funcaptcha", "websiteURL": PAGE, "publickey": "pk", "surl": url},
        {"type": "geetest_v3", "websiteURL": PAGE, "gt": "g", "challenge": "c", "api_server": url},
    ]

    for task in tasks:
        body = await _create(client, task)
        assert body["errorCode"] == "ERROR_BAD_REQUEST", (task["type"], body)

    assert FakeAccountRepo.charges == []
    assert state.producer.published == []


async def test_hostname_that_resolves_private_is_rejected(wired) -> None:
    client, state = wired
    state.url_resolver = resolver_map({"internal.test": ("10.0.0.1",)})

    body = await _create(client, {"type": "text_captcha", "websiteKey": "k", "websiteURL": "https://internal.test/"})

    assert body["errorCode"] == "ERROR_BAD_REQUEST"
    assert state.url_resolver.calls == ["internal.test"]
    assert FakeAccountRepo.charges == []


async def test_unresolvable_host_is_rejected(wired) -> None:
    client, state = wired
    state.url_resolver = resolver_map(default=())

    body = await _create(client, {"type": "text_captcha", "websiteKey": "k", "websiteURL": "https://nope.test/"})

    assert body["errorCode"] == "ERROR_BAD_REQUEST"
    assert FakeAccountRepo.charges == []


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/", "javascript:alert(1)"]
)
async def test_non_http_scheme_is_rejected(wired, url: str) -> None:
    client, state = wired

    body = await _create(client, {"type": "text_captcha", "websiteKey": "k", "websiteURL": url})

    assert body["errorCode"] == "ERROR_BAD_REQUEST"
    assert FakeAccountRepo.charges == []
    assert state.producer.published == []


async def test_public_url_is_accepted_and_reaches_the_worker(wired) -> None:
    client, state = wired

    body = await _create(client, {"type": "text_captcha", "websiteKey": "k", "websiteURL": PAGE})

    assert body["errorId"] == 0, body
    task = state.state_store.by_id[uuid.UUID(body["taskId"])].task
    assert task.site_url == PAGE
    assert state.url_resolver.calls == ["example.com"]
    assert len(FakeAccountRepo.charges) == 1


async def test_bare_host_surl_is_validated_as_https(wired) -> None:
    """FunCaptcha clients send `surl` as a bare host; the solver prepends https."""
    client, state = wired

    body = await _create(
        client,
        {
            "type": "funcaptcha",
            "websiteURL": PAGE,
            "publickey": "pk",
            "surl": "client-api.arkoselabs.com",
        },
    )

    assert body["errorId"] == 0, body
    task = state.state_store.by_id[uuid.UUID(body["taskId"])].task
    assert task.payload["surl"] == "client-api.arkoselabs.com"
    assert "client-api.arkoselabs.com" in state.url_resolver.calls


async def test_a_task_without_urls_still_passes(wired) -> None:
    client, state = wired

    body = await _create(client, {"type": "text_captcha", "websiteKey": "k"})

    assert body["errorId"] == 0, body
    assert state.url_resolver.calls == []


# ---- legacy /in.php ---------------------------------------------------------


async def test_legacy_private_pageurl_is_rejected_before_charging(wired) -> None:
    client, state = wired

    response = await _legacy(client, method="hcaptcha", sitekey=SITEKEY, pageurl="http://10.0.0.1/")

    assert response.text == "ERROR_BAD_REQUEST"
    assert FakeAccountRepo.charges == []
    assert state.producer.published == []


async def test_legacy_private_pageurl_in_json_mode(wired) -> None:
    client, _state = wired

    response = await _legacy(client, method="hcaptcha", sitekey=SITEKEY, pageurl="http://127.0.0.1/", json="1")

    assert response.json() == {"status": 0, "request": "ERROR_BAD_REQUEST"}


@pytest.mark.parametrize(
    "fields",
    [
        {"method": "altcha", "pageurl": PAGE, "challenge_url": "http://169.254.169.254/latest/meta-data/"},
        {"method": "funcaptcha", "publickey": "pk", "pageurl": PAGE, "surl": "http://10.0.0.1"},
        {"method": "geetest", "gt": "g", "challenge": "c", "pageurl": PAGE, "api_server": "http://10.0.0.1"},
    ],
)
async def test_legacy_private_url_in_vendor_field_is_rejected(wired, fields: dict[str, str]) -> None:
    client, state = wired

    response = await _legacy(client, **fields)

    assert response.text == "ERROR_BAD_REQUEST", response.text
    assert FakeAccountRepo.charges == []
    assert state.producer.published == []


async def test_legacy_public_url_is_accepted(wired) -> None:
    client, state = wired

    response = await _legacy(client, method="hcaptcha", sitekey=SITEKEY, pageurl=PAGE)

    assert response.text.startswith("OK|"), response.text
    task = state.state_store.by_id[uuid.UUID(response.text.removeprefix("OK|"))].task
    assert task.site_url == PAGE
    assert state.url_resolver.calls == ["example.com"]
