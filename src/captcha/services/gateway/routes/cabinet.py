"""Client cabinet: balance, ledger, top-ups, usage stats.

Every endpoint authenticates with the account's `clientKey` in an
`X-Client-Key` header — the same secret the API uses, so a frontend or a Telegram
bot has exactly one credential to hold. Money arrives through a provider
(`application/payments.py`); with the default `manual` provider the credit is
confirmed by an operator call, which is where an acquirer's webhook plugs in.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import ORJSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.adapters.storage import Account, AccountRepo, PaymentIntentRepo, TransactionRepo, UsageRepo
from captcha.application.payments import get_provider
from captcha.application.usage import DayRow, TypeRow, summarize
from captcha.services.gateway.auth import authenticate
from captcha.services.gateway.deps import get_db
from captcha.services.gateway.errors import ERR_BAD_REQUEST, ERR_TASK_NOT_FOUND, envelope
from captcha.services.gateway.routes.admin import require_admin
from captcha.services.gateway.schemas import (
    CabinetAccountResponse,
    TopupConfirmResponse,
    TopupRequest,
    TopupResponse,
    TransactionListResponse,
    TransactionOut,
)

_log = structlog.get_logger(__name__)

router = APIRouter(prefix="/account", tags=["cabinet"], default_response_class=ORJSONResponse)

_MAX_DAYS = 90


async def current_account(
    session: AsyncSession = Depends(get_db),
    x_client_key: str | None = Header(default=None),
) -> Account:
    """Resolve the account from the X-Client-Key header (raises 401/403)."""
    return await authenticate(x_client_key or "", session)


def _display_key_tail(account: Account) -> str:
    """Stored tail when present. The plaintext fallback covers rows seeded before
    0004 and test doubles that only carry `api_key`."""
    tail = getattr(account, "key_tail", None) or ""
    if not tail:
        raw = getattr(account, "api_key", None) or ""
        tail = raw[-4:] if raw else ""
    return f"…{tail}" if tail else ""


@router.get("", response_model=None)
async def get_account(
    account: Account = Depends(current_account),
) -> ORJSONResponse:
    resp = CabinetAccountResponse(
        account_id=str(account.id),
        balance=float(account.balance),
        disabled=account.disabled,
        created_at=account.created_at.isoformat() if account.created_at else None,
        key_tail=_display_key_tail(account),
    )
    return ORJSONResponse(resp.model_dump(by_alias=True, exclude_none=True))


@router.get("/transactions", response_model=None)
async def list_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    account: Account = Depends(current_account),
    session: AsyncSession = Depends(get_db),
) -> ORJSONResponse:
    rows = await TransactionRepo(session).list_for_account(account.id, limit=limit, offset=offset)
    items = [
        TransactionOut(
            id=row.id,
            kind=row.kind,
            amount=f"{row.amount:.6f}",
            balance_after=f"{row.balance_after:.6f}",
            task_id=str(row.task_id) if row.task_id else None,
            external_id=row.external_id,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]
    resp = TransactionListResponse(items=items, limit=limit, offset=offset)
    return ORJSONResponse(resp.model_dump(by_alias=True, exclude_none=True))


@router.post("/topup", response_model=None)
async def create_topup(
    body: TopupRequest,
    account: Account = Depends(current_account),
    session: AsyncSession = Depends(get_db),
) -> ORJSONResponse:
    try:
        provider = get_provider(body.provider)
    except ValueError as exc:
        return ORJSONResponse(classify_bad(str(exc)), status_code=status.HTTP_200_OK)

    amount = Decimal(body.amount)
    intent = await PaymentIntentRepo(session).create(
        account_id=account.id,
        amount=amount,
        provider=provider.name,
    )
    instructions = await provider.start(
        intent_id=str(intent.id),
        account_id=str(account.id),
        amount=amount,
        return_url=body.return_url,
    )
    _log.info("cabinet.topup.created", intent_id=str(intent.id), amount=str(amount), provider=provider.name)
    resp = TopupResponse(
        intent_id=instructions.intent_id,
        provider=instructions.provider,
        amount=instructions.amount,
        status=instructions.status,
        payment_url=instructions.payment_url,
        instructions=instructions.instructions,
    )
    return ORJSONResponse(resp.model_dump(by_alias=True, exclude_none=True))


@router.post("/topup/{intent_id}/confirm", response_model=None, dependencies=[Depends(require_admin)])
async def confirm_topup(
    intent_id: str,
    session: AsyncSession = Depends(get_db),
) -> ORJSONResponse:
    """Credit a pending intent. Idempotent: a replay reports `credited: false`."""
    try:
        parsed = uuid.UUID(intent_id)
    except (ValueError, TypeError):
        return ORJSONResponse(envelope(ERR_TASK_NOT_FOUND), status_code=status.HTTP_200_OK)

    repo = PaymentIntentRepo(session)
    intent = await repo.claim(parsed)
    if intent is None:
        existing = await repo.get(parsed)
        if existing is None:
            return ORJSONResponse(envelope(ERR_TASK_NOT_FOUND), status_code=status.HTTP_200_OK)
        account = await AccountRepo(session).get(existing.account_id)
        resp = TopupConfirmResponse(
            intent_id=str(existing.id),
            credited=False,
            balance=float(account.balance) if account else 0.0,
            status=existing.status,
        )
        return ORJSONResponse(resp.model_dump(by_alias=True, exclude_none=True))

    balance = await AccountRepo(session).topup(
        intent.account_id,
        Decimal(intent.amount),
        external_id=f"intent:{intent.id}",
        meta={"provider": intent.provider, "intent_id": str(intent.id)},
    )
    _log.info("cabinet.topup.credited", intent_id=str(intent.id), amount=str(intent.amount))
    resp = TopupConfirmResponse(
        intent_id=str(intent.id),
        credited=True,
        balance=float(balance),
        status="paid",
    )
    return ORJSONResponse(resp.model_dump(by_alias=True, exclude_none=True))


@router.get("/stats", response_model=None)
async def usage_stats(
    days: int = Query(default=7, ge=1, le=_MAX_DAYS),
    account: Account = Depends(current_account),
    session: AsyncSession = Depends(get_db),
) -> ORJSONResponse:
    since = datetime.now(UTC) - timedelta(days=days)
    repo = UsageRepo(session)
    type_rows = [TypeRow(type=r[0], status=r[1], count=int(r[2]), avg_latency_ms=r[3], spend=r[4]) for r in await repo.by_type(account.id, since)]
    day_rows = [DayRow(day=r[0], count=int(r[1]), spend=r[2]) for r in await repo.by_day(account.id, since)]
    return ORJSONResponse(summarize(type_rows, day_rows, window_days=days))


def classify_bad(message: str) -> dict[str, Any]:
    eid, code, desc = ERR_BAD_REQUEST
    return {"errorId": eid, "errorCode": code, "errorDescription": message or desc}
