"""Gateway lifecycle against real Redis and Postgres.

The contract this file pins down, in order:

1. `createTask` authenticates, prices, charges, writes the Postgres row (with
   credentials redacted), seeds the Redis state hash and publishes exactly one
   envelope to the type's lane stream.
2. `getTaskResult` reads Redis first and projects the solver result onto the
   2captcha shape for the task's type.
3. A missing Redis hash falls back to the Postgres row, including for aged tasks.
4. Failure envelopes surface `errorCode`/`errorDescription`, not a 5xx.
5. An unsupported type or an exhausted quota is refused *before* the charge.
6. Tasks of one account are invisible to another account.
7. `/readyz` exercises both dependencies.

Nothing here calls a captcha vendor: the test drains the stream itself, which is
the worker's job in production, and writes the result the worker would write.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any

import msgspec
import pytest
from support import CLIENT_KEY, OTHER_CLIENT_KEY, create_payload

from captcha.adapters.queue.keys import stream_name, task_hash_key
from captcha.adapters.storage import AccountRepo, TaskRepo
from captcha.domain.enums import CaptchaType, SolveMode, TaskStatus
from captcha.domain.pricing import estimate_cost
from captcha.domain.task import SolverResult, TaskEnvelope
from captcha.services.gateway.state import AppState

pytestmark = pytest.mark.integration

_ENVELOPE = msgspec.msgpack.Decoder(TaskEnvelope)


def _stream_payload(entries: list[Any]) -> TaskEnvelope:
    fields = entries[0][1]
    raw = fields.get(b"data") or fields.get("data")
    assert raw is not None, "stream entry without a data field"
    return _ENVELOPE.decode(raw)


async def test_create_task_charges_persists_and_publishes(client: Any, state: AppState, account: Any) -> None:
    response = await client.post(
        "/createTask",
        json=create_payload(
            websiteURL="https://example.com/login",
            websiteKey="site-key",
            proxyAddress="http://user:pass@127.0.0.1:8000",
            cookies=[{"name": "sid", "value": "secret", "domain": "example.com"}],
        ),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["errorId"] == 0, body
    task_id = uuid.UUID(body["taskId"])

    expected_cost = estimate_cost(CaptchaType.TEXT)

    # --- Postgres: row + redacted payload + ledger-backed balance -------------
    async with state.sessionmaker() as session:
        row = await TaskRepo(session).get(task_id)
        assert row is not None
        assert row.account_id == account.id
        assert row.type == CaptchaType.TEXT.value
        assert row.status == "pending"
        assert row.cost == expected_cost
        # Credentials never reach JSONB; non-secret fields do.
        assert "proxy" not in row.payload
        assert "cookies" not in row.payload
        assert row.payload["site_url"] == "https://example.com/login"
        assert row.payload["sitekey"] == "site-key"

        fresh = await AccountRepo(session).get(account.id)
        assert fresh is not None
        assert fresh.balance == Decimal("25.000000") - expected_cost

    # --- Redis: state hash with the poll window ------------------------------
    envelope = await state.state_store.get(task_id)
    assert envelope is not None
    assert envelope.status is TaskStatus.PENDING
    # The live envelope keeps the full payload; only the persisted copies are redacted.
    assert envelope.task.proxy == "http://user:pass@127.0.0.1:8000"
    assert envelope.task.payload["cookies"][0]["value"] == "secret"
    ttl = await state.redis.ttl(task_hash_key(state.settings.redis_stream_prefix, task_id))
    assert 0 < ttl <= 24 * 60 * 60

    # --- Stream: exactly one envelope, on the lane the catalog dictates -----
    stream = stream_name(state.settings.redis_stream_prefix, "cv")
    entries = await state.redis.xrange(stream)
    assert len(entries) == 1, f"expected one envelope on {stream}, got {len(entries)}"
    published = _stream_payload(entries)
    assert published.task.id == task_id
    assert published.task.type is CaptchaType.TEXT
    assert published.task.proxy == "http://user:pass@127.0.0.1:8000"


async def test_get_task_result_projects_a_ready_result(client: Any, state: AppState, account: Any) -> None:
    created = await client.post("/createTask", json=create_payload())
    task_id = uuid.UUID(created.json()["taskId"])

    pending = await client.get("/getTaskResult", params={"clientKey": CLIENT_KEY, "taskId": str(task_id)})
    assert pending.json()["status"] == "processing"

    now = time.time()
    await state.state_store.set_processing(task_id, "it-worker", started_at=now)
    await state.state_store.set_ready(
        task_id,
        SolverResult(text="abcd", solve_mode=SolveMode.HTTP, latency_ms=12.0),
        finished_at=now + 1,
    )
    async with state.sessionmaker() as session:
        repo = TaskRepo(session)
        await repo.update_status(task_id, "processing", worker_id="it-worker")
        await repo.update_result(task_id, result={"text": "abcd"})
        await session.commit()

    ready = await client.get("/getTaskResult", params={"clientKey": CLIENT_KEY, "taskId": str(task_id)})
    body = ready.json()
    assert body["status"] == "ready"
    assert body["solution"] == {"text": "abcd"}
    assert body["solveCount"] == 1
    assert body["createTime"] > 0
    assert body["endTime"] >= body["createTime"]


async def test_get_task_result_surfaces_a_failure(client: Any, state: AppState, account: Any) -> None:
    created = await client.post("/createTask", json=create_payload())
    task_id = uuid.UUID(created.json()["taskId"])

    await state.state_store.set_failed(
        task_id,
        {"code": "ERR_UNSOLVABLE", "message": "no answer"},
        finished_at=time.time(),
    )

    response = await client.get("/getTaskResult", params={"clientKey": CLIENT_KEY, "taskId": str(task_id)})
    body = response.json()
    assert body["status"] == "failed"
    assert body["errorCode"] == "ERR_UNSOLVABLE"
    assert body["errorDescription"] == "no answer"


async def test_get_task_result_falls_back_to_postgres(client: Any, state: AppState, account: Any) -> None:
    """A task older than the Redis TTL still resolves from the row."""
    created = await client.post("/createTask", json=create_payload())
    task_id = uuid.UUID(created.json()["taskId"])

    await state.state_store.delete(task_id)
    async with state.sessionmaker() as session:
        repo = TaskRepo(session)
        await repo.update_status(task_id, "processing", worker_id="it-worker")
        await repo.update_result(task_id, result={"text": "db-fallback"})
        await session.commit()

    response = await client.get("/getTaskResult", params={"clientKey": CLIENT_KEY, "taskId": str(task_id)})
    body = response.json()
    assert body["status"] == "ready"
    assert body["solution"] == {"text": "db-fallback"}


async def test_unsupported_type_is_refused_before_charging(
    client: Any, state: AppState, account: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin the deployment's capabilities instead of relying on which optional
    # extras happen to be installed: capy is a real enum value, just not one
    # this fake deployment claims.
    monkeypatch.setattr(state, "solver_types", frozenset({CaptchaType.TEXT}))

    response = await client.post("/createTask", json=create_payload(type=CaptchaType.CAPY.value))
    body = response.json()
    assert body["errorId"] == 12
    assert body["errorCode"] == "ERROR_TASK_NOT_SUPPORTED"

    async with state.sessionmaker() as session:
        from sqlalchemy import func, select

        from captcha.adapters.storage.models import Task

        count = (await session.execute(select(func.count()).select_from(Task))).scalar_one()
        assert count == 0, "a refused task must not create a row"
        fresh = await AccountRepo(session).get(account.id)
        assert fresh is not None
        assert fresh.balance == Decimal("25.000000"), "a refused task must not be billed"

    assert await state.redis.xlen(stream_name(state.settings.redis_stream_prefix, "cv")) == 0


async def test_exhausted_quota_is_refused_before_charging(client: Any, state: AppState, account: Any) -> None:
    state.settings = state.settings.model_copy(update={"rate_limit_create_per_min": 1})

    first = await client.post("/createTask", json=create_payload())
    assert first.json()["errorId"] == 0

    second = await client.post("/createTask", json=create_payload())
    body = second.json()
    assert body["errorId"] == 2
    assert body["errorCode"] == "ERROR_NO_SLOT_AVAILABLE"

    async with state.sessionmaker() as session:
        from sqlalchemy import func, select

        from captcha.adapters.storage.models import Task

        count = (await session.execute(select(func.count()).select_from(Task))).scalar_one()
        assert count == 1
        fresh = await AccountRepo(session).get(account.id)
        assert fresh is not None
        assert fresh.balance == Decimal("25.000000") - estimate_cost(CaptchaType.TEXT)


async def test_another_account_cannot_read_the_task(
    client: Any, state: AppState, account: Any, other_account: Any
) -> None:
    created = await client.post("/createTask", json=create_payload())
    task_id = created.json()["taskId"]

    response = await client.get(
        "/getTaskResult",
        params={"clientKey": OTHER_CLIENT_KEY, "taskId": task_id},
    )
    body = response.json()
    assert body["errorId"] == 16
    assert body["errorCode"] == "ERROR_TASK_NOT_FOUND"


async def test_malformed_task_id_is_not_found(client: Any, account: Any) -> None:
    response = await client.get("/getTaskResult", params={"clientKey": CLIENT_KEY, "taskId": "not-a-uuid"})
    body = response.json()
    assert body["errorId"] == 16
    assert body["errorCode"] == "ERROR_TASK_NOT_FOUND"


async def test_readyz_checks_redis_and_postgres(client: Any, state: AppState) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"ok": True, "redis": True, "postgres": True}

    health = await client.get("/healthz")
    assert health.json() == {"ok": True}

