"""Shared browser-lane plumbing for the interactive vendor solvers.

MTCaptcha and the legacy VK image flow are HTTP; VK ID, Yandex SmartCaptcha,
Prosopo Procaptcha and CaptchaFox mint their token inside their own JS, so a
solver that claims an HTTP success for them would be lying. Those solvers use
`ctx.browser` exactly like the hCaptcha/Cloudflare lanes: one fresh session per
task through the provider, no browser or HTTP client created here, and the UA
taken from the page that actually minted the token.

This module is the vendor-neutral half: URL handling, the per-task budget, the
provider session, and the small JS-wait/click helpers that all four browser
vendors share. Vendor-specific widget driving stays in each package.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from captcha.domain import Task
from captcha.domain.errors import CaptchaError, ConfigError, UnsolvableError
from captcha.domain.ports import BrowserSpec

#: Whole-task budget for one page to answer.
DEFAULT_TIMEOUT_S = 90.0
#: Never hand the page less than this, even with a nearly-spent deadline.
MIN_TIMEOUT_S = 10.0
MAX_URL_LEN = 2048
#: Token poll interval; the page's own JS does the work between polls.
POLL_S = 0.5

#: Payload aliases for "the page the client wants solved". VK uses redirectUri,
#: 2captcha's other vendors use websiteURL/pageurl.
_URL_KEYS = (
    "websiteURL",
    "websiteUrl",
    "pageurl",
    "page_url",
    "site_url",
    "siteUrl",
    "redirectUri",
    "redirect_uri",
    "url",
)


def payload_of(task: Task) -> dict[str, Any]:
    return task.payload if isinstance(task.payload, dict) else {}


def website_url(task: Task, *extra_keys: str) -> str:
    """The page to open, from the task or its payload aliases."""
    payload = payload_of(task)
    for key in (*extra_keys, *_URL_KEYS):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(task.site_url, str) and task.site_url.strip():
        return task.site_url.strip()
    raise UnsolvableError(
        "payload missing websiteURL/redirectUri: the browser lane needs a page to render the widget",
        context={"type": task.type.value},
    )


def clean_url(url: str) -> str:
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if len(url) > MAX_URL_LEN or scheme not in ("http", "https"):
        raise UnsolvableError(
            "websiteURL must be a short http(s) URL",
            context={"scheme": scheme, "length": len(url)},
        )
    return url


def host_of(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url if "//" in url else "//" + url)
    return (parsed.hostname or "").lower()


def timeout_s(task: Task, ctx: Any) -> float:
    """Task budget: `deadline_ts`, unless the worker pins `ctx.extra["timeout_s"]`."""
    extra = getattr(ctx, "extra", None)
    override = extra.get("timeout_s") if isinstance(extra, dict) else None
    if isinstance(override, int | float) and override > 0:
        # Explicit worker/test pin: honored as-is, including sub-second values.
        return float(override)
    budget = DEFAULT_TIMEOUT_S
    if task.deadline_ts:
        remaining = task.deadline_ts - time.time()
        if 0 < remaining < budget:
            budget = remaining
    return max(budget, MIN_TIMEOUT_S)


def spec_for(task: Task, ctx: Any, *, label: str) -> BrowserSpec:
    """Per-task spec; UA/locale/timezone stay with the provider on purpose.

    Camoufox derives them from the proxy exit IP. Forcing a client-supplied UA
    here would produce a fingerprint that disagrees with itself.
    """
    config = getattr(ctx, "config", None)
    return BrowserSpec(
        proxy=task.proxy,
        cookies=parse_cookies(payload_of(task).get("cookies")),
        headless=bool(getattr(config, "worker_browser_headless", True)),
        label=label,
    )


def parse_cookies(raw: Any) -> list[dict[str, str]]:
    """Normalize the task cookie field into provider cookie dicts."""
    if isinstance(raw, str):
        out: list[dict[str, str]] = []
        for part in raw.split(";"):
            name, sep, value = part.partition("=")
            if sep and name.strip():
                out.append({"name": name.strip(), "value": value.strip()})
        return out
    if isinstance(raw, list | tuple):
        out = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("name") or item.get("value") is None:
                continue
            cookie = {"name": str(item["name"]), "value": str(item["value"])}
            for key in ("domain", "path"):
                if item.get(key):
                    cookie[key] = str(item[key])
            out.append(cookie)
        return out
    return []


async def maybe_await(value: Any) -> Any:
    """playwright returns awaitables; duck-typed fakes may return plain values."""
    if hasattr(value, "__await__"):
        return await value
    return value


@asynccontextmanager
async def open_session(ctx: Any, spec: BrowserSpec, *, solver: str) -> AsyncIterator[Any]:
    """Open one per-task session through the provider, never a shared context."""
    provider = getattr(ctx, "browser", None)
    if provider is None:
        raise ConfigError(
            "SolverContext.browser is None: the browser lane needs a provider",
            context={"solver": solver},
        )
    opener = getattr(provider, "session", None)
    if opener is not None:
        async with opener(spec) as session:
            yield session
        return
    acquire = getattr(provider, "acquire", None)
    if acquire is None:
        raise ConfigError(
            "browser provider exposes neither session() nor acquire()",
            context={"provider": type(provider).__name__},
        )
    async with acquire() as session:
        yield session


async def page_of(session: Any) -> Any:
    getter = getattr(session, "page", None) or getattr(session, "new_page", None)
    if getter is None:
        raise ConfigError("browser session exposes no page()", context={"session": type(session).__name__})
    return await maybe_await(getter())


async def navigate(session: Any, page: Any, url: str, timeout_ms: int) -> None:
    """Use the provider's goto when it has one; it maps failures to CaptchaError."""
    goto = getattr(session, "goto", None)
    if goto is not None:
        await maybe_await(goto(url, wait_until="domcontentloaded", timeout_ms=timeout_ms))
        return
    try:
        await maybe_await(page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms))
    except CaptchaError:
        raise
    except Exception as exc:
        raise CaptchaError("page navigation failed", context={"url": url, "error": str(exc)[:300]}) from exc


async def evaluate(page: Any, script: str, arg: Any = None) -> Any:
    try:
        return await maybe_await(page.evaluate(script, arg))
    except Exception as exc:
        raise CaptchaError(
            "page script failed",
            context={"error": str(exc)[:300]},
        ) from exc


async def install_init_script(session: Any, script: str) -> bool:
    """Install a script before any page JS runs; False when the session cannot."""
    context = getattr(session, "context", None)
    add = getattr(context, "add_init_script", None)
    if add is None:
        return False
    try:
        await maybe_await(add(script))
    except Exception:
        return False
    return True


async def wait_js(page: Any, script: str, *, timeout_ms: int, poll_ms: int = int(POLL_S * 1000)) -> Any:
    """Poll `script` until it returns something truthy; a timeout returns None.

    The callers own the error mapping: "page never answered" is not the same
    failure class for every vendor.
    """
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
    while True:
        value = await evaluate(page, script)
        if value:
            return value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(poll_ms / 1000.0, remaining))


async def click_first(page: Any, selectors: list[str]) -> str | None:
    """Click the first matching element; returns the selector that worked."""
    for selector in selectors:
        click = getattr(page, "click", None)
        if click is not None:
            try:
                await maybe_await(click(selector, timeout=1500))
                return selector
            except Exception:
                continue
    return None


async def read_ua(page: Any) -> str | None:
    value = await evaluate(page, "() => navigator.userAgent || ''")
    return str(value).strip() if isinstance(value, str) and value.strip() else None
