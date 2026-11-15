"""`POST /createTask` and `POST /getTaskResult` — the 2captcha core pair."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import msgspec
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import ORJSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.adapters.storage import AccountRepo, TaskRepo
from captcha.adapters.storage.models import Task as DbTask
from captcha.application.route_task import route_task
from captcha.domain.enums import CaptchaType, TaskStatus
from captcha.domain.errors import CaptchaError, UrlRejectedError
from captcha.domain.pricing import estimate_cost
from captcha.domain.redaction import redact_payload
from captcha.domain.solution import build_solution, host_for_payload
from captcha.domain.task import Task, TaskEnvelope
from captcha.observability.metrics import tasks_created_total
from captcha.services.gateway.auth import authenticate
from captcha.services.gateway.deps import get_db, get_state
from captcha.services.gateway.errors import (
    ERR_BAD_REQUEST,
    ERR_NO_SLOT,
    ERR_TASK_NOT_FOUND,
    ERR_UNSUPPORTED_TYPE,
    ERR_ZERO_BALANCE,
    classify,
    envelope,
)
from captcha.services.gateway.quotas import QuotaGuard, QuotaLimits
from captcha.services.gateway.schemas import (
    CreateTaskRequest,
    CreateTaskResponse,
    GetTaskResultRequest,
    GetTaskResultResponse,
    status_to_api,
)
from captcha.services.gateway.state import AppState
from captcha.services.gateway.url_guard import validate_client_urls

_log = structlog.get_logger(__name__)

#: Hard stop for a task whose solver hangs. The worker also has a per-call
#: timeout; this is the outer bound that the deadline-aware solvers honour.
_TASK_TTL_SEC = 300.0

router = APIRouter(tags=["tasks"], default_response_class=ORJSONResponse)


@router.post("/createTask", response_model=None)
async def create_task(
    body: CreateTaskRequest,
    session: AsyncSession = Depends(get_db),
    state: AppState = Depends(get_state),
) -> ORJSONResponse:
    account = await authenticate(body.client_key, session)
    acc_repo = AccountRepo(session)
    task_repo = TaskRepo(session)

    # Reject types the enum doesn't know. pydantic has already validated, but a
    # hostile client can craft an unknown value when the enum class allows str
    # coercion — keep the check explicit.
    try:
        ct = CaptchaType(body.task.type)
    except ValueError:
        return ORJSONResponse(envelope(ERR_UNSUPPORTED_TYPE), status_code=status.HTTP_200_OK)

    # No solver in this deployment means no answer will ever come — refuse
    # before charging, not after the deadline.
    if ct not in state.solver_types:
        _log.info("task.unsupported", type=ct.value, known=len(state.solver_types))
        return ORJSONResponse(envelope(ERR_UNSUPPORTED_TYPE), status_code=status.HTTP_200_OK)

    # Everything downstream reads the payload in its wire-normalised shape: the
    # SSRF guard below, the stored row and the envelope all use this one dict.
    payload: dict[str, Any] = body.task.model_dump(mode="json", by_alias=False, exclude_none=True)
    payload.pop("type", None)

    # SSRF guard before any money moves: pageurl/websiteURL/challenge_url/surl/
    # api_server and callbackUrl are fetched by a worker later, and a client
    # must not be able to point that fetch at the host's own network.
    try:
        validate_client_urls(payload, callback_url=body.callback_url)
    except UrlRejectedError as exc:
        _log.info(
            "task.url_rejected",
            type=ct.value,
            field=exc.context.get("field"),
            host=exc.context.get("host"),
            address=exc.context.get("address"),
            account_id=str(account.id),
        )
        return ORJSONResponse(envelope(ERR_BAD_REQUEST), status_code=status.HTTP_200_OK)

    cost = estimate_cost(ct)

    # Rate limit / quota, before the charge: a task the account isn't allowed
    # to create must not cost anything. Limits come from Settings and are
    # counted in Redis; a guard with no limits or no Redis allows everything.
    guard = QuotaGuard(getattr(state, "redis", None), QuotaLimits.from_settings(getattr(state, "settings", None)))
    quota_reason = await guard.check(account.id, cost)
    if quota_reason is not None:
        _log.info(
            "task.quota_rejected",
            reason=quota_reason,
            type=ct.value,
            account_id=str(account.id),
        )
        return ORJSONResponse(envelope(ERR_NO_SLOT), status_code=status.HTTP_200_OK)

    try:
        await acc_repo.charge(account.id, cost)
    except CaptchaError as exc:
        if exc.code == "ERR_BALANCE":
            return ORJSONResponse(envelope(ERR_ZERO_BALANCE), status_code=status.HTTP_200_OK)
        return ORJSONResponse(classify(exc.code, exc.message), status_code=status.HTTP_200_OK)

    # DB row first — the task id is the UUID the row carries.
    # Postgres gets a credential-free copy: proxy*/cookies never reach JSONB.
    # The full payload stays on the Task/envelope and therefore in Redis (24h).
    db_task = await task_repo.create(
        account_id=account.id,
        type_=ct.value,
        payload=redact_payload(payload),
        cost=cost,
        callback_url=body.callback_url,
    )
    task_id: UUID = db_task.id

    # Build the immutable Task + envelope used downstream. The payload stored
    # on-queue is a stripped version of the API body (extras and non-struct
    # fields live on `payload`).
    task = Task(
        id=task_id,
        type=ct,
        payload=payload,
        sitekey=body.task.sitekey,
        site_url=body.task.site_url,
        proxy=body.task.proxy,
        user_agent=body.task.user_agent,
        language=body.task.language,
        callback_url=body.callback_url,
        created_at=time.time(),
        deadline_ts=time.time() + _TASK_TTL_SEC,
        client_id=str(account.id),
    )
    env = TaskEnvelope(task=task, status=TaskStatus.PENDING)

    lane = route_task(task)
    try:
        # State hash first — getTaskResult must find the task even if the stream
        # XADD is briefly delayed. set_pending seeds status=PENDING with 24h TTL.
        await state.state_store.set_pending(env)
        await state.producer.publish(env, lane=lane)
    except Exception as exc:
        # Rollback the charge so the client isn't billed for a dropped task.
        _log.error("enqueue.failed", task_id=str(task_id), error=str(exc))
        try:
            await acc_repo.refund(account.id, cost)
        except Exception:
            _log.exception("refund.failed", task_id=str(task_id))
        return ORJSONResponse(classify("ERR_CONFIG", "enqueue failed"), status_code=status.HTTP_200_OK)

    # Book the task into the active/daily counters now that it is on a stream.
    # A quota-bookkeeping failure must never fail an already-enqueued task.
    try:
        await guard.record(account.id, task_id, cost)
    except Exception:
        _log.exception("task.quota_record_failed", task_id=str(task_id))

    tasks_created_total.labels(type=ct.value, lane=lane).inc()

    resp = CreateTaskResponse(error_id=0, task_id=str(task_id))
    return ORJSONResponse(resp.model_dump(by_alias=True, exclude_none=True))


@router.post("/getTaskResult", response_model=None)
async def get_task_result(
    body: GetTaskResultRequest,
    session: AsyncSession = Depends(get_db),
    state: AppState = Depends(get_state),
) -> ORJSONResponse:
    account = await authenticate(body.client_key, session)

    try:
        task_uuid = UUID(body.task_id)
    except (ValueError, TypeError):
        return ORJSONResponse(envelope(ERR_TASK_NOT_FOUND), status_code=status.HTTP_200_OK)

    # Redis-first: the state hash is written by the worker and is the source of
    # truth for the 24h polling window. Postgres is the fallback for aged rows.
    env = await state.state_store.get(task_uuid)
    if env is not None:
        if env.task.client_id is not None and env.task.client_id != str(account.id):
            # Don't leak another account's task.
            return ORJSONResponse(envelope(ERR_TASK_NOT_FOUND), status_code=status.HTTP_200_OK)

        api_status = status_to_api(env.status)
        resp = GetTaskResultResponse(error_id=0, status=api_status)
        if env.result is not None and api_status == "ready":
            result_dict: dict[str, Any] = msgspec.to_builtins(env.result)
            # Per-type projection: a 2captcha client reads one specific key per type
            # (gRecaptchaResponse / token / challenge+validate+seccode / cookies), so
            # the shape comes from the captcha type, not from whichever fields the
            # solver happened to fill.
            resp.solution = build_solution(env.task.type, result_dict, site_url=env.task.site_url)
            if env.started_at:
                resp.create_time = int(env.started_at)
            if env.finished_at:
                resp.end_time = int(env.finished_at)
            resp.solve_count = 1
        if env.error is not None and api_status == "failed":
            resp.error_code = str(env.error.get("code") or "ERR_UNKNOWN")
            resp.error_description = str(env.error.get("message") or "")
        return ORJSONResponse(resp.model_dump(by_alias=True, exclude_none=True))

    # Fallback: hit the DB. The redis hash has a 24h TTL; older tasks still
    # resolve from Postgres where the row never expires.
    repo = TaskRepo(session)
    db_task: DbTask | None = await repo.get(task_uuid)
    if db_task is None or db_task.account_id != account.id:
        return ORJSONResponse(envelope(ERR_TASK_NOT_FOUND), status_code=status.HTTP_200_OK)

    try:
        ts = TaskStatus(db_task.status)
    except ValueError:
        ts = TaskStatus.PROCESSING
    api_status = status_to_api(ts)
    resp = GetTaskResultResponse(error_id=0, status=api_status)
    if api_status == "ready" and db_task.result is not None:
        try:
            db_type: CaptchaType | None = CaptchaType(db_task.type)
        except ValueError:
            db_type = None
        if db_type is None:
            # Type from an older release we no longer know — hand back what's stored.
            resp.solution = db_task.result
        else:
            resp.solution = build_solution(
                db_type,
                db_task.result,
                site_url=host_for_payload(db_task.payload),
            )
        if db_task.started_at:
            resp.create_time = int(db_task.started_at.timestamp())
        if db_task.finished_at:
            resp.end_time = int(db_task.finished_at.timestamp())
        resp.solve_count = 1
    if api_status == "failed" and db_task.error is not None:
        resp.error_code = str(db_task.error.get("code") or "ERR_UNKNOWN")
        resp.error_description = str(db_task.error.get("message") or "")
    return ORJSONResponse(resp.model_dump(by_alias=True, exclude_none=True))


@router.get("/getTaskResult", response_model=None)
async def get_task_result_get(
    client_key: str = Query(alias="clientKey"),
    task_id: str = Query(alias="taskId"),
    session: AsyncSession = Depends(get_db),
    state: AppState = Depends(get_state),
) -> ORJSONResponse:
    """GET variant; 2captcha clients built on the query-string form use this."""
    body = GetTaskResultRequest(client_key=client_key, task_id=task_id)
    return await get_task_result(body, session=session, state=state)


# Alias — 2captcha's historical `/res.php` behavior some clients still send a
# flat `getTaskResult` on. Keeping the shape consistent is enough.
@router.post("/getTaskResult/", include_in_schema=False, response_model=None)
async def get_task_result_slash(
    body: GetTaskResultRequest,
    session: AsyncSession = Depends(get_db),
    state: AppState = Depends(get_state),
) -> ORJSONResponse:
    return await get_task_result(body, session=session, state=state)


# Defensive: pydantic validators return via the global exception handler; keep
# this stub here so a crash in the handler path still returns the 2captcha
# shape rather than a FastAPI default.
@router.get("/createTask", include_in_schema=False)
async def create_task_wrong_verb() -> ORJSONResponse:
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail={"errorId": 21, "errorCode": "ERROR_BAD_REQUEST", "errorDescription": "Use POST"},
    )
