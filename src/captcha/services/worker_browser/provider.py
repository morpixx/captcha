"""Browser provider resolution for the browser lane (contract §4).

`ctx.browser` is a `BrowserProvider`: `async with provider.session(spec) as session`.
One fresh session per task, no shared context between clients. The worker never
launches a browser itself; the composition root (`cli`) resolves the provider
once per process and hands the same instance to every consumer.

Resolution order:

1. an explicit provider (tests, embedders);
2. `captcha.adapters.browser.provider.build_provider(...)` — browser-core's
   per-task Camoufox implementation, when shipped;
3. `PoolProvider` — local fallback over `captcha.adapters.browser.Pool` with
   `max_uses=1`: a fresh context per task on one shared browser process. Keep
   only until (2) exists; it does not give a per-task browser profile.
"""

from __future__ import annotations

import importlib
import importlib.util
from contextlib import asynccontextmanager
from typing import Any

import structlog

log = structlog.get_logger(__name__)

DEFAULT_SESSIONS = 12
_MAX_SESSIONS = 32


def _external_builder() -> Any:
    try:
        if importlib.util.find_spec("captcha.adapters.browser.provider") is None:
            return None
    except (ImportError, ValueError):
        return None
    module = importlib.import_module("captcha.adapters.browser.provider")
    return getattr(module, "build_provider", None)


def resolve_provider(settings: Any, *, headless: bool = True, provider: Any = None) -> Any:
    """Return the provider this process should use. Never launches a browser."""
    if provider is not None:
        return provider
    builder = _external_builder()
    if builder is not None:
        try:
            return builder(settings=settings, headless=headless)
        except TypeError:
            return builder(settings)
    sessions = int(getattr(settings, "worker_browser_sessions", DEFAULT_SESSIONS) or DEFAULT_SESSIONS)
    sessions = max(1, min(sessions, _MAX_SESSIONS))
    from captcha.adapters.browser import Pool

    log.warning(
        "browser.provider.fallback",
        backend="pool",
        size=sessions,
        hint="adapters/browser/provider.py not available; using per-task contexts on a shared process",
    )
    return PoolProvider(Pool(size=sessions, headless=headless), headless=headless)


async def start_provider(provider: Any) -> None:
    for name in ("start", "launch", "open"):
        fn = getattr(provider, name, None)
        if callable(fn):
            await fn()
            return


async def close_provider(provider: Any) -> None:
    for name in ("aclose", "close", "shutdown", "close_all"):
        fn = getattr(provider, name, None)
        if callable(fn):
            await fn()
            return


def _coerce_spec(spec: Any, *, headless: bool) -> dict[str, Any]:
    """Read a provider spec duck-typed: whatever contract class the caller owns."""

    def get(name: str, default: Any) -> Any:
        if spec is None:
            return default
        if isinstance(spec, dict):
            return spec.get(name, default)
        return getattr(spec, name, default)

    cookies = get("cookies", None) or []
    return {
        "proxy": get("proxy", None),
        "cookies": [c for c in cookies if isinstance(c, dict)],
        "user_agent": get("user_agent", None),
        "locale": get("locale", "en-US") or "en-US",
        "timezone": get("timezone", "America/New_York") or "America/New_York",
        "headless": bool(get("headless", headless)),
        "label": str(get("label", "") or ""),
    }


class PoolProvider:
    """Transitional provider: fresh context per task on a shared browser process.

    `max_uses=1` guarantees no context is ever reused across tasks (no cookies,
    no storage state, no canvas paint bleeding into the next client). It is not
    the contract's per-task Camoufox profile — see the module docstring.
    """

    def __init__(self, pool: Any, *, headless: bool = True, nav_timeout_ms: int = 60_000) -> None:
        self._pool = pool
        self._headless = headless
        self._nav_timeout_ms = nav_timeout_ms

    @property
    def size(self) -> int:
        return int(getattr(self._pool, "size", 0))

    async def start(self) -> None:
        await self._pool.launch()

    async def aclose(self) -> None:
        await self._pool.close_all()

    @asynccontextmanager
    async def session(self, spec: Any = None):  # type: ignore[no-untyped-def]
        from captcha.adapters.browser.session import SessionConfig

        parsed = _coerce_spec(spec, headless=self._headless)
        cfg = SessionConfig(
            proxy=parsed["proxy"],
            user_agent=parsed["user_agent"],
            locale=parsed["locale"],
            timezone=parsed["timezone"],
            max_uses=1,
            nav_timeout_ms=self._nav_timeout_ms,
        )
        async with self._pool.acquire(cfg) as session:
            if parsed["cookies"]:
                context = getattr(session, "context", None)
                if context is not None:
                    await context.add_cookies(parsed["cookies"])
            yield session
