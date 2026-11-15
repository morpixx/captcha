"""Browser port: the shape a solver may rely on.

`docs/production-contract.md` §4 fixes the interface: a provider hands out one
fresh session per task through an async context manager. The implementation
lives in `captcha.adapters.browser`; a solver only ever sees `ctx.browser`, so
this module is a Protocol and nothing else. `domain/` stays stdlib-only.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class BrowserSpec:
    """What a task needs from a browser. Everything else is the provider's call."""

    proxy: str | None = None
    cookies: list[dict[str, Any]] = field(default_factory=list)
    user_agent: str | None = None
    locale: str = "en-US"
    timezone: str = "America/New_York"
    headless: bool = True
    label: str | None = None


class BrowserSession(Protocol):
    """One live browser, owned by exactly one task, closed by the provider."""

    spec: BrowserSpec
    job_id: str

    @property
    def context(self) -> Any:
        """Underlying persistent browser context. Adapter-specific, opaque to solvers."""
        ...

    async def page(self) -> Any:
        """Primary page, created on first use."""
        ...

    async def goto(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 60_000,
    ) -> Any:
        """Navigate the primary page and return it. Raises a CaptchaError on failure."""
        ...

    async def close(self) -> None:
        """Close pages and the context. Idempotent."""
        ...


class BrowserProvider(Protocol):
    """One provider per worker. Sessions never outlive the task that opened them."""

    def session(self, spec: BrowserSpec) -> AbstractAsyncContextManager[BrowserSession]:
        """Yield a fresh session; cleanup runs on exit.

        Implement with :func:`contextlib.asynccontextmanager`.
        """
        ...


__all__ = ["BrowserProvider", "BrowserSession", "BrowserSpec"]
