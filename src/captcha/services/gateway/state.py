"""Shared app state. One AppState lives on `app.state.ctx` for the lifetime
of the process; routes pull clients off it via a dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from captcha.config import Settings
    from captcha.adapters.queue import Producer, TaskStateStore
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


@dataclass
class AppState:
    settings: "Settings"
    redis: "Redis"
    engine: "AsyncEngine"
    sessionmaker: "async_sessionmaker[AsyncSession]"
    state_store: "TaskStateStore"
    producer: "Producer"
