"""Runtime-side lane plumbing shared by the antibot engine solvers.

Two capabilities a solver does not own but may need:

* **JS runtime** -- `ctx.js_runtime` runs a captured engine script
  (`captcha.adapters.jssandbox`). Script-driven engines report their result as a
  capture blob (:mod:`captcha.engines.capture`); this module only executes and
  classifies failures.
* **Browser lane** -- `ctx.browser` hands out one fresh session per task
  (`domain/ports.py`). Same four moves for every engine: open, navigate, read the
  jar until the engine sets its cookie, close.

Nothing here creates a runtime, a browser or an HTTP client: those arrive through
`SolverContext` (docs/production-contract.md §3). Missing capability is
`NeedsBrowserError`, not `ConfigError`: a worker without a JS runtime is a task the
browser lane can still serve, and `ConfigError` is terminal `ERR_CONFIG` for the client.

Read out of a session is duck-typed on purpose -- the provider is Playwright/Camoufox
today, and a fake in tests only has to implement the two methods it is asked for.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urljoin, urlparse

from captcha.domain.errors import CaptchaError, ConfigError, JSRuntimeError, NeedsBrowserError
from captcha.domain.ports import BrowserSpec

#: How long a browser lane poll waits for the engine to set its cookie.
DEFAULT_COOKIE_TIMEOUT_S = 20.0
_POLL_INTERVAL_S = 0.5
#: Page budget for the initial navigation; the provider caps it too.
DEFAULT_NAV_TIMEOUT_MS = 60_000


async def maybe_await(value: Any) -> Any:
    """Playwright returns awaitables; duck-typed fakes may return plain values."""
    return await value if inspect.isawaitable(value) else value


def cookies_to_dict(raw: Any) -> dict[str, str]:
    """Normalize every cookie shape a provider or payload may hand back."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    if isinstance(raw, str):
        return cookie_pairs(raw)
    out: dict[str, str] = {}
    for item in raw:
        if isinstance(item, dict):
            name, value = item.get("name"), item.get("value")
            if name and value is not None:
                out[str(name)] = str(value)
        elif isinstance(item, str):
            out.update(cookie_pairs(item))
    return out


def cookie_pairs(raw: str | None) -> dict[str, str]:
    """`a=b; c=d` -> dict; attributes and empty values are skipped."""
    out: dict[str, str] = {}
    for part in str(raw or "").split(";"):
        name, sep, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name or not value:
            continue
        if name.lower() in ("path", "domain", "expires", "max-age", "samesite", "secure", "httponly"):
            continue
        out[name] = value
    return out


def cookie_spec(raw: Any) -> list[dict[str, Any]]:
    """Cookie jar in the `BrowserSpec.cookies` list-of-dicts shape."""
    return [{"name": name, "value": value} for name, value in cookies_to_dict(raw).items()]


@asynccontextmanager
async def open_session(ctx: Any, spec: Any, *, solver: str) -> AsyncIterator[Any]:
    """One fresh provider session per task, closed on exit."""
    provider = getattr(ctx, "browser", None)
    if provider is None:
        raise ConfigError(
            "SolverContext.browser is None: the browser lane needs a provider",
            context={"solver": solver},
        )
    opener = getattr(provider, "session", None)
    if opener is None:
        raise ConfigError(
            "browser provider exposes no session()",
            context={"solver": solver, "provider": type(provider).__name__},
        )
    session_cm = await maybe_await(opener(spec))
    async with session_cm as session:
        yield session


async def navigate(session: Any, url: str, *, timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS) -> None:
    """Load `url` through the provider's `goto`; failures become `CaptchaError`."""
    goto = getattr(session, "goto", None)
    if goto is None:
        raise ConfigError("browser session exposes no goto()", context={"url": url})
    try:
        await maybe_await(goto(url, wait_until="domcontentloaded", timeout_ms=timeout_ms))
    except CaptchaError:
        raise
    except Exception as exc:  # provider boundary: classify once, here
        raise CaptchaError(
            "page navigation failed",
            context={"url": url, "error": str(exc)[:300], "class": type(exc).__name__},
        ) from exc


async def page_of(session: Any) -> Any:
    getter = getattr(session, "page", None)
    if getter is None:
        raise ConfigError("browser session exposes no page()", context={"session": type(session).__name__})
    return await maybe_await(getter())


async def evaluate(page: Any, script: str, arg: Any = None) -> Any:
    """Run a page script; a page without `evaluate` is a provider problem."""
    fn = getattr(page, "evaluate", None)
    if fn is None:
        raise ConfigError("browser page exposes no evaluate()", context={"page": type(page).__name__})
    try:
        return await maybe_await(fn(script, arg) if arg is not None else fn(script))
    except CaptchaError:
        raise
    except Exception as exc:
        raise JSRuntimeError(
            "page script failed",
            context={"script_head": script.strip()[:120], "error": str(exc)[:300]},
        ) from exc


