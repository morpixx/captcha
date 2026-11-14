"""`POST /getBalance` — single-row Postgres read on the account."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import ORJSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.services.gateway.auth import authenticate
from captcha.services.gateway.deps import get_db
from captcha.services.gateway.schemas import GetBalanceRequest, GetBalanceResponse

router = APIRouter(tags=["balance"], default_response_class=ORJSONResponse)


@router.post("/getBalance", response_model=None)
async def get_balance(
    body: GetBalanceRequest,
    session: AsyncSession = Depends(get_db),
) -> ORJSONResponse:
    account = await authenticate(body.client_key, session)
    resp = GetBalanceResponse(error_id=0, balance=float(account.balance))
    return ORJSONResponse(
        resp.model_dump(by_alias=True, exclude_none=True),
        status_code=status.HTTP_200_OK,
    )
