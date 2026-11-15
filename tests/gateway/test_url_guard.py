"""SSRF guard at the gateway boundary: client URLs never reach a private host.

Contract §8: `pageurl`, `websiteURL`, `challenge_url`, `surl`, `api_server`
(and `callbackUrl`) are fetched by a worker after the task is queued, so the
gateway checks them before the charge. No DNS here: the guard is shape + IP
literal, which is exactly what an offline suite can prove. The route tests use
the same in-memory fakes the other gateway tests do — no redis, no postgres, no
network.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar

import httpx
import pytest

from captcha.domain.enums import CaptchaType
from captcha.domain.errors import UrlRejectedError
from captcha.domain.task import TaskEnvelope
from captcha.services.gateway import auth as auth_module
from captcha.services.gateway.app import create_app
from captcha.services.gateway.deps import get_db
from captcha.services.gateway.routes import legacy as legacy_routes
from captcha.services.gateway.routes import tasks as tasks_routes
from captcha.services.gateway.url_guard import URL_FIELDS, validate_client_urls

CLIENT_KEY = "test-key-0000000042"
PRIVATE_URLS = (
    "http://127.0.0.1:8080/admin",
    "https://10.1.2.3/challenge",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[fd00::1]/",
    "http://localhost/",
    "http://app.localhost/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "http://user:pass@10.0.0.1/",
)


# ---- fakes ------------------------------------------------------------------


@dataclass
class FakeAccount:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
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
        self.published: list[uuid.UUID] = []

    async def publish(self, envelope: TaskEnvelope, lane: str) -> None:
        self.published.append(envelope.task.id)


@dataclass
class FakeState:
    state_store: FakeStore
    producer: FakeProducer
    redis: Any = None
    settings: Any = None
    solver_types: frozenset[CaptchaType] = frozenset(
        {
            CaptchaType.TEXT,
            CaptchaType.HCAPTCHA,
            CaptchaType.FUNCAPTCHA,
            CaptchaType.GEETEST_V3,
            CaptchaType.ALTCHA,
        }
    )


async def _fake_db():
    yield object()


def _build(monkeypatch: pytest.MonkeyPatch) -> tuple[httpx.AsyncClient, FakeState]:
    CREATED.clear()
    FakeAccountRepo.charges.clear()
    state = FakeState(state_store=FakeStore(), producer=FakeProducer())

    app = create_app()
    app.state.ctx = state
    app.dependency_overrides[get_db] = _fake_db
    for module in (auth_module, tasks_routes, legacy_routes):
        monkeypatch.setattr(module, "AccountRepo", FakeAccountRepo, raising=False)
        monkeypatch.setattr(module, "TaskRepo", FakeTaskRepo, raising=False)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    return client, state


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[httpx.AsyncClient, FakeState]:
    return _build(monkeypatch)


# ---- pure guard -------------------------------------------------------------


@pytest.mark.parametrize("value", PRIVATE_URLS)
def test_every_url_field_rejects_a_private_target(value: str) -> None:
    for field_name in URL_FIELDS:
        with pytest.raises(UrlRejectedError) as info:
            validate_client_urls({field_name: value})
        assert info.value.code == "ERR_URL_REJECTED"
        assert info.value.context["field"] == field_name


@pytest.mark.parametrize("value", PRIVATE_URLS)
def test_callback_url_rejects_a_private_target(value: str) -> None:
    with pytest.raises(UrlRejectedError) as info:
        validate_client_urls({}, callback_url=value)
    assert info.value.context["field"] == "callbackUrl"


def test_error_context_never_carries_the_url() -> None:
    secret = "https://10.0.0.1/steal?token=super-secret"
    with pytest.raises(UrlRejectedError) as info:
        validate_client_urls({"pageurl": secret})
    assert "super-secret" not in str(info.value.context)
    assert info.value.context["address"] == "10.0.0.1"


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/login",
        "http://example.com/",
        "example.com",
        "//example.com/widget",
        "https://client-api.arkoselabs.com",
        "https://api.geetest.com/gettype.php?x=1",
        "https://example.com/a" + "b" * 100,
    ],
)
def test_public_urls_pass(value: str) -> None:
    validate_client_urls({"websiteURL": value}, callback_url="https://client.example/cb")


def test_empty_and_non_string_values_are_left_to_the_schema() -> None:
    validate_client_urls({"pageurl": "", "websiteURL": None, "api_server": 12, "challenge_url": ["x"]})


def test_whitespace_in_a_url_is_rejected() -> None:
    with pytest.raises(UrlRejectedError):
        validate_client_urls({"pageurl": "https://exa mple.com/"})


def test_unrelated_payload_keys_are_not_touched() -> None:
    validate_client_urls(
        {
            "textcaptcha": "10.0.0.1 is not a url here",
            "body": "aGVsbG8=",
            # The text solver's own endpoint policy compares it to LLM_ENDPOINT;
            # a local model must stay reachable.
            "endpoint": "http://127.0.0.1:11434",
        }
    )


def test_inline_image_payloads_are_not_parsed_as_urls() -> None:
    validate_client_urls({"captcha_img": "iVBORw0KGgoAAAANSUhEUg" + "A" * 3000})


@pytest.mark.parametrize("value", ["http://10.0.0.1/x.png", "https://169.254.169.254/x.png"])
def test_image_urls_are_guarded_when_they_are_urls(value: str) -> None:
    with pytest.raises(UrlRejectedError):
        validate_client_urls({"image_url": value})


@pytest.mark.parametrize("field_name", ["host", "domain", "script_url", "tl_url", "widget_url"])
def test_engine_specific_override_urls_are_guarded(field_name: str) -> None:
    with pytest.raises(UrlRejectedError):
        validate_client_urls({field_name: "http://10.0.0.1"})


# ---- createTask -------------------------------------------------------------


async def _create(client: httpx.AsyncClient, task: dict[str, Any], **extra: Any) -> httpx.Response:
    return await client.post("/createTask", json={"clientKey": CLIENT_KEY, "task": task, **extra})


async def test_create_task_rejects_a_private_website_url_without_charging(wired) -> None:
    client, state = wired
    response = await _create(
        client,
        {"type": "text_captcha", "websiteURL": "http://169.254.169.254/latest/meta-data/", "textcaptcha": "q"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["errorId"] == 21
    assert body["errorCode"] == "ERROR_BAD_REQUEST"
    assert FakeAccountRepo.charges == [], "a rejected URL must not be billed"
    assert state.producer.published == [], "a rejected URL must not reach a stream"
    assert CREATED == []


async def test_create_task_rejects_a_private_callback_url_without_charging(wired) -> None:
    client, state = wired
    response = await _create(
        client,
        {"type": "text_captcha", "websiteURL": "https://example.com", "textcaptcha": "q"},
        callbackUrl="http://10.0.0.5/callback",
    )

    assert response.json()["errorId"] == 21
    assert FakeAccountRepo.charges == []
    assert state.producer.published == []


async def test_create_task_accepts_public_urls_and_charges_once(wired) -> None:
    client, state = wired
    response = await _create(
        client,
        {"type": "text_captcha", "websiteURL": "https://example.com/login", "textcaptcha": "q"},
        callbackUrl="https://client.example/cb",
    )

    body = response.json()
    assert body["errorId"] == 0, body
    assert len(FakeAccountRepo.charges) == 1
    assert len(state.producer.published) == 1


# ---- legacy /in.php ---------------------------------------------------------


async def _legacy(client: httpx.AsyncClient, **fields: Any) -> httpx.Response:
    return await client.post("/in.php", data={"key": CLIENT_KEY, **fields})


@pytest.mark.parametrize(
    "fields",
    [
        {"method": "hcaptcha", "sitekey": "k", "pageurl": "http://127.0.0.1/"},
        {"method": "hcaptcha", "sitekey": "k", "pageurl": "http://169.254.169.254/"},
        {"method": "funcaptcha", "publickey": "k", "pageurl": "https://example.com", "surl": "http://10.0.0.1"},
        {"method": "geetest", "gt": "gt", "challenge": "c", "pageurl": "https://example.com", "api_server": "http://[::1]/"},
        {"method": "altcha", "pageurl": "https://example.com", "challenge_url": "http://metadata.google.internal/x"},
    ],
)
async def test_legacy_in_rejects_private_urls_without_charging(wired, fields: dict[str, Any]) -> None:
    client, state = wired
    response = await _legacy(client, **fields)

    assert response.text == "ERROR_BAD_PARAMETERS", response.text
    assert FakeAccountRepo.charges == []
    assert state.producer.published == []
    assert CREATED == []


async def test_legacy_in_still_accepts_a_public_page(wired) -> None:
    client, state = wired
    response = await _legacy(client, method="hcaptcha", sitekey="k", pageurl="https://example.com/login")

    assert response.text.startswith("OK|"), response.text
    assert len(FakeAccountRepo.charges) == 1
    assert len(state.producer.published) == 1
