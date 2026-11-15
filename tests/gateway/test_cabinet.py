"""Client cabinet: auth, balance, ledger page, top-up, usage stats.

Routes are exercised through the real ASGI app; only the session and the
authenticated account are faked, so the dependency wiring is the thing under
test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx

from captcha.adapters.storage.models import Transaction
from captcha.services.gateway import routes
from captcha.services.gateway.app import create_app
from captcha.services.gateway.deps import get_db
from captcha.services.gateway.routes import cabinet

ACCOUNT = SimpleNamespace(
    id=uuid.uuid4(),
    api_key="test-key-0000000001",
    balance=Decimal("10.000000"),
    disabled=False,
    created_at=datetime(2026, 11, 1, tzinfo=UTC),
)


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Answers execute() from a queue of canned row lists, in call order."""

    def __init__(self, results: list[list[Any]] | None = None) -> None:
        self._results = list(results or [])
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def get(self, _model: Any, _pk: Any) -> None:
        return None

    async def execute(self, _stmt: Any) -> FakeResult:
        return FakeResult(self._results.pop(0) if self._results else [])


def _client(session: FakeSession | None = None, *, auth: bool = True) -> httpx.AsyncClient:
    app = create_app()
    app.state.ctx = SimpleNamespace(settings=SimpleNamespace(admin_token="secret"))
    sess = session or FakeSession()

    async def _db() -> Any:
        yield sess

    app.dependency_overrides[get_db] = _db
    if auth:
        app.dependency_overrides[cabinet.current_account] = lambda: ACCOUNT
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


async def test_account_requires_a_key() -> None:
    client = _client(auth=False)
    response = await client.get("/account")
    assert response.status_code == 401
    assert response.json()["detail"]["errorCode"] == "ERROR_KEY_DOES_NOT_EXIST"


async def test_account_returns_balance_and_key_tail() -> None:
    client = _client()
    body = (await client.get("/account")).json()
    assert body["accountId"] == str(ACCOUNT.id)
    assert body["balance"] == 10.0
    assert body["keyTail"] == "…0001"


async def test_transactions_page_maps_ledger_rows() -> None:
    row = Transaction(
        id=7,
        account_id=ACCOUNT.id,
        kind="topup",
        amount=Decimal("5"),
        balance_after=Decimal("15"),
        created_at=datetime(2026, 11, 15, tzinfo=UTC),
    )
    client = _client(FakeSession([[row]]))
    body = (await client.get("/account/transactions")).json()
    assert body["items"][0] == {
        "id": 7,
        "kind": "topup",
        "amount": "5.000000",
        "balanceAfter": "15.000000",
        "createdAt": "2026-11-15T00:00:00+00:00",
    }


async def test_stats_rolls_up_two_queries() -> None:
    by_type = [("hcaptcha", "ready", 8, 400.0, Decimal("0.012")), ("hcaptcha", "failed", 2, 100.0, Decimal("0"))]
    by_day = [(datetime(2026, 11, 15).date(), 10, Decimal("0.012"))]
    client = _client(FakeSession([by_type, by_day]))

    body = (await client.get("/account/stats", params={"days": 7})).json()
    assert body["totals"]["tasks"] == 10
    assert body["totals"]["success_rate"] == 0.8
    assert body["by_type"][0]["type"] == "hcaptcha"
    assert body["daily"][0]["tasks"] == 10


async def test_topup_creates_a_pending_intent() -> None:
    session = FakeSession()
    client = _client(session)
    response = await client.post("/account/topup", json={"amount": 5})
    body = response.json()

    assert body["status"] == "pending"
    assert body["provider"] == "manual"
    assert body["amount"] == "5.000000"
    assert body["instructions"]
    assert session.added and session.added[0].amount == Decimal("5")


async def test_topup_rejects_bad_amount() -> None:
    client = _client()
    response = await client.post("/account/topup", json={"amount": -1})
    assert response.json()["errorId"] == 21  # ERROR_BAD_REQUEST via the validation handler


async def test_confirm_requires_admin_token() -> None:
    client = _client()
    response = await client.post(f"/account/topup/{uuid.uuid4()}/confirm")
    assert response.status_code == 401


async def test_confirm_with_token_reports_unknown_intent() -> None:
    client = _client(FakeSession())
    response = await client.post(
        f"/account/topup/{uuid.uuid4()}/confirm",
        headers={"X-Admin-Token": "secret"},
    )
    body = response.json()
    assert body["errorId"] == 16  # ERR_TASK_NOT_FOUND — no such intent


def test_cabinet_router_is_mounted() -> None:
    paths = create_app().openapi()["paths"]
    assert {"/account", "/account/stats", "/account/topup", "/account/transactions"} <= set(paths)
    assert routes is not None
