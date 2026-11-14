from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from captcha.domain.errors import ConfigError

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str, *, pool_size: int = 20, echo: bool = False) -> AsyncEngine:
    """Create the module-global async engine. Call once at process start."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine
    _engine = create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=pool_size * 2,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=echo,
        future=True,
    )
    _sessionmaker = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise ConfigError("engine not initialised; call init_engine(url) first")
    return _engine


async def shutdown_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def async_session() -> AsyncIterator[AsyncSession]:
    """Transactional scope. Commits on clean exit, rolls back on exception."""
    if _sessionmaker is None:
        raise ConfigError("engine not initialised; call init_engine(url) first")
    session = _sessionmaker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Yields a session that commits on clean request end."""
    if _sessionmaker is None:
        raise ConfigError("engine not initialised; call init_engine(url) first")
    session = _sessionmaker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# Balance alias — the contract exports a `Balance` symbol; it is a view onto an Account.
# The real column lives on Account.balance. Re-export done in __init__.
