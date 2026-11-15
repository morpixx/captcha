"""Browser Pool.

Design:
  - One Browser process is launched at Pool.launch().
  - Pool holds up to `size` idle BrowserContexts. acquire() yields a Session wrapping a
    context; release() returns it to the idle queue unless it's exhausted / broken.
  - Contexts rotate after `SessionConfig.max_uses` to drop any lingering cookies,
    storage state, canvas paint quirks accumulated by the previous target.
  - If `acquire_timeout` elapses while the pool is saturated, raises TimeoutError.

Not thread-safe. Driven by one worker's asyncio loop.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any

from captcha.adapters.browser.launchers import BackendName, Launcher, pick_launcher
from captcha.adapters.browser.proxy import parse_proxy
from captcha.adapters.browser.session import Session, SessionConfig
from captcha.domain.errors import CaptchaError, ConfigError
from captcha.domain.errors import TimeoutError as CaptchaTimeout


@dataclass
class _Slot:
    context: Any
    uses: int = 0


@dataclass
class Pool:
    size: int = 4
    headless: bool = True
    backend: BackendName | None = None
    acquire_timeout_sec: float = 30.0
    default_config: SessionConfig = field(default_factory=SessionConfig)

    _launcher: Launcher | None = field(default=None, init=False, repr=False)
    _pw_ctx: Any = field(default=None, init=False, repr=False)
    _pw: Any = field(default=None, init=False, repr=False)
    _browser: Any = field(default=None, init=False, repr=False)
    _idle: asyncio.Queue[_Slot] = field(default=None, init=False, repr=False)  # type: ignore[assignment]
    _sem: asyncio.Semaphore | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def launch(self) -> None:
        """Pick the backend, start the Browser, warm the slot pool."""
        if self._browser is not None:
            return
        self._launcher = pick_launcher(prefer=self.backend)
        self._pw_ctx = self._launcher.factory()
        self._pw = await self._pw_ctx.__aenter__()

        engine = self._launcher.engine
        launcher_obj = getattr(self._pw, engine, None)
        if launcher_obj is None:
            raise ConfigError(
                "backend missing engine",
                context={"backend": self._launcher.name, "engine": engine},
            )
        try:
            self._browser = await launcher_obj.launch(headless=self.headless)
        except Exception as e:
            raise CaptchaError(
                "browser launch failed",
                context={"backend": self._launcher.name, "error": str(e)},
            ) from e

        self._idle = asyncio.Queue(maxsize=self.size)
        self._sem = asyncio.Semaphore(self.size)

    async def _new_context(self, cfg: SessionConfig) -> Any:
        if self._browser is None:
            raise ConfigError("pool not launched")
        kwargs: dict[str, Any] = {
            "locale": cfg.locale,
            "timezone_id": cfg.timezone,
            "viewport": {"width": cfg.viewport[0], "height": cfg.viewport[1]},
        }
        if cfg.user_agent:
            kwargs["user_agent"] = cfg.user_agent
        if cfg.proxy:
            kwargs["proxy"] = parse_proxy(cfg.proxy)
        if cfg.extra_headers:
            kwargs["extra_http_headers"] = cfg.extra_headers
        try:
            context = await self._browser.new_context(**kwargs)
        except Exception as e:
            raise CaptchaError(
                "context creation failed",
                context={"error": str(e)},
            ) from e
        for s in cfg.init_scripts:
            await context.add_init_script(s)
        return context

    @asynccontextmanager
    async def acquire(self, config: SessionConfig | None = None):  # type: ignore[no-untyped-def]
        """Yield a Session. Use `async with pool.acquire() as sess: ...`."""
        if self._closed:
            raise CaptchaError("pool is closed")
        if self._sem is None:
            raise ConfigError("pool not launched")
        cfg = config or self.default_config

        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.acquire_timeout_sec)
        except TimeoutError as e:
            raise CaptchaTimeout(
                "no browser slot available",
                context={"size": self.size, "timeout_sec": self.acquire_timeout_sec},
            ) from e

        slot: _Slot | None = None
        try:
            # Drain a reusable slot opportunistically.
            try:
                slot = self._idle.get_nowait()
            except asyncio.QueueEmpty:
                slot = None

            if slot is None:
                context = await self._new_context(cfg)
                slot = _Slot(context=context, uses=0)

            session = Session(slot.context, cfg)
            session._uses = slot.uses  # seed used count so exhaustion rolls across acquires
            yield session

            slot.uses = session._uses + 1
            if slot.uses >= cfg.max_uses or self._closed:
                await _safe_close(slot.context)
            else:
                # Return to idle; if the queue is full (shouldn't be — sem bounds it),
                # close instead of blocking.
                try:
                    self._idle.put_nowait(slot)
                except asyncio.QueueFull:
                    await _safe_close(slot.context)
        except CaptchaError:
            if slot is not None:
                await _safe_close(slot.context)
            raise
        except Exception as e:
            if slot is not None:
                await _safe_close(slot.context)
            raise CaptchaError("session failed", context={"error": str(e)}) from e
        finally:
            self._sem.release()

    async def release(self, session: Session) -> None:
        """Explicit release path for callers that don't use the acquire context manager."""
        await session.close()

    async def close_all(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            # Drain idle contexts.
            if self._idle is not None:
                while not self._idle.empty():
                    slot = self._idle.get_nowait()
                    await _safe_close(slot.context)
            if self._browser is not None:
                with suppress(Exception):
                    await self._browser.close()
                self._browser = None
            if self._pw_ctx is not None:
                with suppress(Exception):
                    await self._pw_ctx.__aexit__(None, None, None)
                self._pw_ctx = None


async def _safe_close(context: Any) -> None:
    with suppress(Exception):
        await context.close()
