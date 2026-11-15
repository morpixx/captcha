"""Yandex SmartCaptcha — browser lane; `solve_http` escalates.

The 2026 widget is a set of iframe documents (`checkbox.*.html`,
`advanced.*.html`, `backend.*.html`) that exchange `postMessage` RPC and write
the result into `input[name="smart-token"]`. The token is produced by the
backend document after the risk/PoW check; nothing in it is derivable from an
HTTP request (the loader URL with the sitekey only gets you the shell). So the
HTTP lane raises `NeedsBrowserError`, and the browser lane opens the client's
page, nudges the checkbox/execute hook, and reads the widget's own token.

An advanced image band that never resolves is a retryable
`ChallengeExpiredError`: it may be a bad IP/profile and a fresh session can get
the invisible band. A page that leaves the sitekey's origin is terminal.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ChallengeExpiredError, NeedsBrowserError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from .._browser import (
    clean_url,
    click_first,
    evaluate,
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
  const input = document.querySelector('input[name="smart-token"]');
  if (input && input.value) return input.value;
  if (window.smartCaptcha && typeof window.smartCaptcha.getResponse === 'function') {
    try { const value = window.smartCaptcha.getResponse(0); if (value) return value; } catch (e) {}
    try { return window.smartCaptcha.getResponse() || ''; } catch (e) {}
  }
  return '';
}
"""

#: The checkbox lives in an iframe; clicking the frame's centre hits the button.
CHECKBOX_SELECTORS = [
    "#js-captcha-checkbox",
    '[data-testid="captcha-checkbox"]',
    ".SmartCaptcha-Checkbox",
    'iframe[data-testid="checkbox-iframe"]',
    ".smart-captcha iframe",
]


@register_solver
class YandexSolver(InteractiveBase):
    info = SolverInfo(
        name="yandex",
        types=(CaptchaType.YANDEX,),
        http=True,
        browser=True,
        experimental=True,
        version="0.2.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        sitekey = _sitekey(task)
        url = clean_url(_site_url(task))
        if getattr(ctx, "browser", None) is not None:
            return await self.solve_browser(task, ctx)
        raise NeedsBrowserError(
            "yandex: the smart-token is minted by the widget's iframe/RPC and backend document",
            context={"sitekey": sitekey, "url": url},
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        sitekey = _sitekey(task)
        url = clean_url(_site_url(task))
        expected_host = host_of(url)
        budget_ms = int(timeout_s(task, ctx) * 1000)
        label = f"yandex:{task.type.value}:{task.id}"
        async with open_session(ctx, spec_for(task, ctx, label=label), solver=self.info.name) as session:
            page = await page_of(session)
            await navigate(session, page, url, budget_ms)
            token = await wait_js(page, TOKEN_JS, timeout_ms=max(budget_ms // 2, 1))
            if not token:
                # Invisible widgets ignore the click and answer `execute`; a
                # visible one needs the checkbox gesture. Both are cheap and
                # wrong guesses are ignored by the widget.
                await evaluate(
                    page,
                    "() => { try { window.smartCaptcha && window.smartCaptcha.execute(0); } catch (e) {} }",
                )
                await click_first(page, CHECKBOX_SELECTORS)
                token = await wait_js(page, TOKEN_JS, timeout_ms=max(budget_ms // 2 - 500, 1))
            ua = await read_ua(page)
            host = await _host_of_page(page)
        if host and expected_host and host != expected_host:
            raise UnsolvableError(
                "yandex: the page left the expected origin",
                context={"expected_host": expected_host, "actual_host": host, "url": url},
            )
        if not token:
            raise ChallengeExpiredError(
                "yandex issued no smart-token",
                context={"sitekey": sitekey, "host": host or expected_host, "url": url},
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


def _sitekey(task: Task) -> str:
    payload = task.payload or {}
    for value in (task.sitekey, payload.get("sitekey"), payload.get("websiteKey"), payload.get("siteKey")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise UnsolvableError("yandex payload missing sitekey/websiteKey", context={"type": task.type.value})


def _site_url(task: Task) -> str:
    try:
        return website_url(task)
    except UnsolvableError as exc:
        raise UnsolvableError("yandex payload missing websiteURL", context={"type": task.type.value}) from exc
