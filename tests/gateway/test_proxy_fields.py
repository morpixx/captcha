"""createTask end-to-end with the official 2captcha/capsolver spellings.

Infrastructure is faked exactly like test_api_flow: no redis, no postgres. The
point here is the trip from the wire fields into the immutable Task the workers
see — official type name -> CaptchaType, flat proxy fields -> one URL, hCaptcha
extras -> payload — and that a malformed request costs the client nothing.
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
from captcha.services.gateway.routes import tasks as tasks_routes

CLIENT_KEY = "test-key-0000000002"
PAGE = "https://example.com/login"
SITEKEY = "10000000-ffff-ffff-ffff-000000000001"
SOLVABLE = frozenset({CaptchaType.TEXT, CaptchaType.HCAPTCHA, CaptchaType.HCAPTCHA_ENTERPRISE})


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


def _public_resolver(_host: str) -> tuple[str, ...]:
    """Every test host is public; the gateway guard must not touch real DNS."""
    return ("93.184.216.34",)


@dataclass
class FakeState:
    state_store: FakeStore
    producer: FakeProducer
    settings: Any = None
    solver_types: frozenset[CaptchaType] = SOLVABLE
    url_resolver: Any = _public_resolver


async def _fake_db():
    yield object()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[httpx.AsyncClient, FakeState]:
    CREATED.clear()
    FakeAccountRepo.charges.clear()
    state = FakeState(state_store=FakeStore(), producer=FakeProducer())

    app = create_app()
    app.state.ctx = state
    app.dependency_overrides[get_db] = _fake_db
    for module in (auth_module, tasks_routes):
        monkeypatch.setattr(module, "AccountRepo", FakeAccountRepo, raising=False)
        monkeypatch.setattr(module, "TaskRepo", FakeTaskRepo, raising=False)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    return client, state


async def _create(client: httpx.AsyncClient, task: dict[str, Any]) -> dict[str, Any]:
    response = await client.post("/createTask", json={"clientKey": CLIENT_KEY, "task": task})
    assert response.status_code == 200, response.text
    return response.json()


# ---- happy paths ------------------------------------------------------------


async def test_official_type_and_flat_proxy_reach_the_worker(wired) -> None:
    client, state = wired
    body = await _create(
        client,
        {
            "type": "HCaptchaTask",
            "websiteURL": PAGE,
            "websiteKey": SITEKEY,
            "userAgent": "Mozilla/5.0",
            "proxyType": "socks5",
            "proxyAddress": "203.0.113.10",
            "proxyPort": "1080",
            "proxyLogin": "proxy-user",
            "proxyPassword": "proxy-secret",
            "isInvisible": True,
        },
    )
    assert body["errorId"] == 0
    task_id = uuid.UUID(body["taskId"])
    task = state.state_store.by_id[task_id].task

    assert task.type is CaptchaType.HCAPTCHA
    assert task.proxy == "socks5://proxy-user:proxy-secret@203.0.113.10:1080"
    assert task.sitekey == SITEKEY
    assert task.site_url == PAGE
    assert task.user_agent == "Mozilla/5.0"
    # The scraper reads the same URL; the flat fields must not survive in the
    # stored payload (they carry the password).
    assert task.payload["proxy"] == task.proxy
    assert task.payload["invisible"] is True
    for gone in ("proxyType", "proxyAddress", "proxyPort", "proxyLogin", "proxyPassword", "type"):
        assert gone not in task.payload
    assert state.producer.published == [(task_id, "browser")]
    assert CREATED[0].type == "hcaptcha"


async def test_proxy_string_is_accepted_and_canonicalised(wired) -> None:
    client, state = wired
    body = await _create(client, {"type": "HCaptchaTaskProxyless", "proxy": "HTTPS://user:pass@proxy.example:3128"})
    task = state.state_store.by_id[uuid.UUID(body["taskId"])].task
    assert task.proxy == "https://user:pass@proxy.example:3128"


async def test_hcaptcha_extras_and_enterprise_type(wired) -> None:
    client, state = wired
    body = await _create(
        client,
        {
            "type": "HCaptchaTaskEnterpriseProxyless",
            "websiteURL": PAGE,
            "websiteKey": SITEKEY,
            "isInvisible": "1",
            "enterprisePayload": {"rqdata": "rq-enterprise"},
            "cookies": "cf_clearance=abc; session=def",
            "host": "example.com",
            "userAgent": "Mozilla/5.0",
        },
    )
    assert body["errorId"] == 0
    task = state.state_store.by_id[uuid.UUID(body["taskId"])].task

    assert task.type is CaptchaType.HCAPTCHA_ENTERPRISE
    assert task.payload["invisible"] is True
    assert task.payload["rqdata"] == "rq-enterprise"
    assert task.payload["enterprise_payload"] == {"rqdata": "rq-enterprise"}
    assert task.payload["cookies"] == [
        {"name": "cf_clearance", "value": "abc"},
        {"name": "session", "value": "def"},
    ]
    assert task.payload["host"] == "example.com"
    assert task.payload["user_agent"] == "Mozilla/5.0"
    assert task.user_agent == "Mozilla/5.0"


async def test_snake_case_type_still_works(wired) -> None:
    client, state = wired
    body = await _create(client, {"type": "hcaptcha", "websiteURL": PAGE, "websiteKey": SITEKEY})
    task = state.state_store.by_id[uuid.UUID(body["taskId"])].task
    assert task.type is CaptchaType.HCAPTCHA
    assert task.proxy is None


# ---- rejection paths --------------------------------------------------------


async def test_bad_proxy_is_a_bad_request_without_charging(wired) -> None:
    client, state = wired
    body = await _create(
        client,
        {
            "type": "HCaptchaTaskProxyless",
            "proxyType": "http",
            "proxyAddress": "203.0.113.10",
            "proxyPort": "70000",
        },
    )
    assert body["errorId"] == 21
    assert body["errorCode"] == "ERROR_BAD_REQUEST"
    assert "proxyPort" in body["errorDescription"]
    assert FakeAccountRepo.charges == []
    assert state.producer.published == []
    assert CREATED == []


async def test_unknown_type_is_a_bad_request_without_charging(wired) -> None:
    client, state = wired
    body = await _create(client, {"type": "SolvemediaTask", "websiteURL": PAGE})
    assert body["errorId"] == 21
    assert body["errorCode"] == "ERROR_BAD_REQUEST"
    assert "SolvemediaTask" in body["errorDescription"]
    assert FakeAccountRepo.charges == []
    assert state.producer.published == []
    assert CREATED == []


async def test_known_type_without_a_solver_is_unsupported(wired) -> None:
    client, state = wired
    body = await _create(client, {"type": "RecaptchaV2TaskProxyless", "websiteURL": PAGE, "websiteKey": "k"})
    assert body["errorId"] == 12
    assert body["errorCode"] == "ERROR_TASK_NOT_SUPPORTED"
    assert FakeAccountRepo.charges == []
    assert state.producer.published == []
