"""clientKey → Account lookup. 2captcha exposes the key in the request body,
so there's no header-based flow — routes extract the key themselves and call
`authenticate()`.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.adapters.storage import Account, AccountRepo

#: A key shorter than this is a client mistake; longer than the storage column
#: cannot exist, so both are rejected before any hashing or DB round trip.
_MIN_KEY_LEN = 8
_MAX_KEY_LEN = 64


async def authenticate(client_key: str, session: AsyncSession) -> Account:
    """Return the Account for a client key or raise HTTP 401.

    The lookup is by `api_key_hash` — the plaintext key is never compared to
    anything stored. Disabled accounts are rejected with 403: the key exists but
    isn't usable.
    """
    if not client_key or not (_MIN_KEY_LEN <= len(client_key) <= _MAX_KEY_LEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "errorId": 1,
                "errorCode": "ERROR_KEY_DOES_NOT_EXIST",
                "errorDescription": "Account authorization key not found",
            },
        )
    repo = AccountRepo(session)
    account = await repo.get_by_key(client_key)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "errorId": 1,
                "errorCode": "ERROR_KEY_DOES_NOT_EXIST",
                "errorDescription": "Account authorization key not found",
            },
        )
    if account.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"errorId": 10, "errorCode": "ERROR_ACCOUNT_SUSPENDED", "errorDescription": "Account is suspended"},
        )
    return account
