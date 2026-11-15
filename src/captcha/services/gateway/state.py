"""Shared app state. One AppState lives on `app.state.ctx` for the lifetime
of the process; routes pull clients off it via a dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from captcha.domain.enums import CaptchaType

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from captcha.adapters.queue import Producer, TaskStateStore
    from captcha.config import Settings


@dataclass
class AppState:
    settings: Settings
    redis: Redis
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    state_store: TaskStateStore
    producer: Producer
    #: Types a solver is actually registered for in this process. A type the
    #: deployment cannot solve is refused at the door instead of being charged
    #: for and left to time out.
    solver_types: frozenset[CaptchaType] = frozenset()
