"""FastAPI dependency wiring. Everything that needs app state goes through here.

One sessionmaker is built in `build_state()`; `get_db()` just checks it out a
session per request. The session commits on clean return and rolls back on any
raised exception — the common route-level pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.services.gateway.state import AppState


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.ctx
    return state


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    state: AppState = request.app.state.ctx
    session = state.sessionmaker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