async def session_cookies(session: Any, url: str | None = None) -> dict[str, str]:
    """Read the jar from a session or its underlying browser context."""
    candidates = [getattr(session, "cookies", None)]
    candidates.append(getattr(getattr(session, "context", None), "cookies", None))
    calls: tuple[tuple[Any, ...], ...] = ((url,), ()) if url else ((),)
    for getter in candidates:
        if not callable(getter):
            continue
        for args in calls:
            try:
                raw = await maybe_await(getter(*args))
            except TypeError:
                continue
            except CaptchaError:
                raise
            except Exception:
                continue
            jar = cookies_to_dict(raw)
            if jar:
                return jar
    return {}


async def wait_for_cookie(
    session: Any,
    *,
    names: Iterable[str] = (),
    prefixes: Iterable[str] = (),
    url: str | None = None,
    timeout_s: float = DEFAULT_COOKIE_TIMEOUT_S,
    interval_s: float = _POLL_INTERVAL_S,
) -> tuple[str, str]:
    """Poll until one of `names` -- or a cookie starting with a prefix -- appears.

    Returns `(name, value)` or `("", "")` when the budget runs out. Prefixes exist
    because some engines suffix their cookie per customer (`incap_ses_1234`,
    `TS01abcdef`), so the name cannot be a constant.
    """
    wanted = tuple(names)
    wanted_prefixes = tuple(prefixes)
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        jar = await session_cookies(session, url)
        for name in wanted:
            if jar.get(name):
                return name, jar[name]
        for name, value in jar.items():
            if value and any(name.startswith(prefix) for prefix in wanted_prefixes):
                return name, value
        if time.monotonic() >= deadline:
            return "", ""
        await asyncio.sleep(max(0.0, interval_s))


def set_cookie(resp: Any) -> list[str]:
    """All Set-Cookie values of a response; repeated headers must not collapse."""
    getter = getattr(resp, "headers_all", None)
    if callable(getter):
        return list(getter("set-cookie"))
    header = getattr(resp, "header", None)
    value = header("set-cookie") if callable(header) else None
    return [value] if value else []


def replayable(url: str, origin: str, *, vendors: Iterable[str] = ()) -> bool:
    """Whether a captured sensor request may be replayed through our transport.

    The engine script is untrusted input: letting it point our client at an
    arbitrary host turns a task payload into an SSRF primitive. Only the site
    itself (same host or a subdomain) and the vendor's own API hosts qualify.
    """
    target = _hostname(url)
    if not target:
        return False
    site = _hostname(origin)
    if site and (target == site or target.endswith("." + site)):
        return True
    return any(target == vendor or target.endswith("." + vendor) for vendor in vendors)


def absolute(url: str, base: str) -> str:
    """Resolve a captured URL the way the page that emitted it would.

    Scripts emit absolute URLs, root-relative ones (`/_Incapsula_Resource`) and
    page-relative ones (`TSPD/08abc`). `urljoin` gives all three their browser
    meaning; string concatenation does not -- a root-relative path joined onto a
    page URL duplicates the page path (`/login/_Incapsula_Resource`), which the
    origin answers with a 404 instead of a cookie.
    """
    if not url:
        return base
    if not base:
        return url
    return urljoin(base, url)


def _hostname(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url if "//" in url else "//" + url)
    except ValueError:
        return ""
    return (parsed.hostname or "").lower()


def browser_spec(
    task: Any,
    payload: dict[str, Any],
    *,
    label: str,
    headless: bool = True,
) -> BrowserSpec:
    """Per-task spec: proxy from the task, cookies from the payload.

    UA and locale stay with the provider on purpose -- Camoufox derives them from
    the proxy exit IP, and a forced UA that disagrees with the renderer is exactly
    what these engines score.
    """
    return BrowserSpec(
        proxy=getattr(task, "proxy", None),
        cookies=cookie_spec(payload.get("cookies")),
        headless=headless,
        label=label,
    )


async def run_script(
    ctx: Any,
    *,
    engine: str,
    script: str,
    globals_: dict[str, Any] | None = None,
    timeout_ms: int = 15_000,
) -> Any:
    """Execute an engine script in `ctx.js_runtime`; missing runtime escalates.

    The runtime is required, not optional: without it the only remaining path is
    a real page, so the caller gets `NeedsBrowserError` and the worker moves the
    task to the browser lane (one hop, `MAX_LANE_HOPS`).
    """
    runtime = getattr(ctx, "js_runtime", None)
    if runtime is None:
        raise NeedsBrowserError(
            f"{engine}: no JS runtime on this worker; the script needs a page",
            context={"engine": engine},
        )
    if not script or not script.strip():
        raise JSRuntimeError(f"{engine}: empty script", context={"engine": engine})
    try:
        return await runtime.exec_script(script, globals_ or {}, timeout_ms=timeout_ms)
    except CaptchaError:
        raise
    except Exception as exc:  # runtime boundary: classify once, here
        raise JSRuntimeError(
            f"{engine}: script failed",
            context={"engine": engine, "error": str(exc)[:300], "class": type(exc).__name__},
        ) from exc


__all__ = [
    "DEFAULT_COOKIE_TIMEOUT_S",
    "DEFAULT_NAV_TIMEOUT_MS",
    "absolute",
    "browser_spec",
    "cookie_pairs",
    "cookie_spec",
    "cookies_to_dict",
    "evaluate",
    "maybe_await",
    "navigate",
    "open_session",
    "page_of",
    "replayable",
    "run_script",
    "session_cookies",
    "set_cookie",
    "wait_for_cookie",
]
