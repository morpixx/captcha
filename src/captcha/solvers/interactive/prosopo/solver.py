"""Prosopo Procaptcha — browser lane; HTTP escalates with the reason.

2026 protocol (Prosopo's own source, `packages/provider/src/api/captcha`): the
widget requests an image challenge with `user` (a self-custodied SR25519
account), `dapp` (the sitekey) and `sessionId`, then submits a signed
commitment. The signature covers the timestamp and the provider request hash,
so a solver with no user key cannot produce the body at all. That is a
browser-lane fact, not an HTTP gap: the page's own widget builds the account,
solves the frictionless PoW, and writes `procaptcha-response`.

The browser lane waits for that value (light DOM, shadow DOM, or the global
`procaptcha` API) and returns it with the UA that minted it. An interactive
puzzle/image band that never resolves is a retryable `ChallengeExpiredError`;
a page that leaves the expected origin is terminal.
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
  const find = (root) => {
    if (!root || !root.querySelector) return null;
    const direct = root.querySelector(
      'input[name="procaptcha-response"], textarea[name="procaptcha-response"], [name="procaptcha-response"]');
    if (direct && direct.value) return direct.value;
    const nodes = root.querySelectorAll('*');
    for (const node of nodes) {
      if (node.shadowRoot) {
        const nested = find(node.shadowRoot);
        if (nested) return nested;
      }
    }
    return null;
  };
  const fromDom = find(document);
  if (fromDom) return fromDom;
  if (typeof window.procaptchaResponse === 'string' && window.procaptchaResponse) return window.procaptchaResponse;
  if (window.procaptcha && typeof window.procaptcha.getResponse === 'function') {
    try { const value = window.procaptcha.getResponse(); if (value) return value; } catch (e) {}
    try { const value = window.procaptcha.getResponse(0); if (value) return value; } catch (e) {}
  }
  return '';
}
"""


@register_solver
class ProsopoSolver(InteractiveBase):
    info = SolverInfo(
        name="prosopo",
        types=(CaptchaType.PROSOPO,),
        http=True,
        browser=True,
        experimental=True,
        version="0.2.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        sitekey = _sitekey(task)
        url = clean_url(_site_url(task))
        if getattr(ctx, "browser", None) is not None:
            # An HTTP-lane worker has no provider; one that owns both lanes can
            # finish the task in-process instead of bouncing it through Redis.
            return await self.solve_browser(task, ctx)
        raise NeedsBrowserError(
            "prosopo: the commitment is signed by the widget's SR25519 user; there is no bare-HTTP token",
            context={"sitekey": sitekey, "url": url},
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        sitekey = _sitekey(task)
        url = clean_url(_site_url(task))
        expected_host = host_of(url)
        budget_ms = int(timeout_s(task, ctx) * 1000)
        label = f"prosopo:{task.type.value}:{task.id}"
        async with open_session(ctx, spec_for(task, ctx, label=label), solver=self.info.name) as session:
            page = await page_of(session)
            await navigate(session, page, url, budget_ms)
            token = await wait_js(page, TOKEN_JS, timeout_ms=max(budget_ms // 2, 1))
            if not token:
                await click_first(
                    page,
                    [
                        '[data-testid="procaptcha-checkbox"]',
                        ".procaptcha-checkbox",
                        "#procaptcha",
                        ".procaptcha",
                    ],
                )
                token = await wait_js(page, TOKEN_JS, timeout_ms=max(budget_ms // 2 - 500, 1))
            ua = await read_ua(page)
            host = await _host_of_page(page)
        if host and expected_host and host != expected_host:
            raise UnsolvableError(
                "prosopo: the page left the expected origin",
                context={"expected_host": expected_host, "actual_host": host, "url": url},
            )
        if not token:
            raise ChallengeExpiredError(
                "prosopo issued no procaptcha-response",
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
    for value in (
        task.sitekey,
        payload.get("sitekey"),
        payload.get("websiteKey"),
        payload.get("siteKey"),
        payload.get("dapp"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise UnsolvableError("prosopo payload missing sitekey/websiteKey", context={"type": task.type.value})


def _site_url(task: Task) -> str:
    try:
        return website_url(task)
    except UnsolvableError as exc:
        raise UnsolvableError("prosopo payload missing websiteURL", context={"type": task.type.value}) from exc
