"""Shared browser-lane plumbing for the Cloudflare solvers.

Both solvers get one fresh session per task through `ctx.browser`, navigate with
the provider's `goto`, and take the UA from the page that actually solved.
Nothing here opens a browser or an HTTP client of its own: solvers only use what
the worker injects (docs/production-contract.md §3/§4).
"""

from __future__ import annotations

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


def website_url(task: Task) -> str:
    """The page the client wants solved, from the task or its payload aliases."""
    payload = task.payload if isinstance(task.payload, dict) else {}
    for value in (
        task.site_url,
        payload.get("websiteURL"),
        payload.get("websiteUrl"),
        payload.get("pageurl"),
        payload.get("page_url"),
        payload.get("site_url"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise UnsolvableError(
        "payload missing websiteURL: the browser lane needs a page to render the widget",
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


def host_matches(actual: str, expected: str) -> bool:
    """A subdomain on either side is the same site for origin purposes."""
    actual, expected = actual.lower().strip("."), expected.lower().strip(".")
    if not actual or not expected:
        return False
    return actual == expected or actual.endswith("." + expected) or expected.endswith("." + actual)


def timeout_s(task: Task, ctx: Any) -> float:
    """Task budget: `deadline_ts`, unless the worker pins `ctx.extra["timeout_s"]`."""
    extra = getattr(ctx, "extra", None)
    override = extra.get("timeout_s") if isinstance(extra, dict) else None
    if isinstance(override, int | float) and override > 0:
        return float(override)
    budget = DEFAULT_TIMEOUT_S
    if task.deadline_ts:
        remaining = task.deadline_ts - time.time()
        if 0 < remaining < budget:
            budget = remaining
    return max(budget, MIN_TIMEOUT_S)


def spec_for(task: Task, ctx: Any, *, label: str) -> BrowserSpec:
    """Per-task spec; UA/locale/timezone stay with the provider on purpose.

    Camoufox derives them from the proxy exit IP, and forcing a client-supplied
    UA produces a fingerprint that disagrees with itself -- exactly what
    Cloudflare scores.
    """
    payload = task.payload if isinstance(task.payload, dict) else {}
    config = getattr(ctx, "config", None)
    return BrowserSpec(
        proxy=task.proxy,
        cookies=parse_cookies(payload.get("cookies")),
        headless=bool(getattr(config, "worker_browser_headless", True)),
        label=label,
    )


def parse_cookies(raw: Any) -> list[dict[str, str]]:
    """Normalize a task's cookies field into cookie dicts for the provider.

    Accepts the string form ("a=b; c=d") and the JSON list form
    ([{name, value, domain?, path?}]). Domain/path stay unset unless the client
    sent them; the provider decides the jar.
    """
    if isinstance(raw, str):
        cookies: list[dict[str, str]] = []
        for part in raw.split(";"):
            name, sep, value = part.partition("=")
            if sep and name.strip():
                cookies.append({"name": name.strip(), "value": value.strip()})
        return cookies
    if isinstance(raw, list | tuple):
        cookies = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("name") or item.get("value") is None:
                continue
            cookie = {"name": str(item["name"]), "value": str(item["value"])}
            for key in ("domain", "path"):
                if item.get(key):
                    cookie[key] = str(item[key])
            cookies.append(cookie)
        return cookies
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
    # Compatibility with the pre-provider baseline pool. It has no per-task
    # spec, so proxy/UA/cookies from the task do not reach it -- the contract
    # provider above is the path that carries them.
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


async def nudge_keys(page: Any) -> bool:
    """Tab+Space is the one interaction the 2026 CF gate accepts from automation.

    CDP mouse clicks inside the managed-challenge Turnstile are rejected
    (docs/reverse-2026.md §4); the keyboard path is what passes.
    """
    keyboard = getattr(page, "keyboard", None)
    if keyboard is None:
        return False
    try:
        await maybe_await(keyboard.press("Tab"))
        await maybe_await(keyboard.press("Space"))
    except Exception:
        return False
    return True
