"""clientKey → Account lookup. 2captcha exposes the key in the request body,
so there's no header-based flow — routes extract the key themselves and call
`authenticate()`.
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from captcha_db import Account, AccountRepo


async def authenticate(client_key: str, session: AsyncSession) -> Account:
    """Return the Account for a client key or raise HTTP 401.

    Disabled accounts are rejected with 403 — the key exists but isn't usable.
    """
    if not client_key or len(client_key) < 8:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"errorId": 1, "errorCode": "ERROR_KEY_DOES_NOT_EXIST",
                    "errorDescription": "Account authorization key not found"},
        )
    repo = AccountRepo(session)
    account = await repo.get_by_key(client_key)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"errorId": 1, "errorCode": "ERROR_KEY_DOES_NOT_EXIST",
                    "errorDescription": "Account authorization key not found"},
        )
    if account.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"errorId": 10, "errorCode": "ERROR_ACCOUNT_SUSPENDED",
                    "errorDescription": "Account is suspended"},
        )
    return account
