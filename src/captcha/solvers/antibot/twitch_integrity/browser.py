"""Browser-lane plumbing for the Twitch Integrity solver.

One fresh provider session per task, a navigation to the player page, and a
page-level fetch that mints the integrity token with the page's own KPSDK
state. The solver never owns a browser (production contract section 3); the
provider arrives through SolverContext.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from captcha.domain.errors import ConfigError
from captcha.engines.twitch.builder import INTEGRITY_URL

#: Mint from inside the loaded page: its own fetch is already KPSDK-patched.
_INTEGRITY_JS = """(async () => {
  const response = await fetch(%(url)s, {
    method: "POST",
    headers: %(headers)s,
    body: null,
    mode: "cors",
    credentials: "omit"
  });
  return await response.text();
})()"""


def integrity_js(headers: dict[str, str]) -> str:
    return _INTEGRITY_JS % {"url": json.dumps(INTEGRITY_URL), "headers": json.dumps(headers)}


async def maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


@asynccontextmanager
async def open_session(provider: Any, spec: Any) -> AsyncIterator[Any]:
    session_cm = provider.session(spec)
    if inspect.isawaitable(session_cm):
        session_cm = await session_cm
    async with session_cm as session:
        yield session


async def navigate(session: Any, url: str, *, timeout_ms: int = 60_000) -> None:
    goto = getattr(session, "goto", None)
    if callable(goto):
        await maybe_await(goto(url, wait_until="domcontentloaded", timeout_ms=timeout_ms))
        return
    page_fn = getattr(session, "page", None)
    if not callable(page_fn):
        raise ConfigError("browser session exposes neither goto() nor page()", context={"url": url})
    page = await maybe_await(page_fn())
    await maybe_await(page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms))


async def evaluate(session: Any, script: str) -> Any:
    evaluate_fn = getattr(session, "evaluate", None)
    if callable(evaluate_fn):
        return await maybe_await(evaluate_fn(script))
    page_fn = getattr(session, "page", None)
    if not callable(page_fn):
        raise ConfigError("browser session cannot evaluate scripts", context={})
    page = await maybe_await(page_fn())
    return await maybe_await(page.evaluate(script))
