"""Browser Session: thin async wrapper around a playwright BrowserContext.

Holds exactly one context + one page (captcha solves rarely need more). Fingerprint and
behavior packages are imported lazily so this module loads even if they aren't on the
PYTHONPATH yet — the worker binds them before first use.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from captcha_common.errors import CaptchaError, ConfigError, TimeoutError as CaptchaTimeout

if TYPE_CHECKING:  # pragma: no cover - types only
    from captcha_behavior import Humanizer  # type: ignore[import-not-found]
    from captcha_fingerprint import Profile  # type: ignore[import-not-found]


@dataclass
class SessionConfig:
    """Per-session knobs. One `SessionConfig` per acquired slot."""

    proxy: str | None = None
    user_agent: str | None = None
    locale: str = "en-US"
    timezone: str = "America/New_York"
    viewport: tuple[int, int] = (1280, 800)
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Scripts injected via add_init_script before any page JS runs — plug in here from
    # captcha_fingerprint's stealth bundle and from per-type patches.
    init_scripts: list[str] = field(default_factory=list)
    # How many pages this context will serve before the pool destroys it.
    max_uses: int = 20
    # Navigation default; per-call overrides allowed.
    nav_timeout_ms: int = 30_000


class Session:
    """One live BrowserContext. Not safe to share across tasks concurrently."""

    def __init__(
        self,
        context: Any,
        config: SessionConfig,
        *,
        humanizer: Humanizer | None = None,
        logger: Any = None,
    ) -> None:
        self._context = context
        self._config = config
        self._humanizer = humanizer
        self._log = logger
        self._uses = 0
        self._page: Any = None
        self._closed = False

    @property
    def uses(self) -> int:
        return self._uses

    @property
    def exhausted(self) -> bool:
        return self._uses >= self._config.max_uses

    @property
    def context(self) -> Any:
        return self._context

    async def page(self) -> Any:
        """Lazily open the primary page."""
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
            self._page.set_default_navigation_timeout(self._config.nav_timeout_ms)
            self._page.set_default_timeout(self._config.nav_timeout_ms)
        return self._page

    async def apply_fingerprint(self, profile: Profile) -> None:
        """Apply a resolved fingerprint profile (navigator, screen, canvas, fonts, WebGL).

        `profile` must come from `captcha_fingerprint.Store`; we duck-type to keep the
        import light. A profile exposes `.init_scripts()` returning a list[str] and
        `.http_headers()` returning a dict[str, str].
        """
        headers_fn = getattr(profile, "http_headers", None)
        scripts_fn = getattr(profile, "init_scripts", None)
        if headers_fn is None or scripts_fn is None:
            raise ConfigError(
                "profile missing required interface",
                context={"profile_type": type(profile).__name__},
            )
        headers = headers_fn()
        if headers:
            await self._context.set_extra_http_headers(headers)
        for src in scripts_fn():
            await self._context.add_init_script(src)

    async def inject_scripts(self, scripts: list[str]) -> None:
        """Push a list of init scripts into the context."""
        for s in scripts:
            await self._context.add_init_script(s)

    async def goto_with_stealth(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        timeout_ms: int | None = None,
    ) -> None:
        """Navigate with a small pre-navigation jitter so request timing doesn't look
        machine-cadenced. Real humans don't open a tab and hit the URL within 50ms.
        """
        page = await self.page()
        await asyncio.sleep(random.uniform(0.2, 0.9))
        try:
            await page.goto(
                url,
                wait_until=wait_until,
                timeout=timeout_ms or self._config.nav_timeout_ms,
            )
        except Exception as e:  # playwright Error class — avoid hard import
            if "timeout" in str(e).lower():
                raise CaptchaTimeout(
                    "page navigation timed out",
                    context={"url": url},
                ) from e
            raise CaptchaError(
                "page navigation failed",
                context={"url": url, "error": str(e)},
            ) from e

    async def humanize_click(self, selector: str, *, timeout_ms: int | None = None) -> None:
        """Move + click with dwell. Uses captcha_behavior.Humanizer if wired; otherwise
        a minimal jitter fallback — enough for internal tests, not stealth-grade.
        """
        page = await self.page()
        locator = page.locator(selector)
        await locator.wait_for(state="visible", timeout=timeout_ms or self._config.nav_timeout_ms)
        box = await locator.bounding_box()
        if box is None:
            raise CaptchaError("element not clickable", context={"selector": selector})
        target_x = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
        target_y = box["y"] + box["height"] / 2 + random.uniform(-2, 2)
        if self._humanizer is not None:
            await self._humanizer.move_mouse(page, target_x, target_y)
        else:
            await page.mouse.move(target_x, target_y, steps=random.randint(10, 20))
            await asyncio.sleep(random.uniform(0.04, 0.12))
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.03, 0.09))
        await page.mouse.up()

    async def humanize_fill(self, selector: str, text: str, *, timeout_ms: int | None = None) -> None:
        """Click into field, type with per-char delay. Humanizer's typing cadence beats
        this; use it when the worker wires one in.
        """
        page = await self.page()
        locator = page.locator(selector)
        await locator.wait_for(state="visible", timeout=timeout_ms or self._config.nav_timeout_ms)
        await self.humanize_click(selector, timeout_ms=timeout_ms)
        if self._humanizer is not None:
            await self._humanizer.type_text(page, text)
            return
        for ch in text:
            await page.keyboard.insert_text(ch)
            await asyncio.sleep(random.uniform(0.04, 0.14))

    def mark_used(self) -> None:
        """Pool increments usage on release."""
        self._uses += 1

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._page is not None and not self._page.is_closed():
                await self._page.close()
        except Exception:
            pass
        try:
            await self._context.close()
        except Exception:
            pass
