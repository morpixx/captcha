"""Legacy 2captcha form API: POST /in.php and POST /res.php.

Same business flow as the JSON routes in tasks.py, just a different envelope:
plain text (or {status, request} when json=1) instead of {errorId, ...}, and the
answer flattened to the per-type legacy string via legacy_map.format_solution_string.
HTTP status is always 200 — including errors, like the JSON API.
"""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import UUID

import msgspec
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import ORJSONResponse, PlainTextResponse, Response

from captcha.adapters.storage import AccountRepo, TaskRepo
from captcha.application.route_task import route_task
from captcha.domain.enums import CaptchaType, TaskStatus
from captcha.domain.errors import CaptchaError
from captcha.domain.pricing import estimate_cost
from captcha.domain.solution import build_solution, host_for_payload
from captcha.domain.task import Task, TaskEnvelope
from captcha.services.gateway.auth import authenticate
from captcha.services.gateway.deps import get_db, get_state
from captcha.services.gateway.errors import classify
from captcha.services.gateway.legacy_map import (
    ERROR_BAD_PARAMETERS,
    ERROR_NO_SUCH_METHOD,
    LegacyError,
    format_solution_string,
    parse_in_request,
)
from captcha.services.gateway.schemas import status_to_api
from captcha.services.gateway.state import AppState

_log = structlog.get_logger(__name__)

router = APIRouter(tags=["legacy"])

#: Keep in step with routes/tasks.py — the outer bound on a single task.
_TASK_TTL_SEC = 300.0

_NOT_READY = "CAPCHA_NOT_READY"
_UNSOLVABLE = "ERROR_CAPTCHA_UNSOLVABLE"
_TASK_NOT_FOUND = "ERROR_TASK_NOT_FOUND"
_INTERNAL = "ERROR_INTERNAL"


# ---- wire helpers -----------------------------------------------------------


async def _read_params(request: Request) -> dict[str, str]:
    """Merge query params with a form body. Query values are the fallback, form
    values win when both are present (a client can keep the key in the URL)."""
    params = {str(key): str(value) for key, value in request.query_params.items()}
    try:
        form = await request.form()
    except Exception:
        # A body we cannot parse is not a reason to fail the request: fall back
        # to the query string. Log it — a missing python-multipart looked exactly
        # like "client forgot the key" before this line existed.
        _log.warning("legacy.form_parse_failed", path=request.url.path, exc_info=True)
        return params
    for key, value in form.multi_items():
        if isinstance(value, str):
            params[str(key)] = value
    return params


def _json_mode(params: dict[str, str]) -> bool:
    return params.get("json") == "1"


def _ok(value: str, as_json: bool) -> Response:
    if as_json:
        return ORJSONResponse({"status": 1, "request": value})
    return PlainTextResponse(f"OK|{value}")


def _error(code: str, as_json: bool) -> Response:
    if as_json:
        return ORJSONResponse({"status": 0, "request": code})
    return PlainTextResponse(code)


