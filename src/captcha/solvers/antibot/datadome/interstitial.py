"""DataDome Device Check / interstitial helpers.

Two ways to obtain the ``datadome`` cookie without a live browser of our own:

* **js-runtime** — run the captured challenge script in ``ctx.js_runtime`` and
  read the cookie/headers it hands back (the interstitial script does the
  server round-trip itself).
* **browser lane** — drive the real page through ``ctx.browser`` and read the
  cookie out of the session jar after the challenge settles.

Nothing here creates a runtime, browser or HTTP client: those arrive through
``SolverContext`` (``docs/production-contract.md`` §3).
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from captcha.domain.errors import CaptchaError, ChallengeExpiredError, ConfigError, JSRuntimeError

#: Name of the cookie every answer of this family is built around.
DATADOME_COOKIE: str = "datadome"

#: How long the browser lane waits for the challenge to set its cookie.
DEFAULT_COOKIE_TIMEOUT_S: float = 15.0
_POLL_INTERVAL_S: float = 0.5


@dataclass(frozen=True, slots=True)
class ChallengeOutput:
    """What a device-check script (or the browser jar) produced."""

    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    user_agent: str | None = None

    @property
    def cookie(self) -> str:
        return self.cookies.get(DATADOME_COOKIE, "")


async def maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def cookies_to_dict(raw: Any) -> dict[str, str]:
    """Normalize every cookie shape a provider may hand back."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    if isinstance(raw, str):
        return cookie_pairs(raw)
    out: dict[str, str] = {}
    for item in raw:
        if isinstance(item, dict):
            name = item.get("name")
            value = item.get("value")
            if name and value is not None:
                out[str(name)] = str(value)
        elif isinstance(item, str):
            out.update(cookie_pairs(item))
    return out


def cookie_pairs(raw: str | None) -> dict[str, str]:
    """``a=b; c=d`` -> dict; ignores flags without a value."""
    out: dict[str, str] = {}
    if not raw:
        return out
    for pair in str(raw).split(";"):
        name, sep, value = pair.strip().partition("=")
        if sep and name.strip():
            out[name.strip()] = value.strip()
    return out


def cookie_spec(raw: Any) -> list[dict[str, Any]]:
    """Cookie jar as the ``BrowserSpec.cookies`` list-of-dicts shape."""
    return [{"name": name, "value": value} for name, value in cookies_to_dict(raw).items()]


def normalise(raw: Any) -> ChallengeOutput:
    """Turn a script/browser answer into cookies+headers or a typed failure.

    Accepted shapes: a bare cookie value, ``{"datadome": ...}``, or
    ``{"cookies": ..., "headers": ..., "userAgent": ...}`` where ``cookies`` is a
    map/string/list. A run that produced no ``datadome`` cookie is a stale
    challenge, not a solver bug.
    """
    if raw is None:
        raise ChallengeExpiredError("datadome challenge returned nothing", context={"engine": "datadome"})
    if isinstance(raw, str):
        raw = {"cookies": {DATADOME_COOKIE: raw}}
    if not isinstance(raw, dict):
        raise ChallengeExpiredError(
            "datadome challenge returned an unexpected type",
            context={"got": type(raw).__name__},
        )

    cookies = cookies_to_dict(raw.get("cookies"))
    if isinstance(raw.get("datadome"), str):
        cookies.setdefault(DATADOME_COOKIE, str(raw["datadome"]))
    headers = {str(k): str(v) for k, v in (raw.get("headers") or {}).items() if v is not None}
    user_agent = raw.get("userAgent") or raw.get("user_agent")
    if not cookies.get(DATADOME_COOKIE):
        raise ChallengeExpiredError(
            "challenge script produced no datadome cookie",
            context={"keys": sorted(raw)},
        )
    return ChallengeOutput(
        cookies=cookies,
        headers=headers,
        user_agent=str(user_agent) if user_agent else None,
    )


async def run_challenge_js(
    ctx: Any,
    *,
    script: str,
    globals_: dict[str, Any] | None = None,
    timeout_ms: int = 15_000,
) -> ChallengeOutput:
    """Execute the captured interstitial script in the shared JS runtime."""
    runtime = getattr(ctx, "js_runtime", None)
    if runtime is None:
        raise ConfigError(
            "datadome interstitial needs ctx.js_runtime; escalate to the browser lane",
            context={"engine": "datadome"},
        )
    if not isinstance(script, str) or not script.strip():
        raise ChallengeExpiredError("empty datadome challenge script", context={"engine": "datadome"})
    try:
        raw = await runtime.exec_script(script, globals_ or {}, timeout_ms=timeout_ms)
    except CaptchaError:
        raise
    except Exception as exc:  # runtime boundary: classify once, here
        raise JSRuntimeError(
            "datadome challenge script failed",
            context={"error": str(exc)[:300], "class": type(exc).__name__},
        ) from exc
    return normalise(raw)


@asynccontextmanager
async def open_browser(ctx: Any, spec: Any) -> AsyncIterator[Any]:
    """One fresh provider session; the solver never owns the browser."""
    provider = getattr(ctx, "browser", None)
    if provider is None:
        raise ConfigError(
            "datadome browser lane needs ctx.browser",
            context={"engine": "datadome"},
        )
    session_cm = provider.session(spec)
    session_cm = await maybe_await(session_cm)
    async with session_cm as session:
        yield session


async def open_page(session: Any) -> Any:
    page_fn = getattr(session, "page", None)
    if page_fn is None:
        return None
    try:
        return await maybe_await(page_fn())
    except Exception:  # provider without a usable page is not fatal here
        return None


async def navigate(session: Any, url: str, *, timeout_ms: int = 60_000) -> None:
    """Load ``url`` through the provider's goto, falling back to the raw page."""
    goto = getattr(session, "goto", None)
    try:
        if goto is not None:
            await maybe_await(goto(url, wait_until="domcontentloaded", timeout_ms=timeout_ms))
            return
        page = await open_page(session)
        if page is None:
            raise ConfigError("browser session exposes neither goto() nor page()", context={"url": url})
        await maybe_await(page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms))
    except CaptchaError:
        raise
    except Exception as exc:
        raise CaptchaError(
            "datadome page navigation failed",
            context={"url": url, "error": str(exc)[:300], "class": type(exc).__name__},
        ) from exc


async def session_cookies(session: Any, url: str | None = None) -> dict[str, str]:
    """Read the cookie jar from a session or its underlying context."""
    candidates = [getattr(session, "cookies", None)]
    context = getattr(session, "context", None)
    candidates.append(getattr(context, "cookies", None))
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
    name: str = DATADOME_COOKIE,
    url: str | None = None,
    timeout_s: float = DEFAULT_COOKIE_TIMEOUT_S,
    interval_s: float = _POLL_INTERVAL_S,
) -> dict[str, str]:
    """Poll the jar until the challenge sets ``name`` or the budget runs out."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    delay = max(0.0, interval_s)
    while True:
        jar = await session_cookies(session, url)
        if jar.get(name):
            return jar
        if time.monotonic() >= deadline:
            return jar
        await asyncio.sleep(delay)
