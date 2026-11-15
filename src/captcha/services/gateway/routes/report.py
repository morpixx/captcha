"""`POST /reportCorrect` and `POST /reportIncorrect` — client feedback on a task.

2captcha expects the verdict to stick to the task row: -1 for a wrong answer, 1
for a right one, NULL while nobody reported. The row is also the only place the
gateway can resolve a task id back to its owner, so the ownership check happens
before the verdict is written.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import ORJSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.adapters.storage import TaskRepo
from captcha.services.gateway.auth import authenticate
from captcha.services.gateway.deps import get_db
from captcha.services.gateway.errors import ERR_TASK_NOT_FOUND, envelope
from captcha.services.gateway.schemas import ReportRequest, ReportResponse

_log = structlog.get_logger(__name__)

router = APIRouter(tags=["report"], default_response_class=ORJSONResponse)


@router.post("/reportCorrect", response_model=None)
async def report_correct(
    body: ReportRequest,
    session: AsyncSession = Depends(get_db),
) -> ORJSONResponse:
    return await _report(body, correct=True, session=session)


@router.post("/reportIncorrect", response_model=None)
async def report_incorrect(
    body: ReportRequest,
    session: AsyncSession = Depends(get_db),
) -> ORJSONResponse:
    return await _report(body, correct=False, session=session)


async def _report(body: ReportRequest, *, correct: bool, session: AsyncSession) -> ORJSONResponse:
    account = await authenticate(body.client_key, session)
    try:
        task_id = UUID(body.task_id)
    except (ValueError, TypeError):
        return ORJSONResponse(envelope(ERR_TASK_NOT_FOUND), status_code=status.HTTP_200_OK)

    repo = TaskRepo(session)
    task = await repo.get(task_id)
    if task is None or task.account_id != account.id:
        # Same answer for "does not exist" and "belongs to someone else".
        return ORJSONResponse(envelope(ERR_TASK_NOT_FOUND), status_code=status.HTTP_200_OK)

    await repo.report(task_id, correct)
    _log.info("task.reported", task_id=str(task_id), correct=correct)
    resp = ReportResponse(error_id=0, status="OK")
    return ORJSONResponse(resp.model_dump(by_alias=True, exclude_none=True), status_code=status.HTTP_200_OK)
