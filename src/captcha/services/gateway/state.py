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

    from captcha.adapters.netguard import Resolver
    from captcha.adapters.queue import Producer, TaskStateStore
    from captcha.config import Settings
    from captcha.solvers.registry import SolverRegistry


@dataclass
class AppState:
    settings: Settings
    redis: Redis
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    state_store: TaskStateStore
    producer: Producer
    #: Registry built at boot. The capability refresher needs it to recompute
    #: the sellable set without re-importing every solver on each tick.
    solver_registry: SolverRegistry
    #: Types a solver is actually registered for in this process, refreshed from
    #: live heartbeats on a timer. A type the deployment cannot solve is refused
    #: at the door instead of being charged for and left to time out.
    solver_types: frozenset[CaptchaType] = frozenset()
    #: DNS resolver for the client-URL guard. None = the system resolver; tests
    #: inject a stub so the gateway suite never touches DNS.
    url_resolver: Resolver | None = None