def _auth_error_code(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        code = detail.get("errorCode")
        if code:
            return str(code)
    return "ERROR_KEY_DOES_NOT_EXIST"


def _public_error_code(code: Any) -> str:
    """Envelope codes may be internal (ERR_UNSOLVABLE) or already public."""
    raw = str(code or "ERR_UNKNOWN")
    if raw.startswith("ERROR_"):
        return raw
    return str(classify(raw)["errorCode"])


def _error_from_envelope(error: dict[str, Any] | None) -> str:
    return _public_error_code((error or {}).get("code"))


def _text_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


# ---- /in.php ----------------------------------------------------------------


# Legacy clients send both verbs; params are read from query + form either way.
# Two decorators rather than api_route: FastAPI derives the operation id per
# route, and one route with two verbs emits a duplicate id.
@router.get("/in.php", response_model=None)
@router.post("/in.php", response_model=None)
async def legacy_in(
    request: Request,
    session: Any = Depends(get_db),
    state: AppState = Depends(get_state),
) -> Response:
    params = await _read_params(request)
    as_json = _json_mode(params)

    try:
        account = await authenticate(params.get("key", ""), session)
    except HTTPException as exc:
        return _error(_auth_error_code(exc), as_json)

    try:
        ireq = parse_in_request(params)
    except LegacyError as exc:
        return _error(exc.code, as_json)

    captcha_type = ireq.captcha_type
    # Same gate as the JSON API: no solver here means no answer will ever come.
    if captcha_type not in state.solver_types:
        _log.info("legacy.unsupported", type=captcha_type.value, known=len(state.solver_types))
        return _error("ERROR_TASK_NOT_SUPPORTED", as_json)

    cost = estimate_cost(captcha_type)
    account_repo = AccountRepo(session)
    task_repo = TaskRepo(session)

    try:
        await account_repo.charge(account.id, cost)
    except CaptchaError as exc:
        return _error(_public_error_code(exc.code), as_json)

    payload: dict[str, Any] = dict(ireq.payload)
    db_task = await task_repo.create(
        account_id=account.id,
        type_=captcha_type.value,
        payload=payload,
        cost=cost,
    )
    task_id: UUID = db_task.id

    task = Task(
        id=task_id,
        type=captcha_type,
        payload=payload,
        sitekey=ireq.sitekey,
        site_url=ireq.site_url,
        proxy=_text_or_none(payload.get("proxy")),
        user_agent=ireq.user_agent,
        language=_text_or_none(payload.get("lang")),
        created_at=time.time(),
        deadline_ts=time.time() + _TASK_TTL_SEC,
        client_id=str(account.id),
    )
    envelope = TaskEnvelope(task=task, status=TaskStatus.PENDING)
    lane = route_task(task)

    try:
        # Same ordering as tasks.py: state hash first so /res.php can see the
        # task even if the stream XADD is briefly delayed.
        await state.state_store.set_pending(envelope)
        await state.producer.publish(envelope, lane=lane)
    except Exception as exc:
        # Refund a task that never made it onto the queue.
        _log.error("legacy.enqueue.failed", task_id=str(task_id), error=str(exc))
        try:
            await account_repo.refund(account.id, cost)
        except Exception:
            _log.exception("legacy.refund.failed", task_id=str(task_id))
        return _error(_INTERNAL, as_json)

    return _ok(str(task_id), as_json)


# ---- /res.php ---------------------------------------------------------------


@router.get("/res.php", response_model=None)
@router.post("/res.php", response_model=None)
async def legacy_res(
    request: Request,
    session: Any = Depends(get_db),
    state: AppState = Depends(get_state),
) -> Response:
    params = await _read_params(request)
    as_json = _json_mode(params)

    try:
        account = await authenticate(params.get("key", ""), session)
    except HTTPException as exc:
        return _error(_auth_error_code(exc), as_json)

    action = (params.get("action") or "").strip().lower()
    if action == "get":
        return await _get_result(params, account, session, state, as_json)
    if action == "getbalance":
        return _ok(str(account.balance), as_json)
    if action in {"reportgood", "reportbad"}:
        # No persistence yet — the verdict endpoint is /reportCorrect etc.
        _log.info("legacy.report", action=action, task_id=params.get("id"))
        return _ok("OK_REPORT_RECORDED", as_json)
    if action == "ping":
        return _ok("OK", as_json)
    return _error(ERROR_NO_SUCH_METHOD, as_json)


async def _get_result(
    params: dict[str, str],
    account: Any,
    session: Any,
    state: AppState,
    as_json: bool,
) -> Response:
    raw_id = (params.get("id") or "").strip()
    if not raw_id:
        return _error(ERROR_BAD_PARAMETERS, as_json)
    try:
        task_uuid = UUID(raw_id)
    except (ValueError, TypeError):
        return _error(_TASK_NOT_FOUND, as_json)

    # Redis first: the state hash is the source of truth for the 24h window.
    envelope = await state.state_store.get(task_uuid)
    if envelope is not None:
        if envelope.task.client_id is not None and envelope.task.client_id != str(account.id):
            return _error(_TASK_NOT_FOUND, as_json)

        api_status = status_to_api(envelope.status)
        if api_status == "ready":
            result = msgspec.to_builtins(envelope.result) if envelope.result is not None else None
            solution = build_solution(envelope.task.type, result, site_url=envelope.task.site_url)
            return _ready_answer(envelope.task.type, solution, as_json)
        if api_status == "failed":
            return _error(_error_from_envelope(envelope.error), as_json)
        return _error(_NOT_READY, as_json)

    # Postgres fallback for rows older than the redis TTL.
    repo = TaskRepo(session)
    db_task = await repo.get(task_uuid)
    if db_task is None or db_task.account_id != account.id:
        return _error(_TASK_NOT_FOUND, as_json)

    try:
        task_status = TaskStatus(db_task.status)
    except ValueError:
        task_status = TaskStatus.PROCESSING
    api_status = status_to_api(task_status)

    if api_status == "ready" and db_task.result is not None:
        try:
            db_type: CaptchaType | None = CaptchaType(db_task.type)
        except ValueError:
            db_type = None
        if db_type is not None:
            solution = build_solution(
                db_type,
                db_task.result,
                site_url=host_for_payload(db_task.payload),
            )
            return _ready_answer(db_type, solution, as_json)
        # Unknown historical type: no per-type projection possible; hand back
        # the stored dict as compact JSON rather than an empty answer.
        return _ok(json.dumps(db_task.result, separators=(",", ":"), default=str), as_json)

    if api_status == "failed":
        return _error(_error_from_envelope(db_task.error), as_json)
    return _error(_NOT_READY, as_json)


def _ready_answer(captcha_type: CaptchaType, solution: dict[str, Any] | None, as_json: bool) -> Response:
    if not solution:
        return _error(_UNSOLVABLE, as_json)
    text = format_solution_string(captcha_type, solution)
    if not text:
        return _error(_UNSOLVABLE, as_json)
    return _ok(text, as_json)
