"""CaptchaFox browser lane: render the page's widget, read its token.

The widget keeps its challenge state private, so this lane does no CV: it
waits for `cf-captcha-response` (or `captchafox.getResponse()`), which the
attest/one-click band fills without interaction. The interactive slide/select
band is not exposed to the page, so an empty token is an abstention
(`ChallengeExpiredError`), never a guessed answer.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ChallengeExpiredError, UnsolvableError
from captcha.solvers.base import SolverContext

from .._browser import (
    clean_url,
    click_first,
    host_of,
    navigate,
    open_session,
    page_of,
    read_ua,
    spec_for,
    timeout_s,
    wait_js,
    website_url,
)

TOKEN_JS = """
() => {
  const el = document.querySelector('input[name="cf-captcha-response"], textarea[name="cf-captcha-response"]');
  if (el && el.value) return el.value;
  if (window.captchafox && typeof window.captchafox.getResponse === 'function') {
    return window.captchafox.getResponse() || '';
  }
  return '';
}
"""


async def solve_browser(task: Task, ctx: SolverContext, *, solver: str) -> SolverResult:
    started = time.perf_counter()
    url = clean_url(website_url(task))
    expected_host = host_of(url)
    budget_ms = int(timeout_s(task, ctx) * 1000)
    label = f"captchafox:{task.type.value}:{task.id}"
    async with open_session(ctx, spec_for(task, ctx, label=label), solver=solver) as session:
        page = await page_of(session)
        await navigate(session, page, url, budget_ms)
        token = await wait_js(page, TOKEN_JS, timeout_ms=max(budget_ms // 2, 1))
        if not token:
            await click_first(page, [".cf-widget", ".captchafox", "[data-captchafox]"])
            token = await wait_js(page, TOKEN_JS, timeout_ms=max(budget_ms // 2 - 500, 1))
        ua = await read_ua(page)
        host = await _host_of_page(page)
    if host and expected_host and host != expected_host:
        raise UnsolvableError(
            "captchafox: the page left the expected origin",
            context={"expected_host": expected_host, "actual_host": host, "url": url},
        )
    if not token:
        raise ChallengeExpiredError(
            "captchafox issued no token",
            context={"host": host or expected_host, "url": url},
        )
    return SolverResult(
        token=str(token),
        extra={"userAgent": ua} if ua else {},
        solve_mode=SolveMode.BROWSER,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


async def _host_of_page(page: Any) -> str:
    try:
        value = await page.evaluate("() => location.hostname || ''")
    except Exception:
        return ""
    return str(value).strip().lower() if isinstance(value, str) else ""
